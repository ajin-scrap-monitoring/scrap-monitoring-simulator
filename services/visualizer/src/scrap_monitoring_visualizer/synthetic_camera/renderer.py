"""Perspective PBR renderer behind a replaceable synthetic camera interface."""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

import numpy as np
import pyvista as pv
from PIL import Image

from scrap_monitoring_visualizer.contracts.models import SceneDefinition, SceneFrame
from scrap_monitoring_visualizer.geometry import (
    Mesh,
    SceneGeometry,
    build_scene_geometry,
)

from .models import MaterialConfig, RenderedCameraFrame, SyntheticCameraConfig


class SyntheticRenderer(Protocol):
    def render(
        self,
        header: SceneDefinition,
        frame: SceneFrame,
        config: SyntheticCameraConfig,
    ) -> RenderedCameraFrame: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CameraPlacement:
    position: tuple[float, float, float]
    target: tuple[float, float, float]
    view_up: tuple[float, float, float]


def camera_placement(
    header: SceneDefinition, config: SyntheticCameraConfig
) -> CameraPlacement:
    boundary = header.scene.boundary_xy_m
    minimum_x = min(point[0] for point in boundary)
    maximum_x = max(point[0] for point in boundary)
    minimum_y = min(point[1] for point in boundary)
    maximum_y = max(point[1] for point in boundary)
    span_x = maximum_x - minimum_x
    span_y = maximum_y - minimum_y
    span_z = header.scene.top_z_m - header.scene.floor_z_m
    center_x = (minimum_x + maximum_x) / 2.0
    center_y = (minimum_y + maximum_y) / 2.0
    scale = max(span_x, span_y, span_z, 1.0)

    def transform(vector: tuple[float, float, float]) -> tuple[float, float, float]:
        return (
            center_x + vector[0] * scale,
            center_y + vector[1] * scale,
            header.scene.floor_z_m + vector[2] * scale,
        )

    return CameraPlacement(
        position=transform(config.camera.position_normalized),
        target=transform(config.camera.target_normalized),
        view_up=config.camera.view_up,
    )


def _normalized_point(
    header: SceneDefinition, vector: tuple[float, float, float]
) -> tuple[float, float, float]:
    boundary = header.scene.boundary_xy_m
    minimum_x = min(point[0] for point in boundary)
    maximum_x = max(point[0] for point in boundary)
    minimum_y = min(point[1] for point in boundary)
    maximum_y = max(point[1] for point in boundary)
    center_x = (minimum_x + maximum_x) / 2.0
    center_y = (minimum_y + maximum_y) / 2.0
    scale = max(
        maximum_x - minimum_x,
        maximum_y - minimum_y,
        header.scene.top_z_m - header.scene.floor_z_m,
        1.0,
    )
    return (
        center_x + vector[0] * scale,
        center_y + vector[1] * scale,
        header.scene.floor_z_m + vector[2] * scale,
    )


def _poly_data(mesh: Mesh) -> pv.PolyData:
    points = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(
        [value for face in mesh.faces for value in (3, *face)], dtype=np.int64
    )
    return pv.PolyData(points, faces)


def _add_pbr_mesh(
    plotter: pv.Plotter,
    mesh: Mesh,
    material: MaterialConfig,
    *,
    opacity: float = 1.0,
    smooth_shading: bool = False,
) -> pv.PolyData | None:
    if not mesh.faces:
        return None
    data = _poly_data(mesh)
    plotter.add_mesh(
        data,
        color=material.color,
        metallic=material.metallic,
        opacity=opacity,
        pbr=True,
        roughness=material.roughness,
        smooth_shading=smooth_shading,
        show_edges=False,
    )
    return data


def _scrap_poly_data(
    mesh: Mesh,
    material: MaterialConfig,
    *,
    seed: int,
) -> pv.PolyData:
    data = _poly_data(mesh)
    generator = np.random.default_rng(seed)
    base = np.asarray(material.color, dtype=np.float64) * 255.0
    variation = generator.uniform(0.62, 1.28, size=(len(mesh.faces), 1))
    colors = np.clip(base * variation, 45.0, 210.0).astype(np.uint8)
    data.cell_data["scrap_color"] = colors
    return data


def _add_scrap_surface(
    plotter: pv.Plotter,
    mesh: Mesh,
    material: MaterialConfig,
    *,
    seed: int,
) -> pv.PolyData | None:
    if not mesh.faces:
        return None
    data = _scrap_poly_data(mesh, material, seed=seed)
    plotter.add_mesh(
        data,
        scalars="scrap_color",
        rgb=True,
        metallic=material.metallic,
        pbr=True,
        roughness=material.roughness,
        smooth_shading=False,
        show_edges=False,
    )
    return data


