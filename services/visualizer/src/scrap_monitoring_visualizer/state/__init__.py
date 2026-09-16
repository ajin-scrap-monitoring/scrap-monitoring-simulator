"""Execution state transitions."""

from .machine import (
    ExecutionState,
    StateError,
    StateTransition,
    accept_header,
    accept_segment,
    disconnect,
)

__all__ = [
    "ExecutionState",
    "StateError",
    "StateTransition",
    "accept_header",
    "accept_segment",
    "disconnect",
]
