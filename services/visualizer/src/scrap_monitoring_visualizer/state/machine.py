"""Pure run, connection and frame state transitions."""

from dataclasses import dataclass, replace
from typing import Literal

from scrap_monitoring_visualizer.contracts.models import SceneDefinition, SceneFrame


class StateError(ValueError):
    """A rejected state transition with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExecutionState:
    header: SceneDefinition | None = None
    frame: SceneFrame | None = None
    connected: bool = False
    connection_index: int = 0
    missing_sequences: int = 0


@dataclass(frozen=True, slots=True)
class StateTransition:
    state: ExecutionState
    event: Literal["new_run", "reconnected", "frame", "disconnected"]
    sequence_gap: int = 0


def accept_header(state: ExecutionState, header: SceneDefinition) -> StateTransition:
    if state.header is not None and state.header.run_id == header.run_id:
        if state.header != header:
            raise StateError(
                "run_identity", "same run_id has different static header data"
            )
        return StateTransition(
            state=replace(
                state,
                connected=True,
                connection_index=state.connection_index + 1,
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


def accept_frame(state: ExecutionState, frame: SceneFrame) -> StateTransition:
    header = state.header
    if header is None:
        raise StateError("header_required", "frame requires an accepted header")
    if not state.connected:
        raise StateError("connection", "frame requires an active connection")
    if frame.run_id != header.run_id:
        raise StateError("run_id", "frame run_id does not match the header")
    previous = state.frame
    previous_sequence = previous.sequence if previous is not None else 0
    if frame.sequence <= previous_sequence:
        code = (
            "duplicate_sequence"
            if frame.sequence == previous_sequence
            else "old_sequence"
        )
        raise StateError(code, "frame sequence must increase")
    if previous is not None and frame.scenario.elapsed_s < previous.scenario.elapsed_s:
        raise StateError("simulation_time", "simulation time must not decrease")
    scenario = frame.scenario
    inlet_count = len(header.scene.inlet_positions_xy_m)
    if scenario.phase == "filling":
        if scenario.current_inlet_index is None:
            raise StateError("inlet", "filling phase requires current_inlet_index")
        if scenario.current_inlet_index >= inlet_count:
            raise StateError("inlet", "current_inlet_index is out of range")
    elif scenario.current_inlet_index is not None:
        raise StateError("inlet", "collecting phase requires a null inlet index")
    heights = (height for row in frame.surface.heights_m for height in row)
    if any(
        height < header.scene.floor_z_m or height > header.scene.top_z_m
        for height in heights
    ):
        raise StateError("surface_height", "surface height is outside scene bounds")
    boundary_x = tuple(point[0] for point in header.scene.boundary_xy_m)
    boundary_y = tuple(point[1] for point in header.scene.boundary_xy_m)
    surface = frame.surface
    if not (
        surface.x_coordinates_m[0] <= min(boundary_x)
        and surface.x_coordinates_m[-1] >= max(boundary_x)
        and surface.y_coordinates_m[0] <= min(boundary_y)
        and surface.y_coordinates_m[-1] >= max(boundary_y)
    ):
        raise StateError("surface_extent", "surface grid must cover the scene boundary")
    sequence_gap = frame.sequence - previous_sequence - 1
    return StateTransition(
        state=replace(
            state,
            frame=frame,
            missing_sequences=state.missing_sequences + sequence_gap,
        ),
        event="frame",
        sequence_gap=sequence_gap,
    )


def disconnect(state: ExecutionState) -> StateTransition:
    return StateTransition(state=replace(state, connected=False), event="disconnected")
