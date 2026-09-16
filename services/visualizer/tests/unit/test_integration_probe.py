from __future__ import annotations

import asyncio

import pytest

from scrap_monitoring_visualizer import integration_probe
from scrap_monitoring_visualizer.integration_probe import (
    _check_camera,
    _measure_unique_frames,
)


def test_integration_probe_reports_only_unique_jpeg_samples() -> None:
    assert _measure_unique_frames(
        [b"jpeg-1", b"jpeg-2", b"jpeg-3"],
        [1.0, 1.05, 1.1],
    ) == {
        "network_unique_frames": 3,
        "network_unique_fps": 20.0,
    }

    with pytest.raises(RuntimeError, match="repeated a JPEG"):
        _measure_unique_frames(
            [b"jpeg-1", b"jpeg-1"],
            [1.0, 1.05],
        )


def test_camera_probe_bounds_the_complete_websocket_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowWebSocket:
        def __init__(self) -> None:
            self.received = 0

        async def recv(self) -> str | bytes:
            await asyncio.sleep(0.02)
            self.received += 1
            if self.received == 1:
                return '{"type":"camera_stream_descriptor"}'
            return b"frame"

    class Connection:
        async def __aenter__(self) -> SlowWebSocket:
            return SlowWebSocket()

        async def __aexit__(
            self,
            exception_type: object,
            exception: object,
            traceback: object,
        ) -> None:
            del exception_type, exception, traceback

    monkeypatch.setattr(
        integration_probe,
        "connect",
        lambda *args, **kwargs: Connection(),
    )

    with pytest.raises(TimeoutError):
        asyncio.run(_check_camera("ws://camera.invalid", 0.03, sample_frames=2))
