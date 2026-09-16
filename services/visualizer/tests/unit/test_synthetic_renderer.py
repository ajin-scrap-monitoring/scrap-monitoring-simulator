from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneFrame,
)
from scrap_monitoring_visualizer.synthetic_camera import SyntheticCameraConfig
from scrap_monitoring_visualizer.synthetic_camera.models import InterpolatedFrame
from scrap_monitoring_visualizer.synthetic_camera.renderer import (
    _apply_effects,
    _chute_pivot,
    _chute_poly_data,
    _chute_pose,
    _fixed_conveyor_poly_data,
    _validate_render_window,
    camera_placement,
)

CONTRACT_ROOT = Path("../contracts/scene/v1")


def _header() -> SceneDefinition:
    parser = ContractParser(CONTRACT_ROOT)
    line = (
        (CONTRACT_ROOT / "fixtures/scene.v1.jsonl")
        .read_bytes()
        .splitlines(keepends=True)[0]
    )
    parsed = parser.parse_line(line).value
    assert isinstance(parsed, SceneDefinition)
    return parsed


def _machine_scene() -> tuple[SceneDefinition, SceneFrame]:
    parser = ContractParser(CONTRACT_ROOT)
    lines = (
        (CONTRACT_ROOT / "fixtures/scene.v1.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    header = parser.parse_line(lines[0]).value
    frame = parser.parse_line(lines[1]).value
    assert isinstance(header, SceneDefinition)
    assert isinstance(frame, SceneFrame)
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


def test_fixed_conveyor_trough_and_chute_follow_derived_inlet_geometry() -> None:
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
    assert np.linalg.norm(conveyor_flow) == pytest.approx(2.06)
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
    assert base_terminal[:2] == pytest.approx(pivot + (0.0, -0.06))
    assert rail_terminal[:2] == pytest.approx(pivot + (0.0, 0.06))
    conveyor_faces = conveyor.faces.reshape(-1, 5)
    assert conveyor_faces.shape == (18, 5)
    assert np.all(conveyor_faces[:, 0] == 4)

    rings = tuple(chute.points[index : index + 4] for index in range(0, 28, 4))
    ring_centers = tuple(np.mean(ring, axis=0) for ring in rings)
    assert ring_centers[0][:2] == pytest.approx(pivot + (0.0, 0.12))
    assert ring_centers[1][:2] == pytest.approx(pivot)
    assert ring_centers[2][:2] == pytest.approx(
        pivot + machine.tip_fraction * first_vector
    )
    assert ring_centers[-1][:2] == pytest.approx(first_inlet)
    assert ring_centers[0][2] == pytest.approx(ring_centers[1][2])
    assert ring_centers[1][2] == pytest.approx(ring_centers[2][2])
    assert ring_centers[0][2] - ring_centers[-1][2] == pytest.approx(machine.tip_drop_m)
    tip_heights = np.asarray([center[2] for center in ring_centers[2:]])
    assert np.all(np.diff(tip_heights) <= 0.0)
    assert np.all(np.diff(tip_heights) < 0.0)
    assert np.linalg.norm(rings[0][1] - rings[0][0]) == pytest.approx(1.04)
    assert np.linalg.norm(rings[1][1] - rings[1][0]) == pytest.approx(1.04)
    assert np.linalg.norm(rings[2][1] - rings[2][0]) == pytest.approx(1.0)
    assert np.linalg.norm(rings[-1][1] - rings[-1][0]) == pytest.approx(0.9)
    assert all(
        np.linalg.norm(ring[3] - ring[0]) == pytest.approx(machine.duct_height_m)
        for ring in rings
    )
    socket_axis = ring_centers[1][:2] - ring_centers[0][:2]
    socket_axis /= np.linalg.norm(socket_axis)
    rail_in_socket_m = float(
        np.dot(rail_terminal[:2] - ring_centers[0][:2], socket_axis)
    )
    assert rail_in_socket_m == pytest.approx(0.06)
    assert np.linalg.norm(ring_centers[1][:2] - ring_centers[0][:2]) == pytest.approx(
        0.12
    )
    faces = chute.faces.reshape(-1, 5)
    assert faces.shape == (24, 5)
    assert np.all(faces[:, 0] == 4)
    assert all(len({int(index) // 4 for index in face[1:]}) == 2 for face in faces)

    moved = _chute_poly_data(header, second_frame, machine)
    assert np.mean(moved.points[-4:, :2], axis=0) == pytest.approx(second_inlet)
    assert math.degrees(second_pose.polar_angle_rad) == pytest.approx(
        -12.010686806284566
    )
    distances = np.linalg.norm(
        chute.points[:, np.newaxis, :] - chute.points[np.newaxis, :, :], axis=2
    )
    moved_distances = np.linalg.norm(
        moved.points[:, np.newaxis, :] - moved.points[np.newaxis, :, :], axis=2
    )
    assert moved_distances == pytest.approx(distances)


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


def test_chute_rotation_eases_continuously_and_preserves_local_shape() -> None:
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

    meshes = tuple(
        _chute_poly_data(header, frame, machine, interpolated(alpha))
        for alpha in alphas
    )
    reference_distances = np.linalg.norm(
        meshes[0].points[:, np.newaxis, :] - meshes[0].points[np.newaxis, :, :],
        axis=2,
    )
    for pose, mesh in zip(poses, meshes, strict=True):
        rings = mesh.points.reshape(-1, 4, 3)
        centers = np.mean(rings, axis=1)
        longitudinal = centers[2] - centers[1]
        longitudinal /= np.linalg.norm(longitudinal)
        assert longitudinal == pytest.approx(
            (math.cos(pose.polar_angle_rad), math.sin(pose.polar_angle_rad), 0.0)
        )
        distances = np.linalg.norm(
            mesh.points[:, np.newaxis, :] - mesh.points[np.newaxis, :, :],
            axis=2,
        )
        assert distances == pytest.approx(reference_distances)


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
    _validate_render_window(config, "vtkEGLRenderWindow")
    with pytest.raises(RuntimeError, match="unexpected window"):
        _validate_render_window(config, "vtkXOpenGLRenderWindow")
    with pytest.raises(RuntimeError, match="unexpected window"):
        _validate_render_window(
            replace(config, backend="egl"), "vtkOSOpenGLRenderWindow"
        )
