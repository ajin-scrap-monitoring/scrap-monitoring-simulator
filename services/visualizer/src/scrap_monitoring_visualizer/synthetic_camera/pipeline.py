"""Live state adapter for interpolation and latest-only camera rendering."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from scrap_monitoring_visualizer.contracts.models import (
    SceneDefinition,
    SceneFrame,
    materialize_keyframe,
)
from scrap_monitoring_visualizer.state import ExecutionState

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

    def submit(self, request: CameraRenderRequest) -> int | None: ...

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
    _RATE_WINDOW_S = 5.0

    def __init__(
        self,
        config: SyntheticCameraConfig,
        store: LatestJpegStore | None = None,
        *,
        worker: CameraWorker | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_published: Callable[[InterpolatedFrame, bytes], None] | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.store = store or LatestJpegStore(config.video.max_frame_bytes)
        if self.store.max_frame_bytes != config.video.max_frame_bytes:
            raise ValueError("camera store and profile byte limits must match")
        self._worker = worker or LatestSyntheticRenderWorker()
        self._clock = clock
        self._on_published = on_published
        self._started = False
        self._closed = False
        self._run_id: str | None = None
        self._last_frame: SceneFrame | None = None
        self._last_segment_sequence: int | None = None
        self._last_submitted_target_id = -1
        self._segment: _ScheduledSegment | None = None
        self._next_frame_index = 0
        self._submitted_frames = 0
        self._rendered_frames = 0
        self._rendered_at: deque[float] = deque()
        self._last_render_seconds: float | None = None
        self._last_mode: str | None = None
        self._last_reason: str | None = None
        self._last_error: str | None = None
        self._frames_by_target: dict[int, InterpolatedFrame] = {}

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
        self._last_segment_sequence = None
        self._last_submitted_target_id = -1
        self._segment = None
        self._next_frame_index = 0
        self._last_mode = None
        self._last_reason = None
        self._last_error = None
        self._frames_by_target.clear()
        self._rendered_at.clear()
        self._last_render_seconds = None

    def _submit(self, header: SceneDefinition, frame: InterpolatedFrame) -> None:
        if frame.target_id <= self._last_submitted_target_id:
            return
        replaced_target_id = self._worker.submit(
            CameraRenderRequest(header=header, frame=frame, config=self.config)
        )
        if replaced_target_id is not None:
            self._frames_by_target.pop(replaced_target_id, None)
        self._frames_by_target[frame.target_id] = frame
        self._last_submitted_target_id = frame.target_id
        self._submitted_frames += 1
        self._last_mode = frame.mode
        self._last_reason = frame.reason

    def _submit_due(self, current_time: float) -> None:
        segment = self._segment
        if segment is None:
            return
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

    def _submit_segment_end(self) -> None:
        segment = self._segment
        if segment is None or self._next_frame_index >= len(segment.targets):
            return
        self._submit(
            segment.header,
            materialize_target(
                segment.left,
                segment.right,
                segment.targets[-1],
                timing=self.config.timing,
            ),
        )
        self._next_frame_index = len(segment.targets)
        self._segment = None

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
            self._frames_by_target.clear()
            self._segment = None
            self._next_frame_index = 0
            return
        segment = state.segment
        if header is None or segment is None:
            return
        if segment.sequence == self._last_segment_sequence:
            return
        left = materialize_keyframe(
            header, segment.left, segment.left_sequence, segment.run_id
        )
        right = materialize_keyframe(
            header, segment.right, segment.right_sequence, segment.run_id
        )
        if self._last_frame is None:
            initial_target_id = int(
                left.scenario.elapsed_s * self.config.video.fps + 1e-9
            )
            self._submit(
                header,
                materialize_target(
                    left,
                    right,
                    FrameTarget(
                        elapsed_s=left.scenario.elapsed_s,
                        mode="exact",
                        target_id=initial_target_id,
                    ),
                    timing=self.config.timing,
                ),
            )
        self._submit_due(self._clock())
        self._submit_segment_end()
        targets = schedule_segment(
            left,
            right,
            fps=self.config.video.fps,
            timing=self.config.timing,
        )
        if len(targets) == 1 and targets[0].mode == "hold":
            self._submit(
                header,
                materialize_target(left, right, targets[0], timing=self.config.timing),
            )
            self._segment = None
            self._next_frame_index = 0
        else:
            self._segment = _ScheduledSegment(
                header=header,
                left=left,
                right=right,
                targets=targets,
                started_at=self._clock(),
                origin_elapsed_s=left.scenario.elapsed_s,
            )
            self._next_frame_index = 0
        self._last_frame = right
        self._last_segment_sequence = segment.sequence

    def _publish(self, outcome: CameraRenderOutcome, published_at: float) -> None:
        frame = self._frames_by_target.pop(outcome.target_id, None)
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
        if frame is not None and self._on_published is not None:
            self._on_published(frame, outcome.jpeg)
        self._rendered_frames += 1
        self._rendered_at.append(published_at)
        while self._rendered_at[0] < published_at - self._RATE_WINDOW_S:
            self._rendered_at.popleft()
        self._last_render_seconds = outcome.render_seconds
        self._last_mode = outcome.mode
        self._last_reason = outcome.reason
        self._last_error = None

    def poll(self, now: float | None = None) -> CameraRenderOutcome | None:
        if self._closed:
            return None
        current_time = self._clock() if now is None else now
        outcome = self._worker.poll()
        if outcome is not None:
            self._publish(outcome, current_time)
        if self._started and not self._worker.is_alive:
            raise RuntimeError("synthetic camera renderer process exited")
        self._submit_due(current_time)
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
        now = self._clock()
        recent_renders = tuple(
            rendered_at
            for rendered_at in self._rendered_at
            if rendered_at >= now - self._RATE_WINDOW_S
        )
        source_fps = (
            (len(recent_renders) - 1) / (now - recent_renders[0])
            if len(recent_renders) >= 2 and now > recent_renders[0]
            else 0.0
        )
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
            "camera_source_fps": round(source_fps, 3),
            "camera_source_fps_window_s": self._RATE_WINDOW_S,
            "camera_last_render_ms": (
                round(self._last_render_seconds * 1_000.0, 3)
                if self._last_render_seconds is not None
                else None
            ),
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
