"""Single child process that renders only the newest pending snapshot."""

from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from queue import Empty, Full
from typing import Any

from scrap_monitoring_visualizer.contracts.models import SceneDefinition, SceneFrame
from scrap_monitoring_visualizer.geometry import build_scene_geometry
from scrap_monitoring_visualizer.preview import LatestFrameStore

from .renderer import RenderConfig, render_scene


@dataclass(frozen=True, slots=True)
class RenderRequest:
    header: SceneDefinition
    frame: SceneFrame
    config: RenderConfig


@dataclass(frozen=True, slots=True)
class RenderOutcome:
    generation: int
    sequence: int
    png: bytes | None
    error: str | None


def _render(generation: int, request: RenderRequest) -> RenderOutcome:
    try:
        geometry = build_scene_geometry(request.header, request.frame)
        png, _ = render_scene(
            request.header,
            request.frame,
            geometry,
            config=request.config,
        )
        return RenderOutcome(
            generation=generation,
            sequence=request.frame.sequence,
            png=png,
            error=None,
        )
    except Exception as error:
        return RenderOutcome(
            generation=generation,
            sequence=request.frame.sequence,
            png=None,
            error=str(error),
        )


def _worker_main(requests: Any, outcomes: Any) -> None:
    while True:
        envelope = requests.get()
        if envelope is None:
            return
        generation, request = envelope
        outcome = _render(generation, request)
        outcomes.put(outcome)


class LatestRenderWorker:
    def __init__(self, frames: LatestFrameStore) -> None:
        context = mp.get_context("spawn")
        self._requests = context.Queue(maxsize=1)
        self._outcomes = context.Queue(maxsize=1)
        self._process = context.Process(
            target=_worker_main,
            args=(self._requests, self._outcomes),
            name="visualizer-renderer",
        )
        self._frames = frames
        self._generation = 0
        self._inflight = False
        self._pending: tuple[int, RenderRequest] | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        self._process.start()

    @property
    def is_alive(self) -> bool:
        return self._process.is_alive()

    def submit(self, request: RenderRequest) -> None:
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

    def poll(self) -> RenderOutcome | None:
        latest: RenderOutcome | None = None
        while True:
            try:
                latest = self._outcomes.get_nowait()
            except Empty:
                break
            self._inflight = False
        self._flush_pending()
        if latest is None or latest.generation != self._generation:
            return None
        if latest.error is not None or latest.png is None:
            self.last_error = latest.error or "renderer returned no frame"
            return latest
        self._frames.publish(latest.png, sequence=latest.sequence)
        self.last_error = None
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
