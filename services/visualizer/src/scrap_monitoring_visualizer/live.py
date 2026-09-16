"""Live receiver, renderer and preview process lifetime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import uvicorn

from scrap_monitoring_visualizer.contracts import ContractParser
from scrap_monitoring_visualizer.limits import (
    DEFAULT_FRAME_HEIGHT,
    DEFAULT_FRAME_WIDTH,
)
from scrap_monitoring_visualizer.preview import (
    LatestFrameStore,
    create_preview_app,
)
from scrap_monitoring_visualizer.receiver import SceneReceiver
from scrap_monitoring_visualizer.rendering import RenderConfig
from scrap_monitoring_visualizer.rendering.worker import (
    LatestRenderWorker,
    RenderRequest,
)
from scrap_monitoring_visualizer.state import ExecutionState
from scrap_monitoring_visualizer.synthetic_camera import (
    SyntheticCameraConfig,
    SyntheticCameraPipeline,
    install_camera_routes,
)


@dataclass(frozen=True, slots=True)
class LiveConfig:
    tcp_host: str
    tcp_port: int
    http_host: str
    http_port: int
    width: int = DEFAULT_FRAME_WIDTH
    height: int = DEFAULT_FRAME_HEIGHT
    synthetic_camera: SyntheticCameraConfig | None = None

    def validate(self) -> None:
        if not self.tcp_host or not self.http_host:
            raise ValueError("listen hosts must not be empty")
        if not (1 <= self.tcp_port <= 65_535 and 1 <= self.http_port <= 65_535):
            raise ValueError("listen ports must be between 1 and 65535")
        if self.tcp_host == self.http_host and self.tcp_port == self.http_port:
            raise ValueError("TCP and HTTP endpoints must be different")
        RenderConfig.from_camera_config(
            width=self.width,
            height=self.height,
            camera_config=self.synthetic_camera,
        ).validate()
        if self.synthetic_camera is not None:
            self.synthetic_camera.validate()


class LiveCoordinator:
    def __init__(
        self,
        frames: LatestFrameStore,
        worker: LatestRenderWorker,
        render_config: RenderConfig,
        camera: SyntheticCameraPipeline | None = None,
    ) -> None:
        self._frames = frames
        self._worker = worker
        self._render_config = render_config
        self._camera = camera
        self._receiver: SceneReceiver | None = None
        self._state = ExecutionState()
        self._last_received_sequence: int | None = None
        self._last_valid_received_at: str | None = None

    def bind_receiver(self, receiver: SceneReceiver) -> None:
        self._receiver = receiver

    def state_changed(self, state: ExecutionState) -> None:
        self._state = state
        if self._camera is not None:
            self._camera.state_changed(state)
        frame = state.frame
        if frame is None:
            self._worker.invalidate()
            self._frames.clear()
            self._last_received_sequence = None
            self._last_valid_received_at = None
            return
        if frame.sequence != self._last_received_sequence:
            self._last_received_sequence = frame.sequence
            self._last_valid_received_at = datetime.now(UTC).isoformat()
        assert state.header is not None
        self._worker.submit(
            RenderRequest(
                header=state.header,
                frame=frame,
                config=self._render_config,
            )
        )

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
            "render_error": self._worker.last_error,
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
        self._worker.poll()
        if not self._worker.is_alive:
            raise RuntimeError("browser renderer process exited")
        if self._camera is not None:
            self._camera.poll()


async def run_live(config: LiveConfig) -> int:
    config.validate()
    frames = LatestFrameStore()
    worker = LatestRenderWorker(frames)
    worker.start()
    camera = (
        SyntheticCameraPipeline(config.synthetic_camera)
        if config.synthetic_camera is not None
        else None
    )
    if camera is not None:
        try:
            camera.start()
        except Exception:
            worker.close()
            raise
    render_config = RenderConfig.from_camera_config(
        width=config.width,
        height=config.height,
        camera_config=config.synthetic_camera,
    )
    coordinator = LiveCoordinator(
        frames,
        worker,
        render_config,
        camera,
    )
    receiver = SceneReceiver(ContractParser(), on_state=coordinator.state_changed)
    coordinator.bind_receiver(receiver)
    try:
        tcp_server = await asyncio.start_server(
            receiver.handle_client, config.tcp_host, config.tcp_port
        )
    except Exception:
        if camera is not None:
            camera.close()
        worker.close()
        raise
    app = create_preview_app(frames, coordinator.status)
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
            ws_max_size=4_096,
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
            try:
                try:
                    worker.poll()
                finally:
                    worker.close()
            finally:
                if camera is not None:
                    camera.close()
    if poll_error is not None:
        raise RuntimeError("render polling failed") from poll_error
    return 0
