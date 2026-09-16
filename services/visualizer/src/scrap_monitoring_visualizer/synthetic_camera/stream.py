"""Latest-only MJPEG WebSocket delivery for an edge camera bridge."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .models import StreamDescriptor
from .store import LatestJpegStore

CameraStatusProvider = Callable[[], Mapping[str, object]]
CAMERA_STREAM_PATH = "/camera/v1/stream"
CAMERA_STATUS_PATH = "/camera/v1/status"
_MAX_CAMERA_CLIENTS = 4


class _ConnectionGate:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            if self._active >= self._limit:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            self._active -= 1


async def _wait_for_client_end(websocket: WebSocket) -> tuple[int, str] | None:
    try:
        message = await websocket.receive()
    except RuntimeError, WebSocketDisconnect:
        return None
    if message["type"] == "websocket.disconnect":
        return None
    return 1003, "camera stream is send-only"


def install_camera_routes(
    app: FastAPI,
    store: LatestJpegStore,
    status_provider: CameraStatusProvider,
    fps: int = 30,
) -> None:
    if fps <= 0 or fps > 60:
        raise ValueError("camera stream fps must be between 1 and 60")
    connection_gate = _ConnectionGate(_MAX_CAMERA_CLIENTS)
    descriptor = StreamDescriptor(
        type="camera_stream_descriptor",
        version=1,
        format="MJPEG",
        width=0,
        height=0,
        fps=fps,
        max_frame_bytes=store.max_frame_bytes,
    )

    @app.get(CAMERA_STATUS_PATH)
    async def camera_status() -> JSONResponse:
        payload = dict(status_provider())
        payload["camera_stream_delivery"] = "revision-only"
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @app.websocket(CAMERA_STREAM_PATH)
    async def camera_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        if not await connection_gate.acquire():
            await websocket.close(code=1013, reason="camera client limit exceeded")
            return
        try:
            status = status_provider()
            width = status.get("camera_width")
            height = status.get("camera_height")
            if not isinstance(width, int) or not isinstance(height, int):
                await websocket.close(code=1011, reason="camera dimensions unavailable")
                return
            connection_descriptor = StreamDescriptor(
                type=descriptor.type,
                version=descriptor.version,
                format=descriptor.format,
                width=width,
                height=height,
                fps=descriptor.fps,
                max_frame_bytes=descriptor.max_frame_bytes,
            )
            await websocket.send_text(
                json.dumps(asdict(connection_descriptor), separators=(",", ":"))
            )
            client_end = asyncio.create_task(_wait_for_client_end(websocket))
            try:
                poll_interval_s = min(0.01, 1.0 / (fps * 2))
                clock = asyncio.get_running_loop()
                next_poll_at = clock.time()
                sent_frame = False
                sent_revision: int | None = None
                try:
                    while True:
                        snapshot = store.get()
                        if snapshot is not None and snapshot.revision != sent_revision:
                            async with asyncio.timeout(1.0):
                                await websocket.send_bytes(snapshot.jpeg)
                            sent_frame = True
                            sent_revision = snapshot.revision
                        elif sent_frame:
                            if snapshot is None:
                                await websocket.close(
                                    code=1012,
                                    reason="camera source unavailable",
                                )
                                return
                        next_poll_at += poll_interval_s
                        now = clock.time()
                        if next_poll_at < now - poll_interval_s:
                            next_poll_at = now
                        completed, _ = await asyncio.wait(
                            (client_end,),
                            timeout=max(0.0, next_poll_at - now),
                        )
                        if completed:
                            close_request = client_end.result()
                            if close_request is not None:
                                await websocket.close(
                                    code=close_request[0],
                                    reason=close_request[1],
                                )
                            return
                except TimeoutError, WebSocketDisconnect, RuntimeError:
                    try:
                        await websocket.close()
                    except RuntimeError:
                        pass
            finally:
                client_end.cancel()
                await asyncio.gather(client_end, return_exceptions=True)
        finally:
            await connection_gate.release()