def _chute_poly_data(header: SceneDefinition, frame: SceneFrame) -> pv.PolyData:
    inlet_index = frame.scenario.current_inlet_index
    if inlet_index is None:
        inlet_index = 0
    lower_center_x, lower_center_y = header.scene.inlet_positions_xy_m[inlet_index]
    upper_center_x = sum(point[0] for point in header.scene.inlet_positions_xy_m) / len(
        header.scene.inlet_positions_xy_m
    )
    upper_center_y = sum(point[1] for point in header.scene.inlet_positions_xy_m) / len(
        header.scene.inlet_positions_xy_m
    )
    boundary = header.scene.boundary_xy_m
    planar_scale = max(
        max(point[0] for point in boundary) - min(point[0] for point in boundary),
        max(point[1] for point in boundary) - min(point[1] for point in boundary),
        1.0,
    )
    lower_z = header.scene.top_z_m + 0.15 * planar_scale
    upper_z = header.scene.top_z_m + 0.48 * planar_scale
    lower_x = 0.13 * planar_scale
    lower_y = 0.08 * planar_scale
    upper_x = 0.2 * planar_scale
    upper_y = 0.14 * planar_scale
    angle = math.radians(-15.0 * inlet_index)
    cosine = math.cos(angle)
    sine = math.sin(angle)

    def lower_point(x: float, y: float) -> tuple[float, float, float]:
        return (
            lower_center_x + x * cosine - y * sine,
            lower_center_y + x * sine + y * cosine,
            lower_z,
        )

    points = np.asarray(
        (
            lower_point(-lower_x, -lower_y),
            lower_point(lower_x, -lower_y),
            lower_point(lower_x, lower_y),
            lower_point(-lower_x, lower_y),
            (upper_center_x - upper_x, upper_center_y - upper_y, upper_z),
            (upper_center_x + upper_x, upper_center_y - upper_y, upper_z),
            (upper_center_x + upper_x, upper_center_y + upper_y, upper_z),
            (upper_center_x - upper_x, upper_center_y + upper_y, upper_z),
        ),
        dtype=np.float64,
    )
    faces = np.asarray(
        (
            4,
            0,
            3,
            2,
            1,
            4,
            4,
            5,
            6,
            7,
            4,
            0,
            1,
            5,
            4,
            4,
            1,
            2,
            6,
            5,
            4,
            2,
            3,
            7,
            6,
            4,
            3,
            0,
            4,
            7,
        ),
        dtype=np.int64,
    )
    return pv.PolyData(points, faces)


def _add_chute(
    plotter: pv.Plotter,
    header: SceneDefinition,
    frame: SceneFrame,
    material: MaterialConfig,
) -> pv.PolyData:
    data = _chute_poly_data(header, frame)
    plotter.add_mesh(
        data,
        color=material.color,
        edge_color=(0.3, 0.31, 0.31),
        line_width=1.0,
        metallic=material.metallic,
        pbr=True,
        roughness=material.roughness,
        ambient=0.3,
        smooth_shading=False,
        show_edges=True,
    )
    return data


def _apply_effects(
    image: np.ndarray,
    *,
    seed: int,
    noise_standard_deviation: float,
    vignette_strength: float,
) -> np.ndarray:
    values = image.astype(np.float32) / 255.0
    height, width = values.shape[:2]
    if vignette_strength > 0.0:
        y, x = np.ogrid[-1.0 : 1.0 : complex(height), -1.0 : 1.0 : complex(width)]
        radius = np.minimum(1.0, np.sqrt(x * x + y * y) / np.sqrt(2.0))
        values *= (1.0 - vignette_strength * radius * radius)[..., np.newaxis]
    if noise_standard_deviation > 0.0:
        generator = np.random.default_rng(seed)
        noise = generator.normal(
            0.0,
            noise_standard_deviation,
            size=values.shape,
        ).astype(np.float32)
        values += noise
    return np.asarray(np.clip(values * 255.0, 0.0, 255.0), dtype=np.uint8)


def _validate_render_window(config: SyntheticCameraConfig, observed: str) -> None:
    expected = {
        "osmesa": "vtkOSOpenGLRenderWindow",
        "egl": "vtkEGLRenderWindow",
    }.get(config.backend)
    supported = {"vtkEGLRenderWindow", "vtkOSOpenGLRenderWindow"}
    if (expected is None and observed not in supported) or (
        expected is not None and observed != expected
    ):
        raise RuntimeError(
            f"configured {config.backend} backend produced unexpected window: {observed}"
        )


