"""Render validated frames as deterministic engineering scenes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO

import numpy as np
import pyvista as pv
from PIL import Image

from scrap_monitoring_visualizer.contracts.models import (
    SceneDefinition,
    SceneFrame,
)
from scrap_monitoring_visualizer.geometry import (
    Mesh,
    SceneGeometry,
    surface_height_at,
)
from scrap_monitoring_visualizer.limits import (
    DEFAULT_FRAME_HEIGHT,
    DEFAULT_FRAME_WIDTH,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
)
from scrap_monitoring_visualizer.synthetic_camera.models import SyntheticCameraConfig

SUPPORTED_RENDER_WINDOWS = frozenset(
    {
        "vtkEGLRenderWindow",
        "vtkOSOpenGLRenderWindow",
    }
)
MESH_EDGE_COLOR = "#454545"
OVERLAY_COLOR = "#111827"
HEIGHT_TICK_INTERVAL_M = 2.0
ACTIVE_INLET_COLOR = "#C62828"
INACTIVE_INLET_COLOR = "#2E7D32"


@dataclass(frozen=True, slots=True)
class SceneColorPalette:
    background: tuple[float, float, float]
    floor: tuple[float, float, float]
    wall: tuple[float, float, float]
    scrap: tuple[float, float, float]

    @classmethod
    def from_camera_config(cls, config: SyntheticCameraConfig) -> SceneColorPalette:
        return cls(
            background=config.background_color,
            floor=config.floor_material.color,
            wall=config.wall_material.color,
            scrap=config.scrap_material.color,
        )

    def validate(self) -> None:
        if any(
            not math.isfinite(component) or component < 0.0 or component > 1.0
            for color in (self.background, self.floor, self.wall, self.scrap)
            for component in color
        ):
            raise ValueError(
                "scene color components must be finite and between 0 and 1"
            )


DEFAULT_SCENE_COLOR_PALETTE = SceneColorPalette(
    background=(0.84, 0.85, 0.84),
    floor=(0.25, 0.27, 0.28),
    wall=(0.52, 0.31, 0.16),
    scrap=(0.5, 0.52, 0.54),
)


@dataclass(frozen=True, slots=True)
class RenderConfig:
    width: int = DEFAULT_FRAME_WIDTH
    height: int = DEFAULT_FRAME_HEIGHT
    palette: SceneColorPalette = DEFAULT_SCENE_COLOR_PALETTE

    @classmethod
    def from_camera_config(
        cls,
        *,
        width: int,
        height: int,
        camera_config: SyntheticCameraConfig | None,
    ) -> RenderConfig:
        palette = (
            SceneColorPalette.from_camera_config(camera_config)
            if camera_config is not None
            else DEFAULT_SCENE_COLOR_PALETTE
        )
        return cls(width=width, height=height, palette=palette)

    def validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("frame dimensions must be positive")
        if self.width > MAX_FRAME_WIDTH or self.height > MAX_FRAME_HEIGHT:
            raise ValueError("frame dimensions exceed the configured limit")
        self.palette.validate()


@dataclass(frozen=True, slots=True)
class RenderResult:
    width: int
    height: int
    render_window: str
    parallel_projection: bool
    surface_vertices: int
    surface_faces: int
    volume_side_faces: int
    height_range_m: tuple[float, float]


@dataclass(frozen=True, slots=True)
class SceneDescription:
    camera_position: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]
    active_inlet_index: int | None


@dataclass(frozen=True, slots=True)
class HeightScale:
    line_points: tuple[tuple[float, float, float], ...]
    label_points: tuple[tuple[float, float, float], ...]
    labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InletGuide:
    top: tuple[float, float, float]
    surface: tuple[float, float, float]
    active: bool


def _apply_camera(
    plotter: pv.Plotter,
    camera_position: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ],
) -> None:
    plotter.reset_camera()  # type: ignore[call-arg]
    plotter.camera_position = camera_position
    plotter.enable_parallel_projection()  # type: ignore[call-arg]


def _height_tick_levels(floor_z_m: float, top_z_m: float) -> tuple[float, ...]:
    levels = [floor_z_m]
    level = math.ceil(floor_z_m / HEIGHT_TICK_INTERVAL_M) * HEIGHT_TICK_INTERVAL_M
    if math.isclose(level, floor_z_m, rel_tol=0.0, abs_tol=1e-9):
        level += HEIGHT_TICK_INTERVAL_M
    while level < top_z_m - 1e-9:
        levels.append(level)
        level += HEIGHT_TICK_INTERVAL_M
    if not math.isclose(levels[-1], top_z_m, rel_tol=0.0, abs_tol=1e-9):
        levels.append(top_z_m)
    return tuple(levels)


def _format_height(level: float) -> str:
    normalized = 0.0 if math.isclose(level, 0.0, abs_tol=1e-9) else level
    return f"{normalized:g} m"


def _height_label_font_size(frame_height: int) -> int:
    return max(16, min(28, round(frame_height / 40)))


def _height_scale(
    header: SceneDefinition,
    camera_position: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ],
) -> HeightScale:
    eye, focal, view_up = (
        np.asarray(point, dtype=np.float64) for point in camera_position
    )
    view_direction = focal - eye
    screen_right = np.cross(view_direction, np.asarray(view_up, dtype=np.float64))
    screen_right[2] = 0.0
    screen_right /= np.linalg.norm(screen_right)
    right_x, right_y = float(screen_right[0]), float(screen_right[1])
    edge_x, edge_y = max(
        header.scene.boundary_xy_m,
        key=lambda point: (point[0] * right_x + point[1] * right_y, point),
    )
    x_span = max(point[0] for point in header.scene.boundary_xy_m) - min(
        point[0] for point in header.scene.boundary_xy_m
    )
    y_span = max(point[1] for point in header.scene.boundary_xy_m) - min(
        point[1] for point in header.scene.boundary_xy_m
    )
    z_span = header.scene.top_z_m - header.scene.floor_z_m
    tick_length = 0.025 * max(x_span, y_span, z_span, 1.0)
    label_offset = 2.2 * tick_length
    levels = _height_tick_levels(
        header.scene.floor_z_m,
        header.scene.top_z_m,
    )
    axis_start = (edge_x, edge_y, header.scene.floor_z_m)
    axis_end = (edge_x, edge_y, header.scene.top_z_m)
    tick_points = tuple(
        point
        for level in levels
        for point in (
            (edge_x, edge_y, level),
            (edge_x + right_x * tick_length, edge_y + right_y * tick_length, level),
        )
    )
    label_points = tuple(
        (
            edge_x + right_x * label_offset,
            edge_y + right_y * label_offset,
            level,
        )
        for level in levels
    )
    return HeightScale(
        line_points=(axis_start, axis_end, *tick_points),
        label_points=label_points,
        labels=tuple(_format_height(level) for level in levels),
    )


def _poly_data(mesh: Mesh) -> pv.PolyData:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(
        [value for face in mesh.faces for value in (3, *face)], dtype=np.int64
    )
    return pv.PolyData(vertices, faces)


def _camera_position(
    header: SceneDefinition,
) -> tuple[
    tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]
]:
    x_values = tuple(point[0] for point in header.scene.boundary_xy_m)
    y_values = tuple(point[1] for point in header.scene.boundary_xy_m)
    center_x = (min(x_values) + max(x_values)) / 2
    center_y = (min(y_values) + max(y_values)) / 2
    center_z = (header.scene.floor_z_m + header.scene.top_z_m) / 2
    span = max(
        max(x_values) - min(x_values),
        max(y_values) - min(y_values),
        header.scene.top_z_m - header.scene.floor_z_m,
        1.0,
    )
    focal = (center_x, center_y, center_z)
    return (
        (center_x + 1.7 * span, center_y - 1.7 * span, center_z + 1.3 * span),
        focal,
        (0.0, 0.0, 1.0),
    )


def _inlet_guides(
    header: SceneDefinition,
    frame: SceneFrame,
    active_inlet_index: int | None,
) -> tuple[InletGuide, ...]:
    return tuple(
        InletGuide(
            top=(x_m, y_m, header.scene.top_z_m),
            surface=(x_m, y_m, surface_height_at(frame, x_m, y_m)),
            active=index == active_inlet_index,
        )
        for index, (x_m, y_m) in enumerate(header.scene.inlet_positions_xy_m)
    )


def describe_scene(
    header: SceneDefinition,
    frame: SceneFrame,
    *,
    config: RenderConfig,
) -> SceneDescription:
    config.validate()
    active_inlet = (
        frame.scenario.current_inlet_index
        if frame.scenario.phase == "filling"
        else None
    )
    return SceneDescription(
        camera_position=_camera_position(header),
        active_inlet_index=active_inlet,
    )


def render_scene(
    header: SceneDefinition,
    frame: SceneFrame,
    geometry: SceneGeometry,
    *,
    config: RenderConfig | None = None,
) -> tuple[bytes, RenderResult]:
    config = config or RenderConfig()
    config.validate()
    description = describe_scene(
        header,
        frame,
        config=config,
    )
    plotter = pv.Plotter(off_screen=True, window_size=[config.width, config.height])
    plotter.set_background(config.palette.background)  # type: ignore[arg-type]
    try:
        plotter.add_mesh(_poly_data(geometry.floor), color=config.palette.floor)
        plotter.add_mesh(
            _poly_data(geometry.walls),
            color=config.palette.wall,
            edge_color=MESH_EDGE_COLOR,
            opacity=0.3,
            show_edges=True,
        )
        if geometry.volume_sides.faces:
            plotter.add_mesh(
                _poly_data(geometry.volume_sides),
                color=config.palette.scrap,
                smooth_shading=False,
            )
        plotter.add_mesh(
            _poly_data(geometry.surface),
            color=config.palette.scrap,
            edge_color=MESH_EDGE_COLOR,
            smooth_shading=False,
            show_edges=True,
        )
        height_scale = _height_scale(header, description.camera_position)
        plotter.add_lines(
            np.asarray(height_scale.line_points),
            color=OVERLAY_COLOR,
            width=2,
        )
        plotter.add_point_labels(
            height_scale.label_points,
            height_scale.labels,
            bold=False,
            font_size=_height_label_font_size(config.height),
            text_color=OVERLAY_COLOR,
            show_points=False,
            shape=None,
            always_visible=True,
            justification_horizontal="left",
        )
        x_values = tuple(point[0] for point in header.scene.boundary_xy_m)
        y_values = tuple(point[1] for point in header.scene.boundary_xy_m)
        marker_scale = max(
            max(x_values) - min(x_values), max(y_values) - min(y_values), 1.0
        )
        for guide in _inlet_guides(
            header,
            frame,
            description.active_inlet_index,
        ):
            color = ACTIVE_INLET_COLOR if guide.active else INACTIVE_INLET_COLOR
            plotter.add_lines(
                np.asarray((guide.top, guide.surface)),
                color=color,
                width=3,
            )
            plotter.add_mesh(
                pv.Sphere(
                    radius=0.03 * marker_scale,
                    center=guide.top,
                ),
                color=color,
            )
            plotter.add_mesh(
                pv.Sphere(
                    radius=0.022 * marker_scale,
                    center=guide.surface,
                ),
                color=color,
            )
        _apply_camera(plotter, description.camera_position)
        parallel_projection = bool(plotter.camera.parallel_projection)
        if not parallel_projection:
            raise RuntimeError("camera did not enable parallel projection")
        plotter.render()
        render_window = type(plotter.render_window).__name__
        if render_window not in SUPPORTED_RENDER_WINDOWS:
            raise RuntimeError(f"unexpected render window: {render_window}")
        image = plotter.screenshot(return_img=True)
        if image is None or image.shape[:2] != (config.height, config.width):
            raise RuntimeError("renderer returned an unexpected frame shape")
    finally:
        plotter.close()
    output = BytesIO()
    Image.fromarray(image).save(output, format="PNG")
    return output.getvalue(), RenderResult(
        width=config.width,
        height=config.height,
        render_window=render_window,
        parallel_projection=parallel_projection,
        surface_vertices=len(geometry.surface.vertices),
        surface_faces=len(geometry.surface.faces),
        volume_side_faces=len(geometry.volume_sides.faces),
        height_range_m=(header.scene.floor_z_m, header.scene.top_z_m),
    )
