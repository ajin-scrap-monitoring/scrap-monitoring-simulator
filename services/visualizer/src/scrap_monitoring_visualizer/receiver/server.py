"""Single-producer TCP receiver with strict connection boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace

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

from .framing import LineFramer, LineFramingError


@dataclass(frozen=True, slots=True)
class ReceiverSnapshot:
    connections_accepted: int = 0
    connections_rejected: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    partial_lines_discarded: int = 0
    last_error: str | None = None


class _ConnectionRejected(ValueError):
    pass


class SceneReceiver:
    def __init__(
        self,
        parser: ContractParser,
        *,
        header_timeout_s: float = 5.0,
        on_state: Callable[[ExecutionState], None] | None = None,
    ) -> None:
        if header_timeout_s <= 0:
            raise ValueError("header_timeout_s must be positive")
        self._parser = parser
        self._header_timeout_s = header_timeout_s
        self._on_state = on_state
        self._active = False
        self.state = ExecutionState()
        self.snapshot = ReceiverSnapshot()

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._active:
            self.snapshot = replace(
                self.snapshot,
                connections_rejected=self.snapshot.connections_rejected + 1,
                last_error="additional producer rejected",
            )
            writer.close()
            await writer.wait_closed()
            return
        self._active = True
        accepted_header = False
        framer = LineFramer()
        self.snapshot = replace(
            self.snapshot,
            connections_accepted=self.snapshot.connections_accepted + 1,
        )
        try:
            pending, framing_error = await asyncio.wait_for(
                self._read_header(reader, framer), timeout=self._header_timeout_s
            )
            accepted_header = True
            for raw_line in pending:
                self._process_frame(raw_line)
            if framing_error is not None:
                raise framing_error
            while data := await reader.read(65_536):
                try:
                    records = framer.feed(data)
                except LineFramingError as error:
                    for raw_line in error.completed_records:
                        self._process_frame(raw_line)
                    raise
                for raw_line in records:
                    self._process_frame(raw_line)
        except TimeoutError:
            self._connection_error("header timeout")
        except (
            ContractError,
            LineFramingError,
            StateError,
            _ConnectionRejected,
        ) as error:
            self._connection_error(str(error))
        finally:
            if framer.discard_partial():
                self.snapshot = replace(
                    self.snapshot,
                    partial_lines_discarded=self.snapshot.partial_lines_discarded + 1,
                )
            if accepted_header:
                self.state = disconnect(self.state).state
                if self._on_state is not None:
                    self._on_state(self.state)
            self._active = False
            writer.close()
            await writer.wait_closed()

    async def _read_header(
        self, reader: asyncio.StreamReader, framer: LineFramer
    ) -> tuple[tuple[bytes, ...], LineFramingError | None]:
        while data := await reader.read(65_536):
            framing_error = None
            try:
                records = framer.feed(data)
            except LineFramingError as error:
                records = error.completed_records
                framing_error = error
            if not records:
                if framing_error is not None:
                    raise framing_error
                continue
            parsed = self._parser.parse_line(records[0])
            if not isinstance(parsed.value, SceneDefinition):
                raise _ConnectionRejected("first record must be a stream header")
            self.state = accept_header(self.state, parsed.value).state
            self._record_acceptance()
            if self._on_state is not None:
                self._on_state(self.state)
            return records[1:], framing_error
        raise _ConnectionRejected("connection ended before a stream header")

    def _process_frame(self, raw_line: bytes) -> None:
        try:
            parsed = self._parser.parse_line(raw_line)
            if not isinstance(parsed.value, SceneFrame):
                raise StateError(
                    "record_type", "header is only valid as the first record"
                )
            self.state = accept_frame(self.state, parsed.value).state
        except (ContractError, StateError) as error:
            self.snapshot = replace(
                self.snapshot,
                records_rejected=self.snapshot.records_rejected + 1,
                last_error=str(error),
            )
            return
        self._record_acceptance()
        if self._on_state is not None:
            self._on_state(self.state)

    def _record_acceptance(self) -> None:
        self.snapshot = replace(
            self.snapshot,
            records_accepted=self.snapshot.records_accepted + 1,
        )

    def _connection_error(self, message: str) -> None:
        self.snapshot = replace(self.snapshot, last_error=message)
