"""Thread-safe latest-only MJPEG frame storage."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class JpegSnapshot:
    jpeg: bytes
    revision: int
    sequence: int
    elapsed_s: float
    render_backend: str


class LatestJpegStore:
    def __init__(self, max_frame_bytes: int = 4_194_304) -> None:
        if max_frame_bytes <= 0:
            raise ValueError("maximum JPEG size must be positive")
        self._max_frame_bytes = max_frame_bytes
        self._lock = Lock()
        self._revision = 0
        self._frame: JpegSnapshot | None = None

    @property
    def max_frame_bytes(self) -> int:
        return self._max_frame_bytes

    def publish(
        self,
        jpeg: bytes,
        *,
        sequence: int,
        elapsed_s: float,
        render_backend: str,
    ) -> JpegSnapshot:
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("camera frame must be a complete JPEG image")
        if len(jpeg) > self._max_frame_bytes:
            raise ValueError("camera frame exceeds the configured byte limit")
        with self._lock:
            self._revision += 1
            self._frame = JpegSnapshot(
                jpeg=bytes(jpeg),
                revision=self._revision,
                sequence=sequence,
                elapsed_s=elapsed_s,
                render_backend=render_backend,
            )
            return self._frame

    def clear(self) -> None:
        with self._lock:
            self._revision += 1
            self._frame = None

    def get(self) -> JpegSnapshot | None:
        with self._lock:
            return self._frame

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision
