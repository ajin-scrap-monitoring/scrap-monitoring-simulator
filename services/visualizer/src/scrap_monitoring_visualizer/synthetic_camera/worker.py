"""Latest-only child process for synthetic camera rendering."""

from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from queue import Empty, Full
from typing import Any

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
    error: str | None


def _render(
    renderer: Any,
    generation: int,
    request: CameraRenderRequest,
) -> CameraRenderOutcome:
    frame = request.frame.frame
    try:
        rendered = renderer.render(
            request.header,
            frame,
            request.config,
        )
        return CameraRenderOutcome(
            generation=generation,
            sequence=rendered.sequence,
            elapsed_s=rendered.elapsed_s,
            mode=request.frame.mode,
            reason=request.frame.reason,
            jpeg=rendered.jpeg,
            render_backend=rendered.render_backend,
            error=None,
        )
    except Exception as error:
        return CameraRenderOutcome(
            generation=generation,
            sequence=frame.sequence,
            elapsed_s=frame.scenario.elapsed_s,
            mode=request.frame.mode,
            reason=request.frame.reason,
            jpeg=None,
            render_backend=None,
            error=str(error),
        )


def _worker_main(requests: Any, outcomes: Any) -> None:
    renderer = None
    try:
        while True:
            envelope = requests.get()
            if envelope is None:
                return
            generation, request = envelope
            if renderer is None:
                renderer = create_renderer(request.config)
            outcomes.put(_render(renderer, generation, request))
    finally:
        if renderer is not None:
            renderer.close()


class LatestSyntheticRenderWorker:
    def __init__(self) -> None:
        context = mp.get_context("spawn")
        self._requests = context.Queue(maxsize=1)
        self._outcomes = context.Queue(maxsize=1)
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

    def submit(self, request: CameraRenderRequest) -> None:
        if self._pending is not None:
            self.replaced_pending += 1
        self._pending = (self._generation, request)
        self._flush_pending()

    def _flush_pending(self) -> None:
        if self._pending is None or self._inflight:
            return
        try:
            self._requests.put_nowait(self._pending)
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
                latest = self._outcomes.get_nowait()
            except Empty:
                break
            self._inflight = False
        self._flush_pending()
        if latest is None or latest.generation != self._generation:
            return None
        self.last_error = latest.error
        return latest

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
