from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

from scrap_monitoring_visualizer.contracts import (
    ContractError,
    ContractParser,
    SceneDefinition,
    SceneSegment,
)
from scrap_monitoring_visualizer.state import (
    ExecutionState,
    StateError,
    accept_header,
    accept_segment,
    disconnect,
)

CONTRACT_ROOT = Path("../contracts/scene/v2")


def _scenario(elapsed_s: float, inlet: int | None = 0) -> dict[str, Any]:
    return {
        "elapsed_s": elapsed_s,
        "surface_updated_at_s": elapsed_s,
        "cycle_index": 0,
        "phase": "filling",
        "phase_started_at_s": 0.0,
        "phase_ends_at_s": 10.0,
        "phase_duration_s": 10.0,
        "rate_factor": 1.0,
        "target_fill_ratio": 0.9,
        "surface_fill_ratio": 0.1,
        "surface_volume_m3": 0.1,
        "current_inlet_index": inlet,
    }


def _definition() -> dict[str, Any]:
    return {
        "scene_version": 2,
        "type": "scene_definition",
        "environment_id": "contract-fixture-v2",
        "run_id": "fixture-run-a",
        "input_fingerprint_sha256": "0" * 64,
        "seed": 42,
        "scene": {
            "coordinate_system": "right-handed-z-up",
            "length_unit": "m",
            "angle_unit": "deg",
            "boundary_xy_m": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            "floor_z_m": 0.0,
            "top_z_m": 1.0,
            "inlet_positions_xy_m": [[0.5, 0.5]],
            "sensors": [
                {
                    "sensor_id": "sensor-a",
                    "p0_m": [0.5, 0.5, 1.0],
                    "u0": [0.0, 0.0, -1.0],
                    "u90": [1.0, 0.0, 0.0],
                },
                {
                    "sensor_id": "sensor-b",
                    "p0_m": [0.25, 0.25, 1.0],
                    "u0": [0.0, 0.0, -1.0],
                    "u90": [0.0, 1.0, 0.0],
                },
            ],
            "surface": {
                "cell_size_m": 1.0,
                "x_coordinates_m": [0.0, 1.0],
                "y_coordinates_m": [0.0, 1.0],
            },
        },
    }


def _segment(sequence: int = 1, elapsed_s: float = 0.1) -> dict[str, Any]:
    return {
        "scene_version": 2,
        "type": "scene_segment",
        "sequence": sequence,
        "left_sequence": sequence - 1,
        "right_sequence": sequence,
        "run_id": "fixture-run-a",
        "left": {
            "scenario": _scenario(elapsed_s - 0.1),
            "heights_m": [[0.0, 0.1], [0.2, 0.3]],
        },
        "right": {
            "scenario": _scenario(elapsed_s),
            "heights_m": [[0.0, 0.2], [0.3, 0.4]],
        },
    }


def _line(document: dict[str, Any]) -> bytes:
    return json.dumps(document, separators=(",", ":"), allow_nan=False).encode() + b"\n"


@pytest.fixture(scope="module")
def parser() -> ContractParser:
    return ContractParser(CONTRACT_ROOT)


def _records(parser: ContractParser) -> tuple[SceneDefinition, SceneSegment]:
    header = parser.parse_line(_line(_definition())).value
    segment = parser.parse_line(_line(_segment())).value
    assert isinstance(header, SceneDefinition)
    assert isinstance(segment, SceneSegment)
    return header, segment


def test_segment_parses_to_immutable_models_and_materializes_static_grid(
    parser: ContractParser,
) -> None:
    header, segment = _records(parser)
    state = accept_segment(accept_header(ExecutionState(), header).state, segment).state

    assert state.segment == segment
    assert state.frame is not None
    assert state.frame.surface.x_coordinates_m == (0.0, 1.0)
    assert state.frame.surface.heights_m == ((0.0, 0.2), (0.3, 0.4))
    with pytest.raises(FrozenInstanceError):
        segment.sequence = 2  # type: ignore[misc]


def test_default_parser_uses_packaged_v2_schemas(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert isinstance(
        ContractParser().parse_line(_line(_definition())).value, SceneDefinition
    )


@pytest.mark.parametrize(
    ("line", "code"),
    [
        (b"{}", "framing"),
        (b"\xff\n", "encoding"),
        (b"[]\n", "record_type"),
        (b'{"type":"scene_segment","type":"x"}\n', "duplicate_key"),
        (b'{"type":NaN}\n', "invalid_number"),
    ],
)
def test_rejects_invalid_json_boundaries(
    parser: ContractParser, line: bytes, code: str
) -> None:
    with pytest.raises(ContractError, match=".") as caught:
        parser.parse_line(line)
    assert caught.value.code == code


def test_parser_rejects_non_adjacent_or_unordered_segment(
    parser: ContractParser,
) -> None:
    non_adjacent = _segment()
    non_adjacent["left_sequence"] = 7
    with pytest.raises(ContractError) as caught:
        parser.parse_line(_line(non_adjacent))
    assert caught.value.code == "segment_sequence"
    unordered = _segment()
    unordered["right"]["scenario"]["elapsed_s"] = 0.0
    unordered["right"]["scenario"]["surface_updated_at_s"] = 0.0
    with pytest.raises(ContractError) as caught:
        parser.parse_line(_line(unordered))
    assert caught.value.code == "segment_time"


def test_state_validates_dynamic_height_shape_against_definition(
    parser: ContractParser,
) -> None:
    header, _ = _records(parser)
    malformed = _segment()
    malformed["right"]["heights_m"] = [[0.0, 0.1], [0.2, 0.3], [0.4, 0.5]]
    segment = parser.parse_line(_line(malformed)).value
    assert isinstance(segment, SceneSegment)
    with pytest.raises(StateError) as caught:
        accept_segment(accept_header(ExecutionState(), header).state, segment)
    assert caught.value.code == "surface_shape"


def test_state_tracks_gap_disconnect_and_reconnect(parser: ContractParser) -> None:
    header, segment = _records(parser)
    first = accept_header(ExecutionState(), header)
    accepted = accept_segment(
        first.state, replace(segment, sequence=3, right_sequence=3, left_sequence=2)
    )

    assert accepted.sequence_gap == 2
    assert accepted.state.missing_sequences == 2
    disconnected = disconnect(accepted.state)
    reconnected = accept_header(disconnected.state, header)
    assert reconnected.event == "reconnected"
    assert reconnected.state.segment == accepted.state.segment


def test_state_rejects_non_increasing_segment_time(parser: ContractParser) -> None:
    header, segment = _records(parser)
    state = accept_segment(accept_header(ExecutionState(), header).state, segment).state
    candidate = replace(
        segment,
        sequence=2,
        left_sequence=1,
        right_sequence=2,
        right=replace(
            segment.right, scenario=replace(segment.right.scenario, elapsed_s=0.1)
        ),
    )
    with pytest.raises(StateError) as caught:
        accept_segment(state, candidate)
    assert caught.value.code == "simulation_time"
