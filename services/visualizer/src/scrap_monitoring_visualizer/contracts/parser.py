"""Strict parser for canonical scene version 1 JSON Lines records."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Never, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from scrap_monitoring_visualizer.limits import (
    MAX_BOUNDARY_VERTICES,
    MAX_GRID_POINTS,
)

from .models import (
    ParsedRecord,
    Scenario,
    Scene,
    SceneDefinition,
    SceneFrame,
    Sensor,
    Surface,
)

MAX_RECORD_BYTES = 1_048_576
_VECTOR_TOLERANCE = 1e-6
_GEOMETRY_TOLERANCE = 1e-9


class ContractError(ValueError):
    """A stable contract rejection with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _reject_constant(value: str) -> Never:
    raise ContractError("invalid_number", f"non-finite JSON number: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate_key", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _walk_finite(value: Any, path: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("invalid_number", f"{path} must be finite")
    if isinstance(value, dict):
        for key, child in value.items():
            _walk_finite(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_finite(child, f"{path}[{index}]")


def _schema_error(error: ValidationError) -> ContractError:
    path = "$"
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return ContractError("schema", f"{path}: {error.message}")


def _coordinate2(value: list[Any]) -> tuple[float, float]:
    return (float(value[0]), float(value[1]))


def _coordinate3(value: list[Any]) -> tuple[float, float, float]:
    return (float(value[0]), float(value[1]), float(value[2]))


def _cross(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(
    a: tuple[float, float], b: tuple[float, float], point: tuple[float, float]
) -> bool:
    return (
        abs(_cross(a, b, point)) <= _GEOMETRY_TOLERANCE
        and min(a[0], b[0]) - _GEOMETRY_TOLERANCE
        <= point[0]
        <= max(a[0], b[0]) + _GEOMETRY_TOLERANCE
        and min(a[1], b[1]) - _GEOMETRY_TOLERANCE
        <= point[1]
        <= max(a[1], b[1]) + _GEOMETRY_TOLERANCE
    )


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    crosses = (_cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b))
    if (crosses[0] > 0 > crosses[1] or crosses[0] < 0 < crosses[1]) and (
        crosses[2] > 0 > crosses[3] or crosses[2] < 0 < crosses[3]
    ):
        return True
    return any(
        abs(cross_value) <= _GEOMETRY_TOLERANCE and _on_segment(start, end, point)
        for cross_value, start, end, point in (
            (crosses[0], a, b, c),
            (crosses[1], a, b, d),
            (crosses[2], c, d, a),
            (crosses[3], c, d, b),
        )
    )


def _validate_polygon(boundary: tuple[tuple[float, float], ...]) -> None:
    if len(boundary) > MAX_BOUNDARY_VERTICES:
        raise ContractError("boundary_limit", "boundary vertex limit exceeded")
    if len(set(boundary)) != len(boundary):
        raise ContractError("boundary", "boundary vertices must be unique")
    area_twice = sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(boundary, boundary[1:] + boundary[:1], strict=True)
    )
    if abs(area_twice) <= _GEOMETRY_TOLERANCE:
        raise ContractError("boundary", "boundary area must be non-zero")
    edges = tuple(zip(boundary, boundary[1:] + boundary[:1], strict=True))
    for left_index, (a, b) in enumerate(edges):
        for right_index, (c, d) in enumerate(edges):
            if right_index <= left_index:
                continue
            if right_index in {left_index + 1, (left_index - 1) % len(edges)}:
                continue
            if _segments_intersect(a, b, c, d):
                raise ContractError("boundary", "boundary must not self-intersect")


def _point_in_polygon(
    point: tuple[float, float], boundary: tuple[tuple[float, float], ...]
) -> bool:
    inside = False
    for left, right in zip(boundary, boundary[1:] + boundary[:1], strict=True):
        if _on_segment(left, right, point):
            return True
        if (left[1] > point[1]) != (right[1] > point[1]):
            crossing_x = (right[0] - left[0]) * (point[1] - left[1]) / (
                right[1] - left[1]
            ) + left[0]
            if point[0] < crossing_x:
                inside = not inside
    return inside


def _validate_vector(vector: tuple[float, float, float], path: str) -> None:
    length = math.sqrt(sum(component * component for component in vector))
    if not math.isclose(length, 1.0, rel_tol=0.0, abs_tol=_VECTOR_TOLERANCE):
        raise ContractError("sensor", f"{path} must be a unit vector")


def _strictly_increasing(values: Iterable[float]) -> bool:
    sequence = tuple(values)
    return all(
        left < right for left, right in zip(sequence, sequence[1:], strict=False)
    )


class ContractParser:
    """Load fixed schemas and convert valid records to immutable models."""

    def __init__(self, contract_root: Path | None = None) -> None:
        root = contract_root or self._default_contract_root()
        self._validators = {
            "scene_definition": self._load_validator(root / "definition.schema.json"),
            "scene_frame": self._load_validator(root / "frame.schema.json"),
        }

    @staticmethod
    def _default_contract_root() -> Path:
        candidate = Path(__file__).with_name("schema") / "v1"
        if (candidate / "definition.schema.json").is_file():
            return candidate
        raise ContractError(
            "schema_source", "canonical scene version 1 schemas not found"
        )

    @staticmethod
    def _load_validator(path: Path) -> Draft202012Validator:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ContractError(
                "schema_source", f"cannot load schema: {path}"
            ) from error
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)

    def parse_line(self, raw_line: bytes) -> ParsedRecord:
        if not raw_line.endswith(b"\n") or raw_line.count(b"\n") != 1:
            raise ContractError(
                "framing", "record must contain exactly one trailing LF"
            )
        if len(raw_line) > MAX_RECORD_BYTES:
            raise ContractError("line_limit", "record byte limit exceeded")
        try:
            text = raw_line[:-1].decode("utf-8")
            value = json.loads(
                text,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except UnicodeDecodeError as error:
            raise ContractError("encoding", "record must be UTF-8") from error
        except json.JSONDecodeError as error:
            raise ContractError("json", f"invalid JSON at byte {error.pos}") from error
        if not isinstance(value, dict):
            raise ContractError("record_type", "record root must be an object")
        _walk_finite(value)
        record_type = value.get("type")
        if record_type not in self._validators:
            raise ContractError("record_type", "unknown record type")
        errors = sorted(
            self._validators[record_type].iter_errors(value),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            raise _schema_error(errors[0])
        if record_type == "scene_definition":
            model: SceneDefinition | SceneFrame = self._definition(
                cast(dict[str, Any], value)
            )
        else:
            model = self._frame(cast(dict[str, Any], value))
        return ParsedRecord(raw_line=raw_line, value=model)

    def _definition(self, value: dict[str, Any]) -> SceneDefinition:
        scene_value = cast(dict[str, Any], value["scene"])
        boundary = tuple(
            _coordinate2(cast(list[Any], item))
            for item in cast(list[Any], scene_value["boundary_xy_m"])
        )
        _validate_polygon(boundary)
        floor_z_m = float(scene_value["floor_z_m"])
        top_z_m = float(scene_value["top_z_m"])
        if floor_z_m >= top_z_m:
            raise ContractError("scene_height", "floor_z_m must be below top_z_m")
        inlets = tuple(
            _coordinate2(cast(list[Any], item))
            for item in cast(list[Any], scene_value["inlet_positions_xy_m"])
        )
        if any(not _point_in_polygon(inlet, boundary) for inlet in inlets):
            raise ContractError("inlet", "inlet position must be inside the boundary")
        sensors = tuple(
            self._sensor(cast(dict[str, Any], item), index)
            for index, item in enumerate(cast(list[Any], scene_value["sensors"]))
        )
        if len({sensor.sensor_id for sensor in sensors}) != len(sensors):
            raise ContractError("sensor", "sensor_id values must be unique")
        scene = Scene(
            coordinate_system="right-handed-z-up",
            length_unit="m",
            angle_unit="deg",
            boundary_xy_m=boundary,
            floor_z_m=floor_z_m,
            top_z_m=top_z_m,
            inlet_positions_xy_m=inlets,
            sensors=sensors,
        )
        return SceneDefinition(
            scene_version=1,
            type="scene_definition",
            environment_id=cast(str, value["environment_id"]),
            run_id=cast(str, value["run_id"]),
            input_fingerprint_sha256=cast(str, value["input_fingerprint_sha256"]),
            seed=cast(int, value["seed"]),
            scene=scene,
        )

    @staticmethod
    def _sensor(value: dict[str, Any], index: int) -> Sensor:
        sensor = Sensor(
            sensor_id=cast(str, value["sensor_id"]),
            p0_m=_coordinate3(cast(list[Any], value["p0_m"])),
            u0=_coordinate3(cast(list[Any], value["u0"])),
            u90=_coordinate3(cast(list[Any], value["u90"])),
        )
        _validate_vector(sensor.u0, f"sensors[{index}].u0")
        _validate_vector(sensor.u90, f"sensors[{index}].u90")
        dot = sum(
            left * right for left, right in zip(sensor.u0, sensor.u90, strict=True)
        )
        if not math.isclose(dot, 0.0, rel_tol=0.0, abs_tol=_VECTOR_TOLERANCE):
            raise ContractError(
                "sensor", f"sensors[{index}] vectors must be orthogonal"
            )
        return sensor

    @staticmethod
    def _frame(value: dict[str, Any]) -> SceneFrame:
        scenario_value = cast(dict[str, Any], value["scenario"])
        scenario = Scenario(
            elapsed_s=float(scenario_value["elapsed_s"]),
            surface_updated_at_s=float(scenario_value["surface_updated_at_s"]),
            cycle_index=cast(int, scenario_value["cycle_index"]),
            phase=cast(Any, scenario_value["phase"]),
            phase_started_at_s=float(scenario_value["phase_started_at_s"]),
            phase_ends_at_s=float(scenario_value["phase_ends_at_s"]),
            phase_duration_s=float(scenario_value["phase_duration_s"]),
            rate_factor=float(scenario_value["rate_factor"]),
            target_fill_ratio=float(scenario_value["target_fill_ratio"]),
            surface_fill_ratio=float(scenario_value["surface_fill_ratio"]),
            surface_volume_m3=float(scenario_value["surface_volume_m3"]),
            current_inlet_index=cast(int | None, scenario_value["current_inlet_index"]),
        )
        if scenario.surface_updated_at_s > scenario.elapsed_s:
            raise ContractError(
                "scenario_time", "surface update cannot be in the future"
            )
        if not (
            scenario.phase_started_at_s
            <= scenario.elapsed_s
            <= scenario.phase_ends_at_s
        ):
            raise ContractError("scenario_time", "elapsed_s must be within the phase")
        if not math.isclose(
            scenario.phase_ends_at_s - scenario.phase_started_at_s,
            scenario.phase_duration_s,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ContractError("scenario_time", "phase duration is inconsistent")
        surface_value = cast(dict[str, Any], value["surface"])
        x_coordinates = tuple(
            float(item) for item in cast(list[Any], surface_value["x_coordinates_m"])
        )
        y_coordinates = tuple(
            float(item) for item in cast(list[Any], surface_value["y_coordinates_m"])
        )
        heights = tuple(
            tuple(float(item) for item in cast(list[Any], row))
            for row in cast(list[Any], surface_value["heights_m"])
        )
        if not _strictly_increasing(x_coordinates) or not _strictly_increasing(
            y_coordinates
        ):
            raise ContractError(
                "surface_coordinates", "surface coordinates must increase"
            )
        if len(heights) != len(y_coordinates) or any(
            len(row) != len(x_coordinates) for row in heights
        ):
            raise ContractError("surface_shape", "heights shape must be y by x")
        if len(x_coordinates) * len(y_coordinates) > MAX_GRID_POINTS:
            raise ContractError("grid_limit", "surface grid point limit exceeded")
        surface = Surface(
            cell_size_m=float(surface_value["cell_size_m"]),
            x_coordinates_m=x_coordinates,
            y_coordinates_m=y_coordinates,
            heights_m=heights,
        )
        return SceneFrame(
            scene_version=1,
            type="scene_frame",
            sequence=cast(int, value["sequence"]),
            run_id=cast(str, value["run_id"]),
            scenario=scenario,
            surface=surface,
        )
