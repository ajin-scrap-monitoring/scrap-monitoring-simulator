from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import pyvista as pv

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneFrame,
    SceneSegment,
    materialize_keyframe,
)
from scrap_monitoring_visualizer.synthetic_camera import SyntheticCameraConfig
from scrap_monitoring_visualizer.synthetic_camera.models import InterpolatedFrame
from scrap_monitoring_visualizer.synthetic_camera.renderer import (
    _CHUTE_JOINT_SEGMENTS,
    _CHUTE_TIP_SEGMENTS,
    _apply_effects,
    _chute_pivot,
    _chute_poly_data,
    _chute_pose,
    _fixed_conveyor_poly_data,
    _joint_length_m,
    _validate_render_device,
    _validate_render_window,
    camera_placement,
)

CONTRACT_ROOT = Path("../contracts/scene/v2")


def _header() -> SceneDefinition:
    parser = ContractParser(CONTRACT_ROOT)
    line = (
        (CONTRACT_ROOT / "fixtures/scene.v2.jsonl")
        .read_bytes()
        .splitlines(keepends=True)[0]
    )
    parsed = parser.parse_line(line).value
    assert isinstance(parsed, SceneDefinition)
    return parsed


def _machine_scene() -> tuple[SceneDefinition, SceneFrame]:
    parser = ContractParser(CONTRACT_ROOT)
    lines = (
        (CONTRACT_ROOT / "fixtures/scene.v2.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    header = parser.parse_line(lines[0]).value
    segment = parser.parse_line(lines[1]).value
    assert isinstance(header, SceneDefinition)
    assert isinstance(segment, SceneSegment)
    frame = materialize_keyframe(
        header, segment.right, segment.right_sequence, segment.run_id
    )
    return (
        replace(
            header,
            scene=replace(
                header.scene,
                boundary_xy_m=(
                    (0.0, 0.0),
                    (4.0, 0.0),
                    (4.0, 5.3),
                    (2.7, 5.3),
                    (1.9, 2.5),
                    (0.0, 2.5),
                ),
                top_z_m=10.0,
                inlet_positions_xy_m=((1.5, 1.5), (2.85, 2.593)),
            ),
        ),
        frame,
    )


def _rings(data: pv.PolyData) -> np.ndarray:
    points = data.points
    assert isinstance(points, np.ndarray)
    return points.reshape(-1, 8, 3)


def _ring_direction(ring: np.ndarray) -> np.ndarray:
    lateral = ring[1, :2] - ring[0, :2]
    lateral /= np.linalg.norm(lateral)
    return np.asarray((lateral[1], -lateral[0]))


def _fixed_terminal_outline(conveyor: pv.PolyData) -> np.ndarray:
    points = conveyor.points
    assert isinstance(points, np.ndarray)
    base, left_rail, right_rail = points.reshape(3, 8, 3)
    return np.asarray(
        (
            base[0],
            base[1],
            right_rail[5],
            right_rail[4],
            right_rail[0],
            left_rail[1],
            left_rail[5],
            left_rail[4],
        )
    )


def _segments_cross(
    first_start: np.ndarray,
    first_end: np.ndarray,
    second_start: np.ndarray,
    second_end: np.ndarray,
) -> bool:
    def orientation(
        start: np.ndarray,
        end: np.ndarray,
        point: np.ndarray,
    ) -> float:
        segment = end - start
        relative = point - start
        return float(segment[0] * relative[1] - segment[1] * relative[0])

    first_side_a = orientation(first_start, first_end, second_start)
    first_side_b = orientation(first_start, first_end, second_end)
    second_side_a = orientation(second_start, second_end, first_start)
    second_side_b = orientation(second_start, second_end, first_end)
    return (
        first_side_a * first_side_b < -1e-12 and second_side_a * second_side_b < -1e-12
    )


def _assert_transition_does_not_fold(rings: np.ndarray) -> None:
    transition = rings[: _CHUTE_JOINT_SEGMENTS + 2]
    directions = np.asarray([_ring_direction(ring) for ring in transition])
    for point_index in (0, 1, 3, 5):
        path = transition[:, point_index, :2]
        for index, displacement in enumerate(np.diff(path, axis=0)):
            mean_direction = directions[index] + directions[index + 1]
            mean_direction /= np.linalg.norm(mean_direction)
            assert float(np.dot(displacement, mean_direction)) > 0.0
        for first_index in range(len(path) - 1):
            for second_index in range(first_index + 2, len(path) - 1):
                assert not _segments_cross(
                    path[first_index],
                    path[first_index + 1],
                    path[second_index],
                    path[second_index + 1],
                )


def test_camera_profile_produces_rear_overhead_perspective_placement() -> None:
    config = SyntheticCameraConfig.from_file()
    header, _ = _machine_scene()
    placement = camera_placement(header, config)

    minimum_x = min(point[0] for point in header.scene.boundary_xy_m)
    maximum_x = max(point[0] for point in header.scene.boundary_xy_m)
    minimum_y = min(point[1] for point in header.scene.boundary_xy_m)
    maximum_y = max(point[1] for point in header.scene.boundary_xy_m)
    assert placement.position[2] > header.scene.top_z_m
    assert placement.position[1] < (minimum_y + maximum_y) / 2.0
    assert placement.position[0] == pytest.approx((minimum_x + maximum_x) / 2.0)
    assert placement.target[0] == pytest.approx(placement.position[0])
    direction_y = placement.target[1] - placement.position[1]
    direction_z = placement.target[2] - placement.position[2]
    assert direction_y > 0.0
    assert direction_z < 0.0
    assert 40.0 < config.camera.view_angle_deg < 50.0

    forward = np.asarray(placement.target) - np.asarray(placement.position)
    forward /= np.linalg.norm(forward)
    camera_right = np.cross(forward, np.asarray(placement.view_up))
    camera_right /= np.linalg.norm(camera_right)
    camera_up = np.cross(camera_right, forward)

    def vertical_ndc(world_position: tuple[float, float, float]) -> float:
        relative = np.asarray(world_position) - np.asarray(placement.position)
        depth = float(np.dot(relative, forward))
        return float(np.dot(relative, camera_up)) / (
            depth * math.tan(math.radians(config.camera.view_angle_deg) / 2.0)
        )

    center_x = (minimum_x + maximum_x) / 2.0
    near_wall_rim_ndc = vertical_ndc((center_x, minimum_y, header.scene.top_z_m))
    far_wall_rim_ndc = vertical_ndc((center_x, maximum_y, header.scene.top_z_m))
    assert -0.97 < near_wall_rim_ndc < -0.92
    assert 0.92 < far_wall_rim_ndc < 0.97
    assert far_wall_rim_ndc == pytest.approx(-near_wall_rim_ndc, abs=0.001)
    near_wall_rim_y = (1.0 - near_wall_rim_ndc) * config.video.height / 2.0
    far_wall_rim_y = (1.0 - far_wall_rim_ndc) * config.video.height / 2.0
    assert near_wall_rim_y == pytest.approx(1050.84, abs=0.1)
    assert far_wall_rim_y == pytest.approx(29.16, abs=0.1)


def test_fixed_conveyor_and_straight_chute_form_one_open_trough() -> None:
    header, frame = _machine_scene()
    machine = SyntheticCameraConfig.from_file().machine
    pivot = np.asarray(_chute_pivot(header, machine))
    first_inlet = np.asarray(header.scene.inlet_positions_xy_m[0])
    second_inlet = np.asarray(header.scene.inlet_positions_xy_m[1])
    first_frame = replace(
        frame,
        scenario=replace(frame.scenario, current_inlet_index=0),
    )
    second_frame = replace(
        frame,
        scenario=replace(frame.scenario, current_inlet_index=1),
    )
    conveyor = _fixed_conveyor_poly_data(header, machine)
    chute = _chute_poly_data(header, first_frame, machine)
    first_pose = _chute_pose(header, first_frame, machine)
    second_pose = _chute_pose(header, second_frame, machine)
    joint_length_m = _joint_length_m(header, machine)

    assert pivot == pytest.approx((1.5, 2.880214547118024))
    first_vector = first_inlet - pivot
    second_vector = second_inlet - pivot
    first_radius = np.linalg.norm(first_vector)
    second_radius = np.linalg.norm(second_vector)
    sweep_deg = math.degrees(
        math.atan2(
            first_vector[0] * second_vector[1] - first_vector[1] * second_vector[0],
            float(np.dot(first_vector, second_vector)),
        )
    )
    assert first_radius == pytest.approx(second_radius)
    assert first_radius == pytest.approx(1.3802145471180238)
    assert first_pose.outlet_xy_m == pytest.approx(first_inlet)
    assert math.degrees(first_pose.polar_angle_rad) == pytest.approx(-90.0)
    assert second_pose.outlet_xy_m == pytest.approx(second_inlet)
    assert sweep_deg == pytest.approx(77.98931319371543)

    base, left_rail, right_rail = conveyor.points.reshape(3, 8, 3)
    base_terminal = np.mean(base[[0, 1, 4, 5]], axis=0)
    base_upstream = np.mean(base[[2, 3, 6, 7]], axis=0)
    rail_terminal = np.mean(
        np.concatenate(
            (
                left_rail[[0, 1, 4, 5]],
                right_rail[[0, 1, 4, 5]],
            )
        ),
        axis=0,
    )
    conveyor_flow = base_terminal[:2] - base_upstream[:2]
    assert np.ptp(base[:, 0]) == pytest.approx(machine.conveyor_width_m)
    assert np.linalg.norm(conveyor_flow) == pytest.approx(
        machine.conveyor_length_m - joint_length_m / 2.0
    )
    assert conveyor_flow / np.linalg.norm(conveyor_flow) == pytest.approx((0.0, -1.0))
    assert first_vector / first_radius == pytest.approx((0.0, -1.0))
    assert np.ptp(conveyor.points[:, 2]) == pytest.approx(
        machine.conveyor_body_height_m
    )
    assert np.ptp(base[:, 2]) == pytest.approx(machine.conveyor_body_height_m / 2.0)
    assert np.ptp(left_rail[:, 2]) == pytest.approx(
        machine.conveyor_body_height_m / 2.0
    )
    assert np.ptp(right_rail[:, 2]) == pytest.approx(
        machine.conveyor_body_height_m / 2.0
    )
    assert np.max(base[:, 2]) == pytest.approx(np.min(left_rail[:, 2]))
    assert np.max(base[:, 2]) == pytest.approx(np.min(right_rail[:, 2]))
    assert np.ptp(left_rail[:, 0]) == pytest.approx(0.06)
    assert np.ptp(right_rail[:, 0]) == pytest.approx(0.06)
    assert np.min(right_rail[:, 0]) - np.max(left_rail[:, 0]) == pytest.approx(0.88)
    terminal_center = pivot + (0.0, joint_length_m / 2.0)
    assert base_terminal[:2] == pytest.approx(terminal_center)
    assert rail_terminal[:2] == pytest.approx(terminal_center)
    conveyor_faces = conveyor.faces.reshape(-1, 5)
    assert conveyor_faces.shape == (15, 5)
    assert np.all(conveyor_faces[:, 0] == 4)

    fixed_terminal_faces = (
        {0, 1, 4, 5},
        {8, 9, 12, 13},
        {16, 17, 20, 21},
    )
    assert all(
        set(int(index) for index in face[1:]) not in fixed_terminal_faces
        for face in conveyor_faces
    )
    assert all(
        len({int(index) // 8 for index in face[1:]}) == 1 for face in conveyor_faces
    )

    rings = _rings(chute)
    ring_centers = np.mean(rings, axis=1)
    deck_centers = np.mean(rings[:, [4, 5]], axis=1)
    assert rings.shape == (_CHUTE_JOINT_SEGMENTS + _CHUTE_TIP_SEGMENTS + 2, 8, 3)
    assert rings[0] == pytest.approx(_fixed_terminal_outline(conveyor))
    assert ring_centers[0, :2] == pytest.approx(terminal_center)
    assert ring_centers[_CHUTE_JOINT_SEGMENTS, :2] == pytest.approx(
        pivot + (0.0, -joint_length_m / 2.0)
    )
    assert ring_centers[_CHUTE_JOINT_SEGMENTS + 1, :2] == pytest.approx(
        pivot + machine.tip_fraction * first_vector
    )
    assert ring_centers[-1, :2] == pytest.approx(first_inlet)
    level_decks = deck_centers[: _CHUTE_JOINT_SEGMENTS + 2, 2]
    assert level_decks == pytest.approx(np.full_like(level_decks, level_decks[0]))
    assert deck_centers[0, 2] - deck_centers[-1, 2] == pytest.approx(machine.tip_drop_m)
    tip_heights = deck_centers[_CHUTE_JOINT_SEGMENTS + 1 :, 2]
    assert np.all(np.diff(tip_heights) < 0.0)
    widths = np.linalg.norm(rings[:, 1] - rings[:, 0], axis=1)
    assert widths[: _CHUTE_JOINT_SEGMENTS + 2] == pytest.approx(
        np.full(_CHUTE_JOINT_SEGMENTS + 2, machine.conveyor_width_m)
    )
    assert widths[-1] == pytest.approx(machine.outlet_width_m)
    assert all(
        ring[2][2] - ring[1][2] == pytest.approx(machine.duct_height_m)
        for ring in rings
    )
    assert all(
        ring[4][2] - ring[0][2] == pytest.approx(machine.conveyor_body_height_m / 2.0)
        for ring in rings
    )
    assert all(
        np.linalg.norm(ring[3] - ring[2]) == pytest.approx(0.06)
        and np.linalg.norm(ring[7] - ring[6]) == pytest.approx(0.06)
        for ring in rings
    )
    assert np.linalg.norm(rings[0][5] - rings[0][4]) == pytest.approx(0.88)
    assert np.linalg.norm(rings[7][5] - rings[7][4]) == pytest.approx(0.88)
    assert np.linalg.norm(rings[-1][5] - rings[-1][4]) == pytest.approx(0.78)
    assert rings[0][0][2] == pytest.approx(np.min(base[:, 2]))
    assert rings[0][4][2] == pytest.approx(np.max(base[:, 2]))
    assert rings[0][2][2] == pytest.approx(np.max(left_rail[:, 2]))
    assert all(_ring_direction(ring) == pytest.approx((0.0, -1.0)) for ring in rings)
    faces = chute.faces.reshape(-1, 5)
    assert faces.shape == ((len(rings) - 1) * 8, 5)
    assert np.all(faces[:, 0] == 4)
    assert all(len({int(index) // 8 for index in face[1:]}) == 2 for face in faces)
    cross_section_edges = {
        tuple(sorted((int(face[1]) % 8, int(face[2]) % 8))) for face in faces
    }
    assert cross_section_edges == {
        (0, 1),
        (0, 7),
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
        (5, 6),
        (6, 7),
    }
    assert (3, 6) not in cross_section_edges
    _assert_transition_does_not_fold(rings)

    moved = _chute_poly_data(header, second_frame, machine)
    assert np.mean(moved.points[-8:, :2], axis=0) == pytest.approx(second_inlet)
    assert math.degrees(second_pose.polar_angle_rad) == pytest.approx(
        -12.010686806284566
    )


def test_chute_outlet_follows_deterministic_polar_transition() -> None:
    header, frame = _machine_scene()
    machine = SyntheticCameraConfig.from_file().machine
    interpolation = InterpolatedFrame(
        frame=frame,
        left_sequence=1,
        right_sequence=2,
        alpha=0.5,
        mode="interpolated",
        left_inlet_index=0,
        right_inlet_index=1,
    )

    pose = _chute_pose(header, frame, machine, interpolation)
    pivot = np.asarray(pose.pivot_xy_m)
    outlet = np.asarray(pose.outlet_xy_m)
    endpoint_radius = np.linalg.norm(np.asarray((1.5, 1.5)) - pivot)

    assert np.linalg.norm(outlet - pivot) == pytest.approx(endpoint_radius)
    assert outlet != pytest.approx(pivot)
    assert pose.outlet_xy_m == pytest.approx((2.3684971214690353, 1.807505385193781))
    assert math.degrees(pose.polar_angle_rad) == pytest.approx(-51.005343403142284)


def test_chute_rotation_eases_continuously_and_preserves_joint_contract() -> None:
    header, frame = _machine_scene()
    machine = SyntheticCameraConfig.from_file().machine

    def interpolated(
        alpha: float, source: int = 0, target: int = 1
    ) -> InterpolatedFrame:
        return InterpolatedFrame(
            frame=frame,
            left_sequence=1,
            right_sequence=2,
            alpha=alpha,
            mode="interpolated",
            left_inlet_index=source,
            right_inlet_index=target,
        )

    alphas = (0.0, 0.25, 0.5, 0.75, 1.0)
    poses = tuple(
        _chute_pose(header, frame, machine, interpolated(alpha)) for alpha in alphas
    )
    angles = np.asarray([pose.polar_angle_rad for pose in poses])
    angular_steps = np.diff(angles)
    assert np.all(angular_steps > 0.0)
    assert angular_steps[0] == pytest.approx(angular_steps[-1])
    assert angular_steps[1] == pytest.approx(angular_steps[2])
    assert angular_steps[0] < angular_steps[1]

    reverse_poses = tuple(
        _chute_pose(header, frame, machine, interpolated(1.0 - alpha, 1, 0))
        for alpha in alphas
    )
    assert [pose.polar_angle_rad for pose in reverse_poses] == pytest.approx(angles)

    conveyor = _fixed_conveyor_poly_data(header, machine)
    terminal_outline = _fixed_terminal_outline(conveyor)
    joint_length_m = _joint_length_m(header, machine)
    tip_start_index = _CHUTE_JOINT_SEGMENTS + 1
    for alpha in (0.0, 0.5, 1.0):
        interpolation = interpolated(alpha)
        pose = _chute_pose(header, frame, machine, interpolation)
        mesh = _chute_poly_data(header, frame, machine, interpolation)
        rings = _rings(mesh)
        centers = np.mean(rings, axis=1)
        directions = np.asarray([_ring_direction(ring) for ring in rings])
        chute_direction = np.asarray(
            (math.cos(pose.polar_angle_rad), math.sin(pose.polar_angle_rad))
        )
        pivot = np.asarray(pose.pivot_xy_m)

        assert rings[0] == pytest.approx(terminal_outline)
        assert directions[0] == pytest.approx((0.0, -1.0))
        assert directions[_CHUTE_JOINT_SEGMENTS] == pytest.approx(chute_direction)
        assert directions[_CHUTE_JOINT_SEGMENTS:] == pytest.approx(
            np.tile(chute_direction, (len(rings) - _CHUTE_JOINT_SEGMENTS, 1))
        )
        assert centers[0, :2] == pytest.approx(pivot + (0.0, joint_length_m / 2.0))
        assert centers[_CHUTE_JOINT_SEGMENTS, :2] == pytest.approx(
            pivot + chute_direction * joint_length_m / 2.0
        )
        straight_join = (
            centers[tip_start_index, :2] - centers[_CHUTE_JOINT_SEGMENTS, :2]
        )
        straight_join /= np.linalg.norm(straight_join)
        assert straight_join == pytest.approx(chute_direction)
        assert centers[-1, :2] == pytest.approx(pose.outlet_xy_m)

        deck_heights = np.mean(rings[:, [4, 5], 2], axis=1)
        assert deck_heights[: tip_start_index + 1] == pytest.approx(
            np.full(tip_start_index + 1, deck_heights[0])
        )
        assert np.all(np.diff(deck_heights[tip_start_index:]) < 0.0)
        assert deck_heights[0] - deck_heights[-1] == pytest.approx(machine.tip_drop_m)
        widths = np.linalg.norm(rings[:, 1] - rings[:, 0], axis=1)
        assert widths[: tip_start_index + 1] == pytest.approx(
            np.full(tip_start_index + 1, machine.conveyor_width_m)
        )
        assert widths[-1] == pytest.approx(machine.outlet_width_m)

        faces = mesh.faces.reshape(-1, 5)
        assert all(
            max(int(index) // 8 for index in face[1:])
            - min(int(index) // 8 for index in face[1:])
            == 1
            for face in faces
        )
        cross_section_edges = {
            tuple(sorted((int(face[1]) % 8, int(face[2]) % 8))) for face in faces
        }
        assert (3, 6) not in cross_section_edges
        _assert_transition_does_not_fold(rings)


def test_chute_reaches_nondefault_inlets_with_continuous_radius() -> None:
    header, frame = _machine_scene()
    machine = SyntheticCameraConfig.from_file().machine
    third_inlet = (3.4, 4.1)
    header = replace(
        header,
        scene=replace(
            header.scene,
            inlet_positions_xy_m=(*header.scene.inlet_positions_xy_m, third_inlet),
        ),
    )

    def interpolated(alpha: float) -> InterpolatedFrame:
        return InterpolatedFrame(
            frame=frame,
            left_sequence=1,
            right_sequence=2,
            alpha=alpha,
            mode="interpolated",
            left_inlet_index=0,
            right_inlet_index=2,
        )

    start = _chute_pose(header, frame, machine, interpolated(0.0))
    middle = _chute_pose(header, frame, machine, interpolated(0.5))
    end = _chute_pose(header, frame, machine, interpolated(1.0))
    pivot = np.asarray(start.pivot_xy_m)
    start_radius = np.linalg.norm(np.asarray(start.outlet_xy_m) - pivot)
    middle_radius = np.linalg.norm(np.asarray(middle.outlet_xy_m) - pivot)
    end_radius = np.linalg.norm(np.asarray(end.outlet_xy_m) - pivot)

    assert start.outlet_xy_m == pytest.approx(header.scene.inlet_positions_xy_m[0])
    assert end.outlet_xy_m == pytest.approx(third_inlet)
    assert middle_radius == pytest.approx((start_radius + end_radius) / 2.0)


def test_chute_uses_upstream_fallback_for_equal_inlet_y() -> None:
    header, frame = _machine_scene()
    machine = SyntheticCameraConfig.from_file().machine
    inlets = ((1.5, 1.5), (2.85, 1.5))
    header = replace(
        header,
        scene=replace(header.scene, inlet_positions_xy_m=inlets),
    )
    pivot = _chute_pivot(header, machine)

    assert pivot == pytest.approx((1.5, 6.3))
    for inlet_index, inlet in enumerate(inlets):
        pose = _chute_pose(
            header,
            replace(
                frame,
                scenario=replace(frame.scenario, current_inlet_index=inlet_index),
            ),
            machine,
        )
        assert pose.outlet_xy_m == pytest.approx(inlet)


def test_camera_effects_are_seeded_and_bounded() -> None:
    source = np.full((20, 30, 3), 128, dtype=np.uint8)

    first = _apply_effects(
        source,
        seed=42,
        noise_standard_deviation=0.02,
        vignette_strength=0.2,
    )
    second = _apply_effects(
        source,
        seed=42,
        noise_standard_deviation=0.02,
        vignette_strength=0.2,
    )

    assert np.array_equal(first, second)
    assert first.dtype == np.uint8
    assert not np.array_equal(first, source)


def test_disabled_camera_effects_return_source_without_copy() -> None:
    source = np.full((20, 30, 3), 128, dtype=np.uint8)

    result = _apply_effects(
        source,
        seed=42,
        noise_standard_deviation=0.0,
        vignette_strength=0.0,
    )

    assert result is source


def test_explicit_renderer_backend_checks_actual_window() -> None:
    config = SyntheticCameraConfig.from_file()
    auto_config = replace(config, backend="auto")
    _validate_render_window(auto_config, "vtkEGLRenderWindow")
    _validate_render_window(auto_config, "vtkOSOpenGLRenderWindow")
    with pytest.raises(RuntimeError, match="unexpected window"):
        _validate_render_window(auto_config, "vtkXOpenGLRenderWindow")
    with pytest.raises(RuntimeError, match="unexpected window"):
        _validate_render_window(replace(config, backend="osmesa"), "vtkEGLRenderWindow")
    with pytest.raises(RuntimeError, match="unexpected window"):
        _validate_render_window(
            replace(config, backend="egl"), "vtkOSOpenGLRenderWindow"
        )


def test_explicit_egl_backend_requires_nvidia_opengl_device() -> None:
    config = replace(SyntheticCameraConfig.from_file(), backend="egl")
    capabilities = "\n".join(
        (
            "OpenGL vendor string: NVIDIA Corporation",
            "OpenGL renderer string: NVIDIA RTX 4000 Ada Generation",
            "OpenGL version string: 4.6.0 NVIDIA 580.82.07",
        )
    )

    _validate_render_device(
        config,
        supports_opengl=True,
        capabilities=capabilities,
    )


@pytest.mark.parametrize(
    ("supports_opengl", "capabilities", "message"),
    (
        (
            False,
            "OpenGL vendor string: NVIDIA Corporation\n"
            "OpenGL renderer string: NVIDIA RTX 4000 Ada Generation",
            "does not support OpenGL",
        ),
        (
            True,
            "OpenGL vendor string: Mesa/X.org\n"
            "OpenGL renderer string: NVIDIA RTX 4000 Ada Generation",
            "requires an NVIDIA OpenGL vendor and renderer",
        ),
        (
            True,
            "OpenGL vendor string: NVIDIA Corporation\n"
            "OpenGL renderer string: llvmpipe (LLVM 15.0.6, 256 bits)",
            "requires an NVIDIA OpenGL vendor and renderer",
        ),
        (
            True,
            "OpenGL vendor string: NVIDIA Corporation",
            "did not report OpenGL renderer string",
        ),
    ),
)
def test_explicit_egl_backend_rejects_missing_or_software_device(
    supports_opengl: bool,
    capabilities: str,
    message: str,
) -> None:
    config = replace(SyntheticCameraConfig.from_file(), backend="egl")

    with pytest.raises(RuntimeError, match=message):
        _validate_render_device(
            config,
            supports_opengl=supports_opengl,
            capabilities=capabilities,
        )


def test_cpu_backend_does_not_require_nvidia_capabilities() -> None:
    config = replace(SyntheticCameraConfig.from_file(), backend="osmesa")

    _validate_render_device(
        config,
        supports_opengl=True,
        capabilities="OpenGL vendor string: Mesa/X.org",
    )
