from __future__ import annotations

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
from scrap_monitoring_visualizer.synthetic_camera.renderer import (
    _apply_effects,
    _chute_poly_data,
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


def test_camera_profile_produces_rear_overhead_perspective_placement() -> None:
    config = SyntheticCameraConfig.from_file()
    header = _header()
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
    assert 0.6 < abs(direction_z) / direction_y < 1.4
    assert 40.0 < config.camera.view_angle_deg < 50.0


def test_chute_uses_fixed_upper_mount_and_movable_wide_outlet() -> None:
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
    header = replace(
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
    )

    data = _chute_poly_data(header, frame)
    lower_width = float(np.ptp(data.points[:4, 0]))
    lower_depth = float(np.ptp(data.points[:4, 1]))
    upper_width = float(np.ptp(data.points[4:, 0]))
    upper_depth = float(np.ptp(data.points[4:, 1]))
    minimum_z = float(np.min(data.points[:, 2]))
    maximum_z = float(np.max(data.points[:, 2]))

    assert lower_width == pytest.approx(1.378)
    assert lower_depth == pytest.approx(0.848)
    assert upper_width == pytest.approx(2.12)
    assert upper_depth == pytest.approx(1.484)
    assert np.mean(data.points[:4, :2], axis=0) == pytest.approx((1.5, 1.5))
    assert np.mean(data.points[4:, :2], axis=0) == pytest.approx((2.175, 2.0465))
    assert minimum_z == pytest.approx(10.795)
    assert maximum_z == pytest.approx(12.544)

    moved = _chute_poly_data(
        header,
        replace(
            frame,
            scenario=replace(frame.scenario, current_inlet_index=1),
        ),
    )
    outlet_edge = moved.points[1, :2] - moved.points[0, :2]
    outlet_angle_deg = float(np.degrees(np.arctan2(outlet_edge[1], outlet_edge[0])))

    assert np.mean(moved.points[:4, :2], axis=0) == pytest.approx((2.85, 2.593))
    assert np.mean(moved.points[4:, :2], axis=0) == pytest.approx((2.175, 2.0465))
    assert outlet_angle_deg == pytest.approx(-15.0)


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


def test_explicit_renderer_backend_checks_actual_window() -> None:
    config = SyntheticCameraConfig.from_file()
    _validate_render_window(config, "vtkEGLRenderWindow")
    with pytest.raises(RuntimeError, match="unexpected window"):
        _validate_render_window(config, "vtkXOpenGLRenderWindow")
    with pytest.raises(RuntimeError, match="unexpected window"):
        _validate_render_window(
            replace(config, backend="egl"), "vtkOSOpenGLRenderWindow"
        )
