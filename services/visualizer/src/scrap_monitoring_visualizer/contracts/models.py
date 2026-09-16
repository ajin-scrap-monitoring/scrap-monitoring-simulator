"""Immutable canonical scene version 2 records."""

from dataclasses import dataclass
from typing import Literal

type Coordinate2 = tuple[float, float]
type Coordinate3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class Sensor:
    sensor_id: str
    p0_m: Coordinate3
    u0: Coordinate3
    u90: Coordinate3


@dataclass(frozen=True, slots=True)
class SurfaceGrid:
    cell_size_m: float
    x_coordinates_m: tuple[float, ...]
    y_coordinates_m: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Scene:
    coordinate_system: Literal["right-handed-z-up"]
    length_unit: Literal["m"]
    angle_unit: Literal["deg"]
    boundary_xy_m: tuple[Coordinate2, ...]
    floor_z_m: float
    top_z_m: float
    inlet_positions_xy_m: tuple[Coordinate2, ...]
    sensors: tuple[Sensor, ...]
    surface: SurfaceGrid


@dataclass(frozen=True, slots=True)
class SceneDefinition:
    scene_version: Literal[2]
    type: Literal["scene_definition"]
    environment_id: str
    run_id: str
    input_fingerprint_sha256: str
    seed: int
    scene: Scene


@dataclass(frozen=True, slots=True)
class Scenario:
    elapsed_s: float
    surface_updated_at_s: float
    cycle_index: int
    phase: Literal["filling", "collecting"]
    phase_started_at_s: float
    phase_ends_at_s: float
    phase_duration_s: float
    rate_factor: float
    target_fill_ratio: float
    surface_fill_ratio: float
    surface_volume_m3: float
    current_inlet_index: int | None


@dataclass(frozen=True, slots=True)
class SceneKeyframe:
    scenario: Scenario
    heights_m: tuple[tuple[float, ...], ...]


@dataclass(frozen=True, slots=True)
class Surface:
    cell_size_m: float
    x_coordinates_m: tuple[float, ...]
    y_coordinates_m: tuple[float, ...]
    heights_m: tuple[tuple[float, ...], ...]


@dataclass(frozen=True, slots=True)
class SceneFrame:
    """A static-grid keyframe materialized for existing geometry consumers."""

    scene_version: Literal[2]
    type: Literal["scene_keyframe"]
    sequence: int
    run_id: str
    scenario: Scenario
    surface: Surface


def materialize_keyframe(
    definition: SceneDefinition,
    keyframe: SceneKeyframe,
    sequence: int,
    run_id: str,
) -> SceneFrame:
    grid = definition.scene.surface
    return SceneFrame(
        scene_version=2,
        type="scene_keyframe",
        sequence=sequence,
        run_id=run_id,
        scenario=keyframe.scenario,
        surface=Surface(
            cell_size_m=grid.cell_size_m,
            x_coordinates_m=grid.x_coordinates_m,
            y_coordinates_m=grid.y_coordinates_m,
            heights_m=keyframe.heights_m,
        ),
    )


@dataclass(frozen=True, slots=True)
class SceneSegment:
    scene_version: Literal[2]
    type: Literal["scene_segment"]
    sequence: int
    left_sequence: int
    right_sequence: int
    run_id: str
    left: SceneKeyframe
    right: SceneKeyframe


@dataclass(frozen=True, slots=True)
class ParsedRecord:
    raw_line: bytes
    value: SceneDefinition | SceneSegment
