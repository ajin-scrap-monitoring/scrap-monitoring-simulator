"""Pure run, connection and latest scene segment state transitions."""

from dataclasses import dataclass, replace
from typing import Literal

from scrap_monitoring_visualizer.contracts.models import (
    SceneDefinition,
    SceneFrame,
    SceneKeyframe,
    SceneSegment,
    materialize_keyframe,
)


class StateError(ValueError):
    """A rejected state transition with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExecutionState:
    header: SceneDefinition | None = None
    segment: SceneSegment | None = None
    connected: bool = False
    connection_index: int = 0
    missing_sequences: int = 0

    @property
    def frame(self) -> SceneFrame | None:
        if self.header is None or self.segment is None:
            return None
        return materialize_keyframe(
            self.header,
            self.segment.right,
            self.segment.right_sequence,
            self.segment.run_id,
        )


@dataclass(frozen=True, slots=True)
class StateTransition:
    state: ExecutionState
    event: Literal["new_run", "reconnected", "segment", "disconnected"]
    sequence_gap: int = 0


def accept_header(state: ExecutionState, header: SceneDefinition) -> StateTransition:
    _validate_static_grid(header)
    if state.header is not None and state.header.run_id == header.run_id:
        if state.header != header:
            raise StateError(
                "run_identity", "same run_id has different static header data"
            )
        return StateTransition(
            state=replace(
                state, connected=True, connection_index=state.connection_index + 1
            ),
            event="reconnected",
        )
    return StateTransition(
        state=ExecutionState(
            header=header,
            connected=True,
            connection_index=state.connection_index + 1,
        ),
        event="new_run",
    )


def accept_segment(state: ExecutionState, segment: SceneSegment) -> StateTransition:
    header = state.header
    if header is None:
        raise StateError("header_required", "segment requires an accepted header")
    if not state.connected:
        raise StateError("connection", "segment requires an active connection")
    if segment.run_id != header.run_id:
        raise StateError("run_id", "segment run_id does not match the header")
    previous = state.segment
    previous_sequence = previous.sequence if previous is not None else 0
    if segment.sequence <= previous_sequence:
        code = (
            "duplicate_sequence"
            if segment.sequence == previous_sequence
            else "old_sequence"
        )
        raise StateError(code, "segment sequence must increase")
    if (
        previous is not None
        and segment.right.scenario.elapsed_s <= previous.right.scenario.elapsed_s
    ):
        raise StateError("simulation_time", "simulation time must increase")
    _validate_keyframe(header, segment.left)
    _validate_keyframe(header, segment.right)
    sequence_gap = segment.sequence - previous_sequence - 1
    return StateTransition(
        state=replace(
            state,
            segment=segment,
            missing_sequences=state.missing_sequences + sequence_gap,
        ),
        event="segment",
        sequence_gap=sequence_gap,
    )


def _validate_static_grid(header: SceneDefinition) -> None:
    boundary_x = tuple(point[0] for point in header.scene.boundary_xy_m)
    boundary_y = tuple(point[1] for point in header.scene.boundary_xy_m)
    grid = header.scene.surface
    if not (
        grid.x_coordinates_m[0] <= min(boundary_x)
        and grid.x_coordinates_m[-1] >= max(boundary_x)
        and grid.y_coordinates_m[0] <= min(boundary_y)
        and grid.y_coordinates_m[-1] >= max(boundary_y)
    ):
        raise StateError("surface_extent", "surface grid must cover the scene boundary")


def _validate_keyframe(header: SceneDefinition, keyframe: SceneKeyframe) -> None:
    scenario = keyframe.scenario
    inlet_count = len(header.scene.inlet_positions_xy_m)
    if scenario.phase == "filling":
        if scenario.current_inlet_index is None:
            raise StateError("inlet", "filling phase requires current_inlet_index")
        if scenario.current_inlet_index >= inlet_count:
            raise StateError("inlet", "current_inlet_index is out of range")
    elif scenario.current_inlet_index is not None:
        raise StateError("inlet", "collecting phase requires a null inlet index")
    grid = header.scene.surface
    if len(keyframe.heights_m) != len(grid.y_coordinates_m) or any(
        len(row) != len(grid.x_coordinates_m) for row in keyframe.heights_m
    ):
        raise StateError("surface_shape", "heights shape must match the static grid")
    if any(
        height < header.scene.floor_z_m or height > header.scene.top_z_m
        for row in keyframe.heights_m
        for height in row
    ):
        raise StateError("surface_height", "surface height is outside scene bounds")


def disconnect(state: ExecutionState) -> StateTransition:
    return StateTransition(state=replace(state, connected=False), event="disconnected")