class VtkPbrRenderer:
    """Current CPU-capable backend with an EGL-compatible boundary."""

    def __init__(self) -> None:
        self._plotter: pv.Plotter | None = None
        self._header: SceneDefinition | None = None
        self._config: SyntheticCameraConfig | None = None
        self._surface_data: pv.PolyData | None = None
        self._volume_data: pv.PolyData | None = None
        self._chute_data: pv.PolyData | None = None

    def close(self) -> None:
        if self._plotter is not None:
            self._plotter.close()
        self._plotter = None
        self._header = None
        self._config = None
        self._surface_data = None
        self._volume_data = None
        self._chute_data = None

    def _initialize_scene(
        self,
        header: SceneDefinition,
        frame: SceneFrame,
        config: SyntheticCameraConfig,
        geometry: SceneGeometry,
    ) -> pv.Plotter:
        self.close()
        placement = camera_placement(header, config)
        plotter = pv.Plotter(
            off_screen=True,
            window_size=[config.video.width, config.video.height],
            lighting="none",
        )
        self._plotter = plotter
        try:
            plotter.set_background(config.background_color)  # type: ignore[arg-type]
            _add_pbr_mesh(plotter, geometry.floor, config.floor_material)
            _add_pbr_mesh(
                plotter,
                geometry.walls,
                config.wall_material,
                opacity=0.96,
            )
            self._volume_data = _add_pbr_mesh(
                plotter,
                geometry.volume_sides,
                config.scrap_material,
                smooth_shading=False,
            )
            self._surface_data = _add_scrap_surface(
                plotter,
                geometry.surface,
                config.scrap_material,
                seed=header.seed,
            )
            self._chute_data = _add_chute(
                plotter,
                header,
                frame,
                config.chute_material,
            )
            for configured_light in config.lights:
                position = _normalized_point(
                    header, configured_light.position_normalized
                )
                light = pv.Light(
                    position=position,
                    focal_point=placement.target,
                    color=configured_light.color,
                    intensity=configured_light.intensity,
                    positional=False,
                )
                plotter.add_light(light)
            plotter.add_light(
                pv.Light(
                    light_type="headlight",
                    color=(1.0, 0.96, 0.9),
                    intensity=0.55,
                )
            )
            plotter.camera_position = (
                placement.position,
                placement.target,
                placement.view_up,
            )
            plotter.camera.parallel_projection = False
            plotter.camera.view_angle = config.camera.view_angle_deg
        except Exception:
            self.close()
            raise
        self._header = header
        self._config = config
        return plotter

    def _update_scene(
        self,
        header: SceneDefinition,
        frame: SceneFrame,
        geometry: SceneGeometry,
    ) -> bool:
        assert self._config is not None
        surface_data = _scrap_poly_data(
            geometry.surface,
            self._config.scrap_material,
            seed=header.seed,
        )
        volume_data = (
            _poly_data(geometry.volume_sides) if geometry.volume_sides.faces else None
        )
        if self._surface_data is None or (self._volume_data is None) != (
            volume_data is None
        ):
            return False
        self._surface_data.copy_from(surface_data)
        if self._volume_data is not None and volume_data is not None:
            self._volume_data.copy_from(volume_data)
        if self._chute_data is None:
            return False
        self._chute_data.copy_from(_chute_poly_data(header, frame))
        return True

    def render(
        self,
        header: SceneDefinition,
        frame: SceneFrame,
        config: SyntheticCameraConfig,
    ) -> RenderedCameraFrame:
        config.validate()
        geometry = build_scene_geometry(header, frame)
        plotter = self._plotter
        if (
            plotter is None
            or self._header != header
            or self._config != config
            or not self._update_scene(header, frame, geometry)
        ):
            plotter = self._initialize_scene(header, frame, config, geometry)
        image: np.ndarray | None = None
        render_window = ""
        plotter.reset_camera_clipping_range()
        if plotter.camera.parallel_projection:
            raise RuntimeError("synthetic camera must use perspective projection")
        plotter.render()
        render_window = type(plotter.render_window).__name__
        _validate_render_window(config, render_window)
        image = plotter.screenshot(return_img=True)
        if image is None or image.shape[:2] != (
            config.video.height,
            config.video.width,
        ):
            raise RuntimeError("renderer returned an unexpected frame shape")
        assert image is not None
        effect_seed = (
            header.seed ^ frame.sequence ^ round(frame.scenario.elapsed_s * 1_000_000)
        )
        processed = _apply_effects(
            image,
            seed=effect_seed,
            noise_standard_deviation=config.effects.noise_standard_deviation,
            vignette_strength=config.effects.vignette_strength,
        )
        output = BytesIO()
        Image.fromarray(processed).save(
            output,
            format="JPEG",
            quality=config.video.jpeg_quality,
            optimize=False,
            progressive=False,
        )
        jpeg = output.getvalue()
        if len(jpeg) > config.video.max_frame_bytes:
            raise RuntimeError("rendered JPEG exceeds the configured byte limit")
        return RenderedCameraFrame(
            jpeg=jpeg,
            sequence=frame.sequence,
            elapsed_s=frame.scenario.elapsed_s,
            width=config.video.width,
            height=config.video.height,
            render_backend=render_window,
        )


def create_renderer(config: SyntheticCameraConfig) -> SyntheticRenderer:
    config.validate()
    return VtkPbrRenderer()
