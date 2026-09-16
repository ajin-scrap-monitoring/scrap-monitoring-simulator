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
    SceneFrame,
)
from scrap_monitoring_visualizer.state import (
    ExecutionState,
    StateError,
    accept_frame,
    accept_header,
    disconnect,
)

CONTRACT_ROOT = Path("../contracts/scene/v1")
FIXTURE_PATH = CONTRACT_ROOT / "fixtures/scene.v1.jsonl"


@pytest.fixture(scope="module")
def parser() -> ContractParser:
    return ContractParser(CONTRACT_ROOT)


@pytest.fixture
def documents() -> tuple[dict[str, Any], dict[str, Any]]:
    header_line, frame_line = FIXTURE_PATH.read_bytes().splitlines()
    return json.loads(header_line), json.loads(frame_line)


def _line(document: dict[str, Any]) -> bytes:
    return json.dumps(document, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def _records(parser: ContractParser) -> tuple[SceneDefinition, SceneFrame]:
    header_line, frame_line = FIXTURE_PATH.read_bytes().splitlines(keepends=True)
    header = parser.parse_line(header_line)
    frame = parser.parse_line(frame_line)
    assert isinstance(header.value, SceneDefinition)
    assert isinstance(frame.value, SceneFrame)
    return header.value, frame.value


def test_fixture_parses_to_immutable_models(parser: ContractParser) -> None:
    lines = FIXTURE_PATH.read_bytes().splitlines(keepends=True)
    parsed = tuple(parser.parse_line(line) for line in lines)

    assert parsed[0].raw_line == lines[0]
    assert isinstance(parsed[0].value, SceneDefinition)
    assert isinstance(parsed[1].value, SceneFrame)
    assert parsed[1].value.surface.heights_m == ((0.0, 0.1), (0.2, 0.3))
    with pytest.raises(FrozenInstanceError):
        parsed[1].value.sequence = 2  # type: ignore[misc]


def test_default_parser_uses_packaged_schemas(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header_line = FIXTURE_PATH.resolve().read_bytes().splitlines(True)[0]
    monkeypatch.chdir(tmp_path)
    parser = ContractParser()
    parsed = parser.parse_line(header_line)

    assert isinstance(parsed.value, SceneDefinition)


@pytest.mark.parametrize(
    ("line", "code"),
    [
        (b"{}", "framing"),
        (b"\xff\n", "encoding"),
        (b"[]\n", "record_type"),
        (b'{"type":"missing"}\n', "record_type"),
        (b'{"type":"scene_frame","type":"x"}\n', "duplicate_key"),
        (b'{"type":NaN}\n', "invalid_number"),
        (b'{"type":1e999}\n', "invalid_number"),
    ],
)
def test_rejects_invalid_json_boundaries(
    parser: ContractParser, line: bytes, code: str
) -> None:
    with pytest.raises(ContractError, match=".") as caught:
        parser.parse_line(line)
    assert caught.value.code == code


def test_schema_rejects_unknown_and_missing_fields(
    parser: ContractParser, documents: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    _, frame = documents
    del frame["sequence"]
    frame["unexpected"] = True

    with pytest.raises(ContractError) as caught:
        parser.parse_line(_line(frame))
    assert caught.value.code == "schema"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda header: header["scene"].update(floor_z_m=1.0, top_z_m=1.0),
            "scene_height",
        ),
        (lambda header: header["scene"]["sensors"][0].update(u0=[2, 0, 0]), "sensor"),
        (lambda header: header["scene"]["sensors"][0].update(u90=[0, 0, -1]), "sensor"),
        (lambda header: header["scene"].update(inlet_positions_xy_m=[[2, 2]]), "inlet"),
        (
            lambda header: header["scene"].update(
                boundary_xy_m=[[0, 0], [1, 1], [0, 1], [1, 0]]
            ),
            "boundary",
        ),
    ],
)
def test_header_semantics(
    parser: ContractParser,
    documents: tuple[dict[str, Any], dict[str, Any]],
    mutation: Any,
    code: str,
) -> None:
    header, _ = documents
    mutation(header)
    with pytest.raises(ContractError) as caught:
        parser.parse_line(_line(header))
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda frame: frame["surface"].update(x_coordinates_m=[1, 0]),
            "surface_coordinates",
        ),
        (
            lambda frame: frame["surface"].update(x_coordinates_m=[0, 0.5, 1]),
            "surface_shape",
        ),
        (
            lambda frame: frame["scenario"].update(surface_updated_at_s=2),
            "scenario_time",
        ),
        (
            lambda frame: frame["scenario"].update(phase_duration_s=11),
            "scenario_time",
        ),
    ],
)
def test_frame_semantics(
    parser: ContractParser,
    documents: tuple[dict[str, Any], dict[str, Any]],
    mutation: Any,
    code: str,
) -> None:
    _, frame = documents
    mutation(frame)
    with pytest.raises(ContractError) as caught:
        parser.parse_line(_line(frame))
    assert caught.value.code == code


