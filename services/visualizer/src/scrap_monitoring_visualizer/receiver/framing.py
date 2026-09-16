"""Bounded LF record assembly independent of TCP packet boundaries."""

from scrap_monitoring_visualizer.contracts.parser import MAX_RECORD_BYTES


class LineFramingError(ValueError):
    """A connection-level line framing error."""

    def __init__(self, message: str, completed_records: tuple[bytes, ...] = ()) -> None:
        super().__init__(message)
        self.completed_records = completed_records


class LineFramer:
    def __init__(self, max_record_bytes: int = MAX_RECORD_BYTES) -> None:
        if max_record_bytes < 2:
            raise ValueError("max_record_bytes must be at least 2")
        self._max_record_bytes = max_record_bytes
        self._buffer = bytearray()

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, chunk: bytes) -> tuple[bytes, ...]:
        self._buffer.extend(chunk)
        records: list[bytes] = []
        while True:
            line_end = self._buffer.find(b"\n")
            if line_end < 0:
                if len(self._buffer) >= self._max_record_bytes:
                    self._buffer.clear()
                    raise LineFramingError("record byte limit exceeded", tuple(records))
                return tuple(records)
            record_size = line_end + 1
            if record_size > self._max_record_bytes:
                self._buffer.clear()
                raise LineFramingError("record byte limit exceeded", tuple(records))
            records.append(bytes(self._buffer[:record_size]))
            del self._buffer[:record_size]

    def discard_partial(self) -> bool:
        had_partial = bool(self._buffer)
        self._buffer.clear()
        return had_partial
