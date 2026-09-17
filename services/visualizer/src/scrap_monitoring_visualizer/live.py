"""Live scene receiver and paired Browser presentation lifetime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import uvicorn

from scrap_monitoring_visualizer.contracts import ContractParser, SceneDefinition
from scrap_monitoring_visualizer.preview import create_preview_app
from scrap_monitoring_visualizer.receiver import SceneReceiver
from scrap_monitoring_visualizer.state import ExecutionState
from scrap_monitoring_visualizer.synthetic_camera import (
    SyntheticCameraConfig,
    SyntheticCameraPipeline,
    install_camera_routes,
)
from scrap_monitoring_visualizer.synthetic_camera.models import InterpolatedFrame
from scrap_monitoring_visualizer.visual_stream import (
    LatestVisualStore,
    install_visual_routes,
)


@dataclass(frozen=True, slots=True)
class LiveConfig:
    tcp_host: str
    tcp_port: int
    http_host: str
    http_port: int
    synthetic_camera: SyntheticCameraConfig | None = None

    def validate(self) -> None:
        if not self.tcp_host or not self.http_host:
            raise ValueError("listen hosts must not be empty")
        if not (1 <= self.tcp_port <= 65_535 and 1 <= self.http_port <= 65_535):
            raise ValueError("listen ports must be between 1 and 65535")
        if self.tcp_host == self.http_host and self.tcp_port == self.http_port:
            raise ValueError("TCP and HTTP endpoints must be different")
        if self.synthetic_camera is not None:
            self.synthetic_camera.validate()


class LiveCoordinator:
    def __init__(
        self,
        visual_store: LatestVisualStore,
        camera: SyntheticCameraPipeline | None = None,
    ) -> None:
        self._visual_store = visual_store
        self._camera = camera
        self._receiver: SceneReceiver | None = None
        self._state = ExecutionState()
        self._last_received_sequence: int | None = None
        self._last_valid_received_at: str | None = None

    def bind_receiver(self, receiver: SceneReceiver) -> None:
        self._receiver = receiver

    @property
    def definition(self) -> SceneDefinition | None:
        return self._state.header if self._state.connected else None

    def state_changed(self, state: ExecutionState) -> None:
        self._state = state
        if self._camera is not None:
            self._camera.state_changed(state)
        segment = state.segment
        if not state.connected or segment is None:
            self._visual_store.clear()
            self._last_received_sequence = None
            self._last_valid_received_at = None
            return
        if segment.sequence != self._last_received_sequence:
            self._last_received_sequence = segment.sequence
            self._last_valid_received_at = datetime.now(UTC).isoformat()

    def status(self) -> dict[str, Any]:
        frame = self._state.frame
        receiver_snapshot = (
            self._receiver.snapshot if self._receiver is not None else None
        )
        status: dict[str, Any] = {
            "connected": self._state.connected,
            "run_id": self._state.header.run_id
            if self._state.header is not None
            else None,
            "received_sequence": frame.sequence if frame is not None else None,
            "missing_sequences": self._state.missing_sequences,
            "connection_index": self._state.connection_index,
            "last_valid_received_at": self._last_valid_received_at,
            "records_accepted": (
                receiver_snapshot.records_accepted
                if receiver_snapshot is not None
                else 0
            ),
            "records_rejected": (
                receiver_snapshot.records_rejected
                if receiver_snapshot is not None
                else 0
            ),
            "scene": (
                {
                    "sequence": frame.sequence,
                    "elapsed_s": frame.scenario.elapsed_s,
                    "surface_fill_ratio": frame.scenario.surface_fill_ratio,
                    "surface_volume_m3": frame.scenario.surface_volume_m3,
                    "phase": frame.scenario.phase,
                    "cycle_index": frame.scenario.cycle_index,
                    "current_inlet_index": frame.scenario.current_inlet_index,
                }
                if frame is not None
                else None
            ),
        }
        status["synthetic_camera"] = (
            dict(self._camera.status())
            if self._camera is not None
            else {"camera_enabled": False}
        )
        return status

    def poll_renderers(self) -> None:
        if self._camera is not None:
            self._camera.poll()


async def run_live(config: LiveConfig) -> int:
    config.validate()
    visual_store = LatestVisualStore()

    def publish_visual(frame: InterpolatedFrame, jpeg: bytes) -> None:
        visual_store.publish(frame, jpeg)

    camera = (
        SyntheticCameraPipeline(
            config.synthetic_camera,
            on_published=publish_visual,
        )
        if config.synthetic_camera is not None
        else None
    )
    if camera is not None:
        camera.start()
    coordinator = LiveCoordinator(visual_store, camera)
    receiver = SceneReceiver(ContractParser(), on_state=coordinator.state_changed)
    coordinator.bind_receiver(receiver)
    try:
        tcp_server = await asyncio.start_server(
            receiver.handle_client, config.tcp_host, config.tcp_port
        )
    except Exception:
        if camera is not None:
            camera.close()
        raise
    app = create_preview_app(coordinator.status)
    if config.synthetic_camera is not None:
        install_visual_routes(
            app,
            visual_store,
            lambda: coordinator.definition,
            config.synthetic_camera,
        )
    if camera is not None:
        install_camera_routes(
            app,
            camera.store,
            camera.status,
            camera.config.video.fps,
        )
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.http_host,
            port=config.http_port,
            access_log=False,
            log_level="info",
            timeout_keep_alive=5,
            ws_max_queue=1,
            ws_max_size=4_194_304,
            ws_per_message_deflate=False,
            ws_ping_interval=0.5,
            ws_ping_timeout=2.0,
        )
    )
    poll_error: Exception | None = None

    async def poll_renderer() -> None:
        nonlocal poll_error
        try:
            while not uvicorn_server.should_exit:
                coordinator.poll_renderers()
                await asyncio.sleep(0.005)
        except Exception as error:
            poll_error = error
            uvicorn_server.should_exit = True

    poll_task = asyncio.create_task(poll_renderer())
    try:
        async with tcp_server:
            await uvicorn_server.serve()
    finally:
        uvicorn_server.should_exit = True
        tcp_server.close()
        await tcp_server.wait_closed()
        try:
            await poll_task
        finally:
            if camera is not None:
                camera.close()
    if poll_error is not None:
        raise RuntimeError("render polling failed") from poll_error
    return 0
