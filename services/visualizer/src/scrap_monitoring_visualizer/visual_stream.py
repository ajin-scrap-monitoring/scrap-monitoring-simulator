"""Paired Browser visual frames and latest-only WebSocket delivery."""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Lock

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from scrap_monitoring_visualizer.contracts.models import SceneDefinition
from scrap_monitoring_visualizer.synthetic_camera.models import (
    InterpolatedFrame,
    SyntheticCameraConfig,
)

VISUAL_STREAM_PATH = "/visual/v1/stream"
_MAX_VISUAL_CLIENTS = 1


@dataclass(frozen=True, slots=True)
class VisualSnapshot:
    jpeg: bytes
    target_id: int
    target_elapsed_s: float
    left_sequence: int
    right_sequence: int
    alpha: float
    heights_m: tuple[tuple[float, ...], ...]
    shared: Mapping[str, object]


class LatestVisualStore:
    """Stores one camera and WebGL pair as an atomic Browser presentation unit."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot: VisualSnapshot | None = None

    def publish(self, frame: InterpolatedFrame, jpeg: bytes) -> VisualSnapshot:
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("visual frame must contain a complete JPEG")
        scenario = frame.frame.scenario
        snapshot = VisualSnapshot(
            jpeg=bytes(jpeg),
            target_id=frame.target_id,
            target_elapsed_s=frame.frame.scenario.elapsed_s,
            left_sequence=frame.left_sequence,
            right_sequence=frame.right_sequence,
            alpha=frame.alpha,
            heights_m=frame.frame.surface.heights_m,
            shared={
                "cycle_index": scenario.cycle_index,
                "phase": scenario.phase,
                "target_fill_ratio": scenario.target_fill_ratio,
                "surface_fill_ratio": scenario.surface_fill_ratio,
                "surface_volume_m3": scenario.surface_volume_m3,
                "current_inlet_index": scenario.current_inlet_index,
            },
        )
        with self._lock:
            current = self._snapshot
            if current is not None and snapshot.target_id <= current.target_id:
                return current
            self._snapshot = snapshot
            return snapshot

    def clear(self) -> None:
        with self._lock:
            self._snapshot = None

    def get(self) -> VisualSnapshot | None:
        with self._lock:
            return self._snapshot


def _descriptor(
    definition: SceneDefinition, camera_config: SyntheticCameraConfig
) -> dict[str, object]:
    scene = definition.scene
    return {
        "type": "visual_stream_descriptor",
        "version": 1,
        "fps": 30,
        "model": {
            "boundary_xy_m": scene.boundary_xy_m,
            "floor_z_m": scene.floor_z_m,
            "top_z_m": scene.top_z_m,
            "inlet_positions_xy_m": scene.inlet_positions_xy_m,
            "surface": {
                "x_coordinates_m": scene.surface.x_coordinates_m,
                "y_coordinates_m": scene.surface.y_coordinates_m,
            },
            "palette": {
                "background": camera_config.background_color,
                "wall": camera_config.wall_material.color,
                "scrap": camera_config.scrap_material.color,
                "guide": camera_config.chute_material.color,
            },
        },
    }


def encode_visual_snapshot(snapshot: VisualSnapshot) -> bytes:
    heights = tuple(value for row in snapshot.heights_m for value in row)
    metadata = {
        "target_id": snapshot.target_id,
        "target_elapsed_s": snapshot.target_elapsed_s,
        "left_sequence": snapshot.left_sequence,
        "right_sequence": snapshot.right_sequence,
        "alpha": snapshot.alpha,
        "height_count": len(heights),
        "jpeg_bytes": len(snapshot.jpeg),
        "shared": dict(snapshot.shared),
    }
    header = json.dumps(metadata, separators=(",", ":"), allow_nan=False).encode()
    return b"".join(
        (
            struct.pack(">I", len(header)),
            header,
            struct.pack(f"<{len(heights)}f", *heights),
            snapshot.jpeg,
        )
    )


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
    return 1003, "visual stream is send-only"


def install_visual_routes(
    app: FastAPI,
    store: LatestVisualStore,
    definition_provider: Callable[[], SceneDefinition | None],
    camera_config: SyntheticCameraConfig,
    *,
    fps: int = 30,
) -> None:
    if fps != 30:
        raise ValueError("visual stream FPS must be 30")
    connection_gate = _ConnectionGate(_MAX_VISUAL_CLIENTS)

    @app.websocket(VISUAL_STREAM_PATH)
    async def visual_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        if not await connection_gate.acquire():
            await websocket.close(code=1013, reason="visual client limit exceeded")
            return
        try:
            definition = definition_provider()
            if definition is None:
                await websocket.close(code=1013, reason="visual definition unavailable")
                return
            await websocket.send_text(
                json.dumps(
                    _descriptor(definition, camera_config), separators=(",", ":")
                )
            )
            client_end = asyncio.create_task(_wait_for_client_end(websocket))
            try:
                loop = asyncio.get_running_loop()
                sent_target_id: int | None = None
                next_poll_at = loop.time()
                while True:
                    snapshot = store.get()
                    if snapshot is not None and snapshot.target_id != sent_target_id:
                        async with asyncio.timeout(1.0):
                            await websocket.send_bytes(encode_visual_snapshot(snapshot))
                        sent_target_id = snapshot.target_id
                    next_poll_at += min(0.01, 1.0 / (fps * 2))
                    now = loop.time()
                    if next_poll_at < now - 1.0 / fps:
                        next_poll_at = now
                    completed, _ = await asyncio.wait(
                        (client_end,),
                        timeout=max(0.0, next_poll_at - now),
                    )
                    if completed:
                        close_request = client_end.result()
                        if close_request is not None:
                            await websocket.close(
                                code=close_request[0], reason=close_request[1]
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
