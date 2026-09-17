"""Latest-only child process for synthetic camera rendering."""

from __future__ import annotations

import asyncio
import math
import multiprocessing as mp
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from multiprocessing.connection import Connection
from queue import Empty, Full
from typing import Any, cast

from scrap_monitoring_visualizer.contracts.models import SceneDefinition

from .models import InterpolatedFrame, SyntheticCameraConfig
from .renderer import create_renderer


@dataclass(frozen=True, slots=True)
class CameraRenderRequest:
    header: SceneDefinition
    frame: InterpolatedFrame
    config: SyntheticCameraConfig


@dataclass(frozen=True, slots=True)
class CameraRenderOutcome:
    generation: int
    sequence: int
    elapsed_s: float
    mode: str
    reason: str | None
    jpeg: bytes | None
    render_backend: str | None
    render_seconds: float
    error: str | None
    target_id: int = 0
    stage_seconds: dict[str, float] = field(default_factory=dict)
    request_ipc_seconds: float | None = None
    result_ipc_seconds: float | None = None
    worker_idle_seconds: float | None = None
    completed_at: float | None = None


def _stage_seconds(rendered: Any) -> dict[str, float]:
    raw = getattr(rendered, "stage_seconds", None)
    if not isinstance(raw, Mapping):
        return {}
    timings: dict[str, float] = {}
    for name, value in raw.items():
        if (
            isinstance(name, str)
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0.0
        ):
            timings[name] = float(value)
    return dict(sorted(timings.items()))


def _render(
    renderer: Any,
    generation: int,
    request: CameraRenderRequest,
    *,
    submitted_at: float | None = None,
    request_received_at: float | None = None,
    worker_idle_seconds: float | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> CameraRenderOutcome:
    frame = request.frame.frame
    started_at = clock()
    received_at = request_received_at if request_received_at is not None else started_at
    request_ipc_seconds = (
        max(0.0, received_at - submitted_at) if submitted_at is not None else None
    )
    try:
        rendered = renderer.render(
            request.header,
            frame,
            request.config,
            interpolation=request.frame,
        )
        completed_at = clock()
        return CameraRenderOutcome(
            generation=generation,
            target_id=request.frame.target_id,
            sequence=rendered.sequence,
            elapsed_s=rendered.elapsed_s,
            mode=request.frame.mode,
            reason=request.frame.reason,
            jpeg=rendered.jpeg,
            render_backend=rendered.render_backend,
            render_seconds=completed_at - started_at,
            error=None,
            stage_seconds=_stage_seconds(rendered),
            request_ipc_seconds=request_ipc_seconds,
            worker_idle_seconds=worker_idle_seconds,
            completed_at=completed_at,
        )
    except Exception as error:
        completed_at = clock()
        return CameraRenderOutcome(
            generation=generation,
            target_id=request.frame.target_id,
            sequence=frame.sequence,
            elapsed_s=frame.scenario.elapsed_s,
            mode=request.frame.mode,
            reason=request.frame.reason,
            jpeg=None,
            render_backend=None,
            render_seconds=completed_at - started_at,
            error=str(error),
            request_ipc_seconds=request_ipc_seconds,
            worker_idle_seconds=worker_idle_seconds,
            completed_at=completed_at,
        )


def _worker_main(requests: Any, outcomes: Any) -> None:
    renderer = None
    try:
        while True:
            idle_started_at = time.perf_counter()
            envelope = requests.get()
            request_received_at = time.perf_counter()
            if envelope is None:
                return
            generation, request, submitted_at = envelope
            if renderer is None:
                renderer = create_renderer(request.config)
            outcome = _render(
                renderer,
                generation,
                request,
                submitted_at=submitted_at,
                request_received_at=request_received_at,
                worker_idle_seconds=request_received_at - idle_started_at,
            )
            outcomes.put(outcome)
    finally:
        if renderer is not None:
            renderer.close()


class LatestSyntheticRenderWorker:
    def __init__(self) -> None:
        context = mp.get_context("spawn")
        self._requests = context.Queue(maxsize=1)
        self._outcomes = context.Queue(maxsize=1)
        self._outcome_reader = cast(Connection, cast(Any, self._outcomes)._reader)
        self._process = context.Process(
            target=_worker_main,
            args=(self._requests, self._outcomes),
            name="synthetic-camera-renderer",
        )
        self._generation = 0
        self._inflight = False
        self._pending: tuple[int, CameraRenderRequest] | None = None
        self.last_error: str | None = None
        self.replaced_pending = 0

    def start(self) -> None:
        self._process.start()

    @property
    def is_alive(self) -> bool:
        return self._process.is_alive()

    @property
    def inflight(self) -> bool:
        return self._inflight

    @property
    def pending(self) -> bool:
        return self._pending is not None

    def submit(self, request: CameraRenderRequest) -> int | None:
        replaced_target_id = (
            self._pending[1].frame.target_id if self._pending is not None else None
        )
        if self._pending is not None:
            self.replaced_pending += 1
        self._pending = (self._generation, request)
        self._flush_pending()
        return replaced_target_id

    def _flush_pending(self) -> None:
        if self._pending is None or self._inflight:
            return
        generation, request = self._pending
        try:
            self._requests.put_nowait((generation, request, time.perf_counter()))
        except Full:
            return
        self._inflight = True
        self._pending = None

    def invalidate(self) -> None:
        self._generation += 1
        self._pending = None
        self.last_error = None

    def poll(self) -> CameraRenderOutcome | None:
        latest: CameraRenderOutcome | None = None
        while True:
            try:
                candidate = self._outcomes.get_nowait()
            except Empty:
                break
            if candidate.completed_at is not None:
                candidate = replace(
                    candidate,
                    result_ipc_seconds=max(
                        0.0, time.perf_counter() - candidate.completed_at
                    ),
                )
            latest = candidate
            self._inflight = False
        self._flush_pending()
        if latest is None or latest.generation != self._generation:
            return None
        self.last_error = latest.error
        return latest

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[None] = loop.create_future()

        def wake() -> None:
            if not ready.done():
                ready.set_result(None)

        outcome_fd = self._outcome_reader.fileno()
        process_fd = self._process.sentinel
        loop.add_reader(outcome_fd, wake)
        loop.add_reader(process_fd, wake)
        try:
            if self._outcome_reader.poll() or not self._process.is_alive():
                wake()
            await ready
        finally:
            loop.remove_reader(outcome_fd)
            loop.remove_reader(process_fd)

    def close(self, timeout_s: float = 5.0) -> None:
        self._pending = None
        try:
            self._requests.put(None, timeout=timeout_s)
        except Full:
            pass
        self._process.join(timeout_s)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout_s)
        self._requests.close()
        self._outcomes.close()
