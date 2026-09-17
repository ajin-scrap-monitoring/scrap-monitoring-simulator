"""Perspective PBR renderer behind a replaceable synthetic camera interface."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from types import MappingProxyType
from typing import Protocol, cast

import numpy as np
import pyvista as pv
import vtk
from PIL import Image
from vtk.util.numpy_support import numpy_to_vtk, vtk_to_numpy

from scrap_monitoring_visualizer.contracts.models import SceneDefinition, SceneFrame
from scrap_monitoring_visualizer.geometry import (
    Mesh,
    SceneGeometry,
    SceneGeometryTopology,
    build_scene_geometry_topology,
)

from .models import (
    InterpolatedFrame,
    MachineConfig,
    MaterialConfig,
    RenderedCameraFrame,
    SyntheticCameraConfig,
)


class SyntheticRenderer(Protocol):
    def render(
        self,
        header: SceneDefinition,
        frame: SceneFrame,
        config: SyntheticCameraConfig,
        *,
        interpolation: InterpolatedFrame | None = None,
    ) -> RenderedCameraFrame: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CameraPlacement:
    position: tuple[float, float, float]
    target: tuple[float, float, float]
    view_up: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ChutePose:
    pivot_xy_m: tuple[float, float]
    outlet_xy_m: tuple[float, float]
    polar_angle_rad: float


@dataclass(frozen=True, slots=True)
class TimedRenderedCameraFrame(RenderedCameraFrame):
    stage_seconds: Mapping[str, float]


_GEOMETRY_EPSILON = 1e-9
_MAX_CHUTE_TIP_ANGLE_RAD = math.radians(20.0)
_CHUTE_JOINT_SEGMENTS = 6
_CHUTE_TIP_SEGMENTS = 4


class _VtkBilinearScaler:
    def __init__(
        self,
        source_width: int,
        source_height: int,
        output_width: int,
        output_height: int,
    ) -> None:
        self._source_width = source_width
        self._source_height = source_height
        self._output_width = output_width
        self._output_height = output_height
        self._input = vtk.vtkImageData()
        self._input.SetDimensions(source_width, source_height, 1)
        interpolator = vtk.vtkImageInterpolator()
        interpolator.SetInterpolationModeToLinear()
        self._resize = vtk.vtkImageResize()
        self._resize.SetInputData(self._input)
        self._resize.SetResizeMethodToOutputDimensions()
        self._resize.SetOutputDimensions(output_width, output_height, 1)
        self._resize.SetInterpolator(interpolator)
        self._resize.InterpolateOn()

    def resize(self, image: np.ndarray) -> np.ndarray:
        if image.shape != (self._source_height, self._source_width, 3):
            raise ValueError("bilinear scaler received an unexpected frame shape")
        pixels = numpy_to_vtk(
            np.ascontiguousarray(image).reshape(-1, 3),
            deep=False,
        )
        pixels.SetNumberOfComponents(3)
        self._input.GetPointData().SetScalars(pixels)
        self._input.Modified()
        self._resize.Update()
        output = vtk_to_numpy(self._resize.GetOutput().GetPointData().GetScalars())
        return cast(
            np.ndarray,
            output.reshape(self._output_height, self._output_width, 3),
        )


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


def _chute_pivot(
    header: SceneDefinition,
    machine: MachineConfig,
) -> tuple[float, float]:
    inlet_positions = header.scene.inlet_positions_xy_m
    boundary = header.scene.boundary_xy_m
    first_x, first_y = inlet_positions[0]

    def upstream_pivot() -> tuple[float, float]:
        highest_y = max(point[1] for point in boundary)
        return (
            first_x,
            max(highest_y, max(point[1] for point in inlet_positions))
            + machine.conveyor_width_m,
        )

    if len(inlet_positions) == 1:
        return upstream_pivot()

    second_x, second_y = inlet_positions[1]
    delta_x = second_x - first_x
    delta_y = second_y - first_y
    if abs(delta_y) <= _GEOMETRY_EPSILON:
        return upstream_pivot()
    pivot_y = (delta_x * delta_x + second_y * second_y - first_y * first_y) / (
        2.0 * delta_y
    )
    if not math.isfinite(pivot_y) or pivot_y <= max(first_y, second_y):
        return upstream_pivot()
    return first_x, pivot_y


def _machine_center_z(header: SceneDefinition, machine: MachineConfig) -> float:
    return header.scene.top_z_m + machine.conveyor_center_above_wall_m


def _shortest_polar_delta(
    source_angle: float,
    target_angle: float,
    *,
    source_index: int,
    target_index: int,
) -> float:
    delta = math.atan2(
        math.sin(target_angle - source_angle),
        math.cos(target_angle - source_angle),
    )
    if math.isclose(abs(delta), math.pi, rel_tol=0.0, abs_tol=1e-12):
        return -math.pi if target_index > source_index else math.pi
    return delta


def _smoothstep(alpha: float) -> float:
    return alpha * alpha * (3.0 - 2.0 * alpha)


def _joint_length_m(
    header: SceneDefinition,
    machine: MachineConfig,
) -> float:
    pivot = np.asarray(_chute_pivot(header, machine), dtype=np.float64)
    minimum_radius = min(
        float(np.linalg.norm(np.asarray(inlet, dtype=np.float64) - pivot))
        for inlet in header.scene.inlet_positions_xy_m
    )
    return min(
        machine.conveyor_width_m * 1.25,
        machine.conveyor_length_m * 0.75,
        minimum_radius * machine.tip_fraction * 1.5,
    )


def _chute_pose(
    header: SceneDefinition,
    frame: SceneFrame,
    machine: MachineConfig,
    interpolation: InterpolatedFrame | None = None,
) -> ChutePose:
    inlet_positions = header.scene.inlet_positions_xy_m
    default_index = frame.scenario.current_inlet_index
    if default_index is None:
        default_index = 0
    source_index_value: int | None
    target_index_value: int | None
    if interpolation is None:
        source_index_value = default_index
        target_index_value = default_index
        alpha = 1.0
    else:
        source_index_value = interpolation.left_inlet_index
        target_index_value = interpolation.right_inlet_index
        alpha = interpolation.alpha
    source_index = 0 if source_index_value is None else source_index_value
    target_index = 0 if target_index_value is None else target_index_value
    if not math.isfinite(alpha) or not 0.0 <= alpha <= 1.0:
        raise ValueError("chute interpolation alpha must be between zero and one")

    pivot_x, pivot_y = _chute_pivot(header, machine)
    source_x, source_y = inlet_positions[source_index]
    target_x, target_y = inlet_positions[target_index]
    source_dx = source_x - pivot_x
    source_dy = source_y - pivot_y
    target_dx = target_x - pivot_x
    target_dy = target_y - pivot_y
    source_angle = math.atan2(source_dy, source_dx)
    target_angle = math.atan2(target_dy, target_dx)
    eased_alpha = _smoothstep(alpha)
    polar_angle = source_angle + eased_alpha * _shortest_polar_delta(
        source_angle,
        target_angle,
        source_index=source_index,
        target_index=target_index,
    )
    source_radius = math.hypot(source_dx, source_dy)
    target_radius = math.hypot(target_dx, target_dy)
    radius = source_radius + eased_alpha * (target_radius - source_radius)
    if radius <= _GEOMETRY_EPSILON:
        raise ValueError("chute outlet radius must be positive")
    return ChutePose(
        pivot_xy_m=(pivot_x, pivot_y),
        outlet_xy_m=(
            pivot_x + radius * math.cos(polar_angle),
            pivot_y + radius * math.sin(polar_angle),
        ),
        polar_angle_rad=polar_angle,
    )


def _fixed_conveyor_poly_data(
    header: SceneDefinition,
    machine: MachineConfig,
) -> pv.PolyData:
    pivot_x, pivot_y = _chute_pivot(header, machine)
    direction_x, direction_y = 0.0, -1.0
    lateral_x = -direction_y
    lateral_y = direction_x
    half_width = machine.conveyor_width_m / 2.0
    center_z = _machine_center_z(header, machine)
    lower_z = center_z - machine.conveyor_body_height_m / 2.0
    upper_z = center_z + machine.conveyor_body_height_m / 2.0
    deck_z = center_z
    rail_thickness = min(
        machine.conveyor_width_m * 0.06,
        machine.conveyor_body_height_m / 2.0,
    )
    joint_length_m = _joint_length_m(header, machine)

    def body_point(
        longitudinal_m: float,
        lateral_m: float,
        z_m: float,
    ) -> tuple[float, float, float]:
        return (
            pivot_x + longitudinal_m * direction_x + lateral_m * lateral_x,
            pivot_y + longitudinal_m * direction_y + lateral_m * lateral_y,
            z_m,
        )

    def box_points(
        downstream_m: float,
        upstream_m: float,
        lateral_min_m: float,
        lateral_max_m: float,
        bottom_z_m: float,
        top_z_m: float,
    ) -> np.ndarray:
        return np.asarray(
            (
                body_point(downstream_m, lateral_min_m, bottom_z_m),
                body_point(downstream_m, lateral_max_m, bottom_z_m),
                body_point(upstream_m, lateral_max_m, bottom_z_m),
                body_point(upstream_m, lateral_min_m, bottom_z_m),
                body_point(downstream_m, lateral_min_m, top_z_m),
                body_point(downstream_m, lateral_max_m, top_z_m),
                body_point(upstream_m, lateral_max_m, top_z_m),
                body_point(upstream_m, lateral_min_m, top_z_m),
            ),
            dtype=np.float64,
        )

    base = box_points(
        -joint_length_m / 2.0,
        -machine.conveyor_length_m,
        -half_width,
        half_width,
        lower_z,
        deck_z,
    )
    rail_downstream_m = -joint_length_m / 2.0
    left_rail = box_points(
        rail_downstream_m,
        -machine.conveyor_length_m,
        -half_width,
        -half_width + rail_thickness,
        deck_z,
        upper_z,
    )
    right_rail = box_points(
        rail_downstream_m,
        -machine.conveyor_length_m,
        half_width - rail_thickness,
        half_width,
        deck_z,
        upper_z,
    )
    points = np.concatenate((base, left_rail, right_rail))
    faces = np.concatenate(
        tuple(
            _closed_hexahedron_faces(8 * index, close_downstream=False)
            for index in range(3)
        )
    )
    return pv.PolyData(points, faces)


def _closed_hexahedron_faces(
    point_offset: int = 0,
    *,
    close_downstream: bool = True,
) -> np.ndarray:
    quads = [
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ]
    if not close_downstream:
        del quads[2]
    faces = np.asarray(
        [value for quad in quads for value in (4, *quad)],
        dtype=np.int64,
    )
    indexed_faces = faces.reshape(-1, 5)
    indexed_faces[:, 1:] += point_offset
    return indexed_faces.reshape(-1)


def _chute_poly_data(
    header: SceneDefinition,
    frame: SceneFrame,
    machine: MachineConfig,
    interpolation: InterpolatedFrame | None = None,
) -> pv.PolyData:
    pose = _chute_pose(header, frame, machine, interpolation)
    pivot_x, pivot_y = pose.pivot_xy_m
    radius = math.hypot(
        pose.outlet_xy_m[0] - pivot_x,
        pose.outlet_xy_m[1] - pivot_y,
    )
    cosine = math.cos(pose.polar_angle_rad)
    sine = math.sin(pose.polar_angle_rad)
    center_z = _machine_center_z(header, machine)
    tip_run = radius * (1.0 - machine.tip_fraction)
    if math.atan2(machine.tip_drop_m, tip_run) > _MAX_CHUTE_TIP_ANGLE_RAD:
        raise ValueError("chute tip angle exceeds 20 degrees")

    joint_length_m = _joint_length_m(header, machine)
    tip_start_m = radius * machine.tip_fraction
    if tip_start_m <= joint_length_m / 2.0:
        raise ValueError("chute joint leaves no straight section before the tip")

    pivot = np.asarray((pivot_x, pivot_y), dtype=np.float64)
    fixed_direction = np.asarray((0.0, -1.0), dtype=np.float64)
    chute_direction = np.asarray((cosine, sine), dtype=np.float64)
    joint_start = pivot - fixed_direction * joint_length_m / 2.0
    joint_end = pivot + chute_direction * joint_length_m / 2.0
    start_tangent = fixed_direction * joint_length_m
    end_tangent = chute_direction * joint_length_m
    rings: list[tuple[np.ndarray, np.ndarray, float, float]] = []
    for joint_index in range(_CHUTE_JOINT_SEGMENTS + 1):
        alpha = joint_index / _CHUTE_JOINT_SEGMENTS
        alpha_squared = alpha * alpha
        alpha_cubed = alpha_squared * alpha
        center = (
            (2.0 * alpha_cubed - 3.0 * alpha_squared + 1.0) * joint_start
            + (alpha_cubed - 2.0 * alpha_squared + alpha) * start_tangent
            + (-2.0 * alpha_cubed + 3.0 * alpha_squared) * joint_end
            + (alpha_cubed - alpha_squared) * end_tangent
        )
        tangent = (
            (6.0 * alpha_squared - 6.0 * alpha) * joint_start
            + (3.0 * alpha_squared - 4.0 * alpha + 1.0) * start_tangent
            + (-6.0 * alpha_squared + 6.0 * alpha) * joint_end
            + (3.0 * alpha_squared - 2.0 * alpha) * end_tangent
        )
        tangent_length = float(np.linalg.norm(tangent))
        if tangent_length <= _GEOMETRY_EPSILON:
            raise ValueError("chute joint tangent must be positive")
        rings.append(
            (
                center,
                tangent / tangent_length,
                center_z,
                machine.conveyor_width_m,
            )
        )

    rings.append(
        (
            pivot + chute_direction * tip_start_m,
            chute_direction,
            center_z,
            machine.conveyor_width_m,
        )
    )
    for tip_index in range(1, _CHUTE_TIP_SEGMENTS + 1):
        tip_alpha = tip_index / _CHUTE_TIP_SEGMENTS
        eased_tip_alpha = _smoothstep(tip_alpha)
        rings.append(
            (
                pivot + chute_direction * (tip_start_m + tip_run * tip_alpha),
                chute_direction,
                center_z - machine.tip_drop_m * eased_tip_alpha,
                machine.conveyor_width_m
                + (machine.outlet_width_m - machine.conveyor_width_m) * eased_tip_alpha,
            )
        )
    half_body_height_m = machine.conveyor_body_height_m / 2.0
    rail_height_above_deck_m = machine.duct_height_m - half_body_height_m
    rail_thickness_m = min(
        machine.conveyor_width_m * 0.06,
        half_body_height_m,
    )
    points = np.asarray(
        [
            (
                center[0] - direction[1] * lateral_m,
                center[1] + direction[0] * lateral_m,
                station_z + vertical_m,
            )
            for center, direction, station_z, width_m in rings
            for lateral_m, vertical_m in (
                (-width_m / 2.0, -half_body_height_m),
                (width_m / 2.0, -half_body_height_m),
                (width_m / 2.0, rail_height_above_deck_m),
                (width_m / 2.0 - rail_thickness_m, rail_height_above_deck_m),
                (width_m / 2.0 - rail_thickness_m, 0.0),
                (-width_m / 2.0 + rail_thickness_m, 0.0),
                (-width_m / 2.0 + rail_thickness_m, rail_height_above_deck_m),
                (-width_m / 2.0, rail_height_above_deck_m),
            )
        ],
        dtype=np.float64,
    )
    faces = np.asarray(
        [
            value
            for ring_index in range(len(rings) - 1)
            for edge_index in range(8)
            for value in (
                4,
                ring_index * 8 + edge_index,
                ring_index * 8 + (edge_index + 1) % 8,
                (ring_index + 1) * 8 + (edge_index + 1) % 8,
                (ring_index + 1) * 8 + edge_index,
            )
        ],
        dtype=np.int64,
    )
    return pv.PolyData(points, faces)


def _add_machine_mesh(
    plotter: pv.Plotter,
    data: pv.PolyData,
    material: MaterialConfig,
    *,
    show_edges: bool = True,
) -> None:
    plotter.add_mesh(
        data,
        color=material.color,
        edge_color=(0.3, 0.31, 0.31),
        line_width=1.0,
        metallic=material.metallic,
        opacity=1.0,
        pbr=True,
        roughness=material.roughness,
        ambient=0.3,
        smooth_shading=False,
        show_edges=show_edges,
    )


def _add_fixed_conveyor(
    plotter: pv.Plotter,
    header: SceneDefinition,
    machine: MachineConfig,
    material: MaterialConfig,
) -> pv.PolyData:
    data = _fixed_conveyor_poly_data(header, machine)
    _add_machine_mesh(plotter, data, material, show_edges=False)
    return data


def _add_chute(
    plotter: pv.Plotter,
    header: SceneDefinition,
    frame: SceneFrame,
    machine: MachineConfig,
    material: MaterialConfig,
    interpolation: InterpolatedFrame | None = None,
) -> pv.PolyData:
    data = _chute_poly_data(header, frame, machine, interpolation)
    _add_machine_mesh(plotter, data, material, show_edges=False)
    return data


def _apply_effects(
    image: np.ndarray,
    *,
    seed: int,
    noise_standard_deviation: float,
    vignette_strength: float,
) -> np.ndarray:
    if noise_standard_deviation == 0.0 and vignette_strength == 0.0:
        return image
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


def _opengl_capability(capabilities: str, name: str) -> str:
    expected = name.casefold()
    for line in capabilities.splitlines():
        label, separator, value = line.partition(":")
        if separator and label.strip().casefold() == expected:
            observed = value.strip()
            if observed:
                return observed
    raise RuntimeError(f"renderer did not report {name}")


def _validate_render_device(
    config: SyntheticCameraConfig,
    *,
    supports_opengl: bool,
    capabilities: str,
) -> None:
    if config.backend != "egl":
        return
    if not supports_opengl:
        raise RuntimeError("configured egl backend does not support OpenGL")
    vendor = _opengl_capability(capabilities, "OpenGL vendor string")
    renderer = _opengl_capability(capabilities, "OpenGL renderer string")
    if "nvidia" not in vendor.casefold() or "nvidia" not in renderer.casefold():
        raise RuntimeError(
            "configured egl backend requires an NVIDIA OpenGL vendor and renderer; "
            f"observed vendor={vendor!r}, renderer={renderer!r}"
        )


class VtkPbrRenderer:
    """Current CPU-capable backend with an EGL-compatible boundary."""

    def __init__(self) -> None:
        self._plotter: pv.Plotter | None = None
        self._header: SceneDefinition | None = None
        self._config: SyntheticCameraConfig | None = None
        self._topology: SceneGeometryTopology | None = None
        self._surface_data: pv.PolyData | None = None
        self._volume_data: pv.PolyData | None = None
        self._fixed_conveyor_data: pv.PolyData | None = None
        self._chute_data: pv.PolyData | None = None
        self._chute_pose: ChutePose | None = None
        self._scaler: _VtkBilinearScaler | None = None
        self._render_device_validated = False

    def close(self) -> None:
        if self._plotter is not None:
            self._plotter.close()
        self._plotter = None
        self._header = None
        self._config = None
        self._topology = None
        self._surface_data = None
        self._volume_data = None
        self._fixed_conveyor_data = None
        self._chute_data = None
        self._chute_pose = None
        self._scaler = None
        self._render_device_validated = False

    def _initialize_scene(
        self,
        header: SceneDefinition,
        frame: SceneFrame,
        config: SyntheticCameraConfig,
        topology: SceneGeometryTopology,
        geometry: SceneGeometry,
        interpolation: InterpolatedFrame | None,
    ) -> pv.Plotter:
        self.close()
        placement = camera_placement(header, config)
        plotter = pv.Plotter(
            off_screen=True,
            window_size=[config.video.raster_width, config.video.raster_height],
            lighting="none",
        )
        render_window = plotter.render_window
        if render_window is None:
            raise RuntimeError("renderer did not create a render window")
        render_window.SetMultiSamples(0)
        self._plotter = plotter
        try:
            plotter.set_background(config.background_color)  # type: ignore[arg-type]
            _add_pbr_mesh(plotter, geometry.floor, config.floor_material)
            _add_pbr_mesh(
                plotter,
                geometry.walls,
                config.wall_material,
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
            self._fixed_conveyor_data = _add_fixed_conveyor(
                plotter,
                header,
                config.machine,
                config.chute_material,
            )
            self._chute_data = _add_chute(
                plotter,
                header,
                frame,
                config.machine,
                config.chute_material,
                interpolation,
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
            plotter.reset_camera_clipping_range()
        except Exception:
            self.close()
            raise
        self._header = header
        self._config = config
        self._topology = topology
        self._chute_pose = _chute_pose(header, frame, config.machine, interpolation)
        self._scaler = _VtkBilinearScaler(
            config.video.raster_width,
            config.video.raster_height,
            config.video.width,
            config.video.height,
        )
        return plotter

    def _update_scene(
        self,
        frame: SceneFrame,
        geometry: SceneGeometry,
        interpolation: InterpolatedFrame | None,
    ) -> bool:
        if self._surface_data is None or self._volume_data is None:
            return False
        if self._surface_data.n_points != len(geometry.surface.vertices):
            return False
        if self._volume_data.n_points != len(geometry.volume_sides.vertices):
            return False
        self._surface_data.points = np.asarray(
            geometry.surface.vertices,
            dtype=np.float64,
        )
        self._volume_data.points = np.asarray(
            geometry.volume_sides.vertices,
            dtype=np.float64,
        )
        if self._fixed_conveyor_data is None or self._chute_data is None:
            return False
        assert self._header is not None
        assert self._config is not None
        chute_pose = _chute_pose(
            self._header,
            frame,
            self._config.machine,
            interpolation,
        )
        if chute_pose != self._chute_pose:
            self._chute_data.copy_from(
                _chute_poly_data(
                    self._header,
                    frame,
                    self._config.machine,
                    interpolation,
                )
            )
            self._chute_pose = chute_pose
        return True

    def render(
        self,
        header: SceneDefinition,
        frame: SceneFrame,
        config: SyntheticCameraConfig,
        *,
        interpolation: InterpolatedFrame | None = None,
    ) -> TimedRenderedCameraFrame:
        stage_seconds: dict[str, float] = {}
        stage_started_at = time.perf_counter()
        config.validate()
        plotter = self._plotter
        topology = self._topology
        initialize = (
            plotter is None
            or topology is None
            or self._header != header
            or self._config != config
        )
        if initialize:
            topology = build_scene_geometry_topology(header)
        assert topology is not None
        geometry = topology.materialize(frame)
        if initialize or not self._update_scene(frame, geometry, interpolation):
            plotter = self._initialize_scene(
                header,
                frame,
                config,
                topology,
                geometry,
                interpolation,
            )
        stage_seconds["scene_update"] = time.perf_counter() - stage_started_at
        assert plotter is not None
        image: np.ndarray | None = None
        render_window = ""
        if plotter.camera.parallel_projection:
            raise RuntimeError("synthetic camera must use perspective projection")
        stage_started_at = time.perf_counter()
        plotter.render()
        vtk_render_window = plotter.render_window
        if vtk_render_window is None:
            raise RuntimeError("renderer did not create a render window")
        render_window = type(vtk_render_window).__name__
        _validate_render_window(config, render_window)
        stage_seconds["vtk_render"] = time.perf_counter() - stage_started_at
        stage_started_at = time.perf_counter()
        image = plotter.screenshot(return_img=True)
        stage_seconds["framebuffer_readback"] = time.perf_counter() - stage_started_at
        if config.backend == "egl" and not self._render_device_validated:
            _validate_render_device(
                config,
                supports_opengl=bool(vtk_render_window.SupportsOpenGL()),
                capabilities=str(vtk_render_window.ReportCapabilities()),
            )
            self._render_device_validated = True
        if image is None or image.shape[:2] != (
            config.video.raster_height,
            config.video.raster_width,
        ):
            raise RuntimeError("renderer returned an unexpected frame shape")
        assert image is not None
        effect_seed = (
            header.seed ^ frame.sequence ^ round(frame.scenario.elapsed_s * 1_000_000)
        )
        stage_started_at = time.perf_counter()
        processed = _apply_effects(
            image,
            seed=effect_seed,
            noise_standard_deviation=config.effects.noise_standard_deviation,
            vignette_strength=config.effects.vignette_strength,
        )
        stage_seconds["effects"] = time.perf_counter() - stage_started_at
        scaler = self._scaler
        if scaler is None:
            raise RuntimeError("renderer did not initialize the bilinear scaler")
        stage_started_at = time.perf_counter()
        output_image = Image.fromarray(scaler.resize(processed))
        stage_seconds["resize"] = time.perf_counter() - stage_started_at
        stage_started_at = time.perf_counter()
        output = BytesIO()
        output_image.save(
            output,
            format="JPEG",
            quality=config.video.jpeg_quality,
            optimize=False,
            progressive=False,
        )
        jpeg = output.getvalue()
        stage_seconds["jpeg_encode"] = time.perf_counter() - stage_started_at
        if len(jpeg) > config.video.max_frame_bytes:
            raise RuntimeError("rendered JPEG exceeds the configured byte limit")
        return TimedRenderedCameraFrame(
            jpeg=jpeg,
            sequence=frame.sequence,
            elapsed_s=frame.scenario.elapsed_s,
            width=config.video.width,
            height=config.video.height,
            render_backend=render_window,
            stage_seconds=MappingProxyType(stage_seconds.copy()),
        )


def create_renderer(config: SyntheticCameraConfig) -> SyntheticRenderer:
    config.validate()
    return VtkPbrRenderer()
