"""Live state adapter for interpolation and latest-only camera rendering."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from scrap_monitoring_visualizer.contracts.models import SceneDefinition, SceneFrame
from scrap_monitoring_visualizer.state import ExecutionState

from .interpolation import exact_frame
from .models import FrameTarget, InterpolatedFrame, SyntheticCameraConfig
from .scheduler import materialize_target, schedule_segment
from .store import LatestJpegStore
from .worker import (
    CameraRenderOutcome,
    CameraRenderRequest,
    LatestSyntheticRenderWorker,
)


class CameraWorker(Protocol):
    last_error: str | None
    replaced_pending: int

    @property
    def is_alive(self) -> bool: ...

    def start(self) -> None: ...

    def submit(self, request: CameraRenderRequest) -> None: ...

    def invalidate(self) -> None: ...

    def poll(self) -> CameraRenderOutcome | None: ...

    def close(self, timeout_s: float = 5.0) -> None: ...


@dataclass(frozen=True, slots=True)
class _ScheduledSegment:
    header: SceneDefinition
    left: SceneFrame
    right: SceneFrame
    targets: tuple[FrameTarget, ...]
    started_at: float
    origin_elapsed_s: float


class SyntheticCameraPipeline:
    def __init__(
        self,
        config: SyntheticCameraConfig,
        store: LatestJpegStore | None = None,
        *,
        worker: CameraWorker | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        config.validate()
        self.config = config
        self.store = store or LatestJpegStore(config.video.max_frame_bytes)
        if self.store.max_frame_bytes != config.video.max_frame_bytes:
            raise ValueError("camera store and profile byte limits must match")
        self._worker = worker or LatestSyntheticRenderWorker()
        self._clock = clock
        self._started = False
        self._closed = False
        self._run_id: str | None = None
        self._last_frame: SceneFrame | None = None
        self._segment: _ScheduledSegment | None = None
        self._next_frame_index = 0
        self._submitted_frames = 0
        self._rendered_frames = 0
        self._last_mode: str | None = None
        self._last_reason: str | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("camera pipeline is closed")
        if self._started:
            return
        self._worker.start()
        self._started = True

    def _reset_run(self, run_id: str | None) -> None:
        self._worker.invalidate()
        self.store.clear()
        self._run_id = run_id
        self._last_frame = None
        self._segment = None
        self._next_frame_index = 0
        self._last_mode = None
        self._last_reason = None
        self._last_error = None

    def _submit(self, header: SceneDefinition, frame: InterpolatedFrame) -> None:
        self._worker.submit(
            CameraRenderRequest(header=header, frame=frame, config=self.config)
        )
        self._submitted_frames += 1
        self._last_mode = frame.mode
        self._last_reason = frame.reason

    def state_changed(self, state: ExecutionState) -> None:
        if self._closed:
            return
        header = state.header
        run_id = header.run_id if header is not None else None
        if run_id != self._run_id:
            self._reset_run(run_id)
        if not state.connected:
            self._worker.invalidate()
            self.store.clear()
            self._segment = None
            self._next_frame_index = 0
            return
        frame = state.frame
        if header is None or frame is None:
            return
        previous = self._last_frame
        if previous is not None and frame.sequence == previous.sequence:
            return
        if previous is None:
            self._submit(header, exact_frame(frame))
        else:
            targets = schedule_segment(
                previous,
                frame,
                fps=self.config.video.fps,
                timing=self.config.timing,
            )
            if len(targets) == 1 and targets[0].mode == "hold":
                self._submit(
                    header,
                    materialize_target(
                        previous,
                        frame,
                        targets[0],
                        timing=self.config.timing,
                    ),
                )
                self._segment = None
                self._next_frame_index = 0
            else:
                self._segment = _ScheduledSegment(
                    header=header,
                    left=previous,
                    right=frame,
                    targets=targets,
                    started_at=self._clock(),
                    origin_elapsed_s=previous.scenario.elapsed_s,
                )
                self._next_frame_index = 0
        self._last_frame = frame

    def _publish(self, outcome: CameraRenderOutcome) -> None:
        if outcome.error is not None or outcome.jpeg is None:
            self._last_error = outcome.error or "camera renderer returned no frame"
            self.store.clear()
            return
        self.store.publish(
            outcome.jpeg,
            sequence=outcome.sequence,
            elapsed_s=outcome.elapsed_s,
            render_backend=outcome.render_backend or "unknown",
        )
        self._rendered_frames += 1
        self._last_mode = outcome.mode
        self._last_reason = outcome.reason
        self._last_error = None

    def poll(self, now: float | None = None) -> CameraRenderOutcome | None:
        if self._closed:
            return None
        outcome = self._worker.poll()
        if outcome is not None:
            self._publish(outcome)
        if self._started and not self._worker.is_alive:
            raise RuntimeError("synthetic camera renderer process exited")
        segment = self._segment
        current_time = self._clock() if now is None else now
        if segment is not None:
            wall_elapsed_s = max(0.0, current_time - segment.started_at)
            due_index = self._next_frame_index
            while due_index < len(segment.targets):
                target = segment.targets[due_index]
                due_after_s = target.elapsed_s - segment.origin_elapsed_s
                if due_after_s > wall_elapsed_s + 1e-12:
                    break
                due_index += 1
            if due_index > self._next_frame_index:
                self._submit(
                    segment.header,
                    materialize_target(
                        segment.left,
                        segment.right,
                        segment.targets[due_index - 1],
                        timing=self.config.timing,
                    ),
                )
                self._next_frame_index = due_index
            if self._next_frame_index >= len(segment.targets):
                self._segment = None
        return outcome

    async def run(
        self,
        stop_event: asyncio.Event,
        *,
        poll_interval_s: float = 0.01,
    ) -> None:
        if poll_interval_s <= 0.0:
            raise ValueError("camera poll interval must be positive")
        self.start()
        while not stop_event.is_set():
            self.poll()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
            except TimeoutError:
                pass

    def status(self) -> Mapping[str, object]:
        snapshot = self.store.get()
        return {
            "camera_enabled": True,
            "camera_width": self.config.video.width,
            "camera_height": self.config.video.height,
            "camera_fps": self.config.video.fps,
            "camera_format": "MJPEG",
            "camera_frame_revision": (
                snapshot.revision if snapshot is not None else None
            ),
            "camera_rendered_sequence": (
                snapshot.sequence if snapshot is not None else None
            ),
            "camera_rendered_elapsed_s": (
                snapshot.elapsed_s if snapshot is not None else None
            ),
            "camera_render_backend": (
                snapshot.render_backend if snapshot is not None else None
            ),
            "camera_interpolation_mode": self._last_mode,
            "camera_interpolation_reason": self._last_reason,
            "camera_frames_submitted": self._submitted_frames,
            "camera_frames_rendered": self._rendered_frames,
            "camera_pending_replaced": self._worker.replaced_pending,
            "camera_worker_alive": self._worker.is_alive if self._started else False,
            "camera_render_error": self._last_error or self._worker.last_error,
        }

    def close(self, timeout_s: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._segment = None
        if self._started:
            self._worker.close(timeout_s)