def test_state_tracks_gap_disconnect_and_reconnect(parser: ContractParser) -> None:
    header, frame = _records(parser)
    first = accept_header(ExecutionState(), header)
    accepted = accept_frame(first.state, replace(frame, sequence=3))

    assert first.event == "new_run"
    assert accepted.sequence_gap == 2
    assert accepted.state.missing_sequences == 2
    disconnected = disconnect(accepted.state)
    assert disconnected.state.connected is False
    reconnected = accept_header(disconnected.state, header)
    assert reconnected.event == "reconnected"
    assert reconnected.state.frame == accepted.state.frame
    assert reconnected.state.connection_index == 2


@pytest.mark.parametrize(
    ("sequence", "elapsed_s", "code"),
    [
        (1, 1.0, "duplicate_sequence"),
        (0, 1.0, "old_sequence"),
        (2, 0.5, "simulation_time"),
    ],
)
def test_state_rejects_non_monotonic_frames(
    parser: ContractParser, sequence: int, elapsed_s: float, code: str
) -> None:
    header, frame = _records(parser)
    state = accept_frame(accept_header(ExecutionState(), header).state, frame).state
    candidate = replace(
        frame,
        sequence=sequence,
        scenario=replace(frame.scenario, elapsed_s=elapsed_s),
    )
    with pytest.raises(StateError) as caught:
        accept_frame(state, candidate)
    assert caught.value.code == code


def test_same_run_requires_identical_static_header(parser: ContractParser) -> None:
    header, _ = _records(parser)
    state = accept_header(ExecutionState(), header).state
    changed = replace(header, environment_id="different")

    with pytest.raises(StateError) as caught:
        accept_header(disconnect(state).state, changed)
    assert caught.value.code == "run_identity"
    assert state.header == header


def test_new_run_clears_previous_frame(parser: ContractParser) -> None:
    header, frame = _records(parser)
    state = accept_frame(accept_header(ExecutionState(), header).state, frame).state
    transition = accept_header(state, replace(header, run_id="new-run"))

    assert transition.event == "new_run"
    assert transition.state.frame is None
    assert transition.state.missing_sequences == 0


@pytest.mark.parametrize(
    ("scenario_changes", "surface_changes", "code"),
    [
        ({"current_inlet_index": None}, {}, "inlet"),
        ({"current_inlet_index": 1}, {}, "inlet"),
        ({"phase": "collecting", "current_inlet_index": 0}, {}, "inlet"),
        ({}, {"heights_m": ((0.0, 1.1), (0.2, 0.3))}, "surface_height"),
        ({}, {"x_coordinates_m": (0.1, 1.0)}, "surface_extent"),
    ],
)
def test_state_checks_scene_dependent_constraints(
    parser: ContractParser,
    scenario_changes: dict[str, Any],
    surface_changes: dict[str, Any],
    code: str,
) -> None:
    header, frame = _records(parser)
    candidate = replace(
        frame,
        scenario=replace(frame.scenario, **scenario_changes),
        surface=replace(frame.surface, **surface_changes),
    )
    state = accept_header(ExecutionState(), header).state
    with pytest.raises(StateError) as caught:
        accept_frame(state, candidate)
    assert caught.value.code == code
