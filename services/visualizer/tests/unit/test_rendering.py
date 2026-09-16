from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
import pyvista as pv

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneFrame,
)
from scrap_monitoring_visualizer.rendering import (
    DEFAULT_SCENE_COLOR_PALETTE,
    RenderConfig,
    SceneColorPalette,
    describe_scene,
)
from scrap_monitoring_visualizer.rendering.renderer import (
    _apply_camera,
    _height_label_font_size,
    _height_scale,
    _height_tick_levels,
    _inlet_guides,
)
from scrap_monitoring_visualizer.synthetic_camera import SyntheticCameraConfig

CONTRACT_ROOT = Path("../contracts/scene/v1")


@pytest.fixture(scope="module")
def records() -> tuple[SceneDefinition, SceneFrame]:
    parser = ContractParser(CONTRACT_ROOT)
    lines = (
        (CONTRACT_ROOT / "fixtures/scene.v1.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    values = tuple(parser.parse_line(line).value for line in lines)
    assert isinstance(values[0], SceneDefinition)
    assert isinstance(values[1], SceneFrame)
    return values[0], values[1]


@pytest.mark.parametrize(
    "config",
    [
        RenderConfig(width=0),
        RenderConfig(height=0),
        RenderConfig(width=3841),
        RenderConfig(height=2161),
    ],
)
def test_render_config_rejects_invalid_dimensions(config: RenderConfig) -> None:
    with pytest.raises(ValueError):
        config.validate()


def test_camera_uses_parallel_projection() -> None:
    class PlotterStub:
        def __init__(self) -> None:
            self.camera_position: object = None
            self.reset = False
            self.parallel_projection = False

        def reset_camera(self) -> None:
            self.reset = True

        def enable_parallel_projection(self) -> None:
            self.parallel_projection = True

    camera_position = (
        (1.0, -1.0, 1.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    plotter = PlotterStub()

    _apply_camera(cast(pv.Plotter, plotter), camera_position)

    assert plotter.reset
    assert plotter.camera_position == camera_position
    assert plotter.parallel_projection


def test_browser_palette_matches_synthetic_camera_material_colors() -> None:
    camera_config = SyntheticCameraConfig.from_file()

    assert SceneColorPalette.from_camera_config(camera_config) == (
        DEFAULT_SCENE_COLOR_PALETTE
    )
    assert (
        RenderConfig.from_camera_config(
            width=1280,
            height=720,
            camera_config=camera_config,
        ).palette
        == DEFAULT_SCENE_COLOR_PALETTE
    )


def test_height_ticks_use_two_meter_intervals_and_include_bounds() -> None:
    assert _height_tick_levels(0.0, 10.0) == (0.0, 2.0, 4.0, 6.0, 8.0, 10.0)
    assert _height_tick_levels(-0.5, 3.5) == (-0.5, 0.0, 2.0, 3.5)


@pytest.mark.parametrize(
    ("frame_height", "font_size"),
    [(360, 16), (720, 18), (1080, 27), (2160, 28)],
)
def test_height_label_font_size_scales_with_frame(
    frame_height: int, font_size: int
) -> None:
    assert _height_label_font_size(frame_height) == font_size


def test_height_scale_uses_screen_right_boundary_edge(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    camera_position = describe_scene(
        header,
        frame,
        config=RenderConfig(),
    ).camera_position
    scale = _height_scale(header, camera_position)

    assert scale.line_points[0][:2] == scale.line_points[1][:2]
    assert scale.line_points[0][2] == header.scene.floor_z_m
    assert scale.line_points[1][2] == header.scene.top_z_m
    assert scale.labels == ("0 m", "1 m")


def test_scene_description_selects_active_inlet(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    description = describe_scene(
        header,
        frame,
        config=RenderConfig(),
    )

    assert description.active_inlet_index == 0


def test_inlet_guides_connect_top_markers_to_rendered_surface(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    header = replace(
        header,
        scene=replace(
            header.scene,
            inlet_positions_xy_m=((0.25, 0.25), (0.75, 0.75)),
        ),
    )

    guides = _inlet_guides(header, frame, active_inlet_index=0)

    assert len(guides) == 2
    assert guides[0].top == (0.25, 0.25, 1.0)
    assert guides[0].surface == pytest.approx((0.25, 0.25, 0.075))
    assert guides[0].active is True
    assert guides[1].surface == pytest.approx((0.75, 0.75, 0.225))
    assert guides[1].active is False


def test_collecting_scene_has_no_active_inlet(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    collecting = replace(
        frame,
        scenario=replace(frame.scenario, phase="collecting", current_inlet_index=None),
    )
    description = describe_scene(
        header,
        collecting,
        config=RenderConfig(),
    )

    assert description.active_inlet_index is None
