"""Execution state transitions."""

from .machine import (
    ExecutionState,
    StateError,
    StateTransition,
    accept_frame,
    accept_header,
    disconnect,
)

__all__ = [
    "ExecutionState",
    "StateError",
    "StateTransition",
    "accept_header",
    "accept_frame",
    "disconnect",
]
