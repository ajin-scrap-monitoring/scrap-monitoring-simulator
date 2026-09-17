from __future__ import annotations

from collections import deque
from dataclasses import replace
from pathlib import Path

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneSegment,
)
from scrap_monitoring_visualizer.state import ExecutionState
from scrap_monitoring_visualizer.synthetic_camera import (
    LatestJpegStore,
    SyntheticCameraConfig,
    SyntheticCameraPipeline,
)
from scrap_monitoring_visualizer.synthetic_camera.worker import (
    CameraRenderOutcome,
    CameraRenderRequest,
)

CONTRACT_ROOT = Path("../contracts/scene/v2")


class FakeWorker:
    def __init__(self) -> None:
        self.last_error: str | None = None
        self.replaced_pending = 0
        self.started = False
        self.alive = True
        self.invalidations = 0
        self.submitted: list[CameraRenderRequest] = []
        self.outcomes: deque[CameraRenderOutcome] = deque()
        self.replaced_on_submit: deque[int | None] = deque()

    def start(self) -> None:
        self.started = True

    @property
    def is_alive(self) -> bool:
        return self.alive

    def submit(self, request: CameraRenderRequest) -> int | None:
        self.submitted.append(request)
        return self.replaced_on_submit.popleft() if self.replaced_on_submit else None

    def invalidate(self) -> None:
        self.invalidations += 1

    def poll(self) -> CameraRenderOutcome | None:
        return self.outcomes.popleft() if self.outcomes else None

    def close(self, timeout_s: float = 5.0) -> None:
        del timeout_s


def _records() -> tuple[SceneDefinition, SceneSegment]:
    parser = ContractParser(CONTRACT_ROOT)
    lines = (
        (CONTRACT_ROOT / "fixtures/scene.v2.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    definition = parser.parse_line(lines[0]).value
    segment = parser.parse_line(lines[1]).value
    assert isinstance(definition, SceneDefinition)
    assert isinstance(segment, SceneSegment)
    return definition, segment


def _state(
    header: SceneDefinition, segment: SceneSegment, *, connected: bool = True
) -> ExecutionState:
    return ExecutionState(header=header, segment=segment, connected=connected)


def _next_segment(segment: SceneSegment, elapsed_s: float) -> SceneSegment:
    right = replace(
        segment.right,
        scenario=replace(
            segment.right.scenario,
            elapsed_s=elapsed_s,
            surface_updated_at_s=elapsed_s,
        ),
    )
    return replace(
        segment,
        sequence=segment.sequence + 1,
        left_sequence=segment.right_sequence,
        right_sequence=segment.right_sequence + 1,
        left=segment.right,
        right=right,
    )


def test_latest_jpeg_store_replaces_without_history() -> None:
    store = LatestJpegStore(16)
    first = store.publish(
        b"\xff\xd8first\xff\xd9", sequence=1, elapsed_s=1.0, render_backend="test"
    )
    second = store.publish(
        b"\xff\xd8last\xff\xd9", sequence=2, elapsed_s=2.0, render_backend="test"
    )

    assert first.revision == 1
    assert store.get() == second
    store.clear()
    assert store.get() is None


def test_pipeline_schedules_targets_from_v2_segment_and_publishes_outcome() -> None:
    header, segment = _records()
    worker = FakeWorker()
    now = [10.0]
    pipeline = SyntheticCameraPipeline(
        SyntheticCameraConfig.from_file(), worker=worker, clock=lambda: now[0]
    )
    pipeline.start()
    pipeline.state_changed(_state(header, segment))

    assert worker.started
    assert len(worker.submitted) == 1
    assert worker.submitted[0].frame.target_id == 0
    pipeline.poll(now=10.0 + 1 / 30)
    assert worker.submitted[-1].frame.target_id == 1

    worker.outcomes.append(
        CameraRenderOutcome(
            generation=0,
            target_id=1,
            sequence=1,
            elapsed_s=1 / 30,
            mode="interpolated",
            reason=None,
            jpeg=b"\xff\xd8frame\xff\xd9",
            render_backend="test",
            render_seconds=0.02,
            error=None,
        )
    )
    pipeline.poll(now=10.1)

    assert pipeline.store.get() is not None
    assert pipeline.status()["camera_frames_rendered"] == 1
    assert pipeline.status()["camera_last_render_ms"] == 20.0
    pipeline.close()


def test_pipeline_finishes_pending_segment_before_accepting_next_segment() -> None:
    header, segment = _records()
    worker = FakeWorker()
    now = [10.0]
    pipeline = SyntheticCameraPipeline(
        SyntheticCameraConfig.from_file(), worker=worker, clock=lambda: now[0]
    )
    pipeline.state_changed(_state(header, segment))
    now[0] = 10.09
    pipeline.state_changed(_state(header, _next_segment(segment, 0.2)))

    assert worker.submitted[-1].frame.frame.scenario.elapsed_s == 0.1
    assert worker.submitted[-1].frame.mode == "exact"
    pipeline.close()


def test_pipeline_clears_frames_on_run_change_and_disconnect() -> None:
    header, segment = _records()
    worker = FakeWorker()
    pipeline = SyntheticCameraPipeline(SyntheticCameraConfig.from_file(), worker=worker)
    pipeline.store.publish(
        b"\xff\xd8live\xff\xd9", sequence=1, elapsed_s=0.1, render_backend="test"
    )
    pipeline.state_changed(
        ExecutionState(header=replace(header, run_id="next"), connected=True)
    )
    assert pipeline.store.get() is None

    pipeline.state_changed(_state(header, segment, connected=False))
    assert worker.invalidations >= 2
    pipeline.close()


def test_pipeline_fails_when_camera_renderer_process_exits() -> None:
    worker = FakeWorker()
    pipeline = SyntheticCameraPipeline(SyntheticCameraConfig.from_file(), worker=worker)
    pipeline.start()
    worker.alive = False
    try:
        pipeline.poll()
    except RuntimeError as error:
        assert str(error) == "synthetic camera renderer process exited"
    else:
        raise AssertionError("dead camera renderer was not detected")
    finally:
        pipeline.close()


def test_pipeline_discards_replaced_and_failed_frame_metadata() -> None:
    header, segment = _records()
    worker = FakeWorker()
    now = [10.0]
    pipeline = SyntheticCameraPipeline(
        SyntheticCameraConfig.from_file(), worker=worker, clock=lambda: now[0]
    )
    pipeline.state_changed(_state(header, segment))
    assert set(pipeline._frames_by_target) == {0}

    worker.replaced_on_submit.append(0)
    now[0] += 1 / 30
    pipeline.poll(now=now[0])
    assert set(pipeline._frames_by_target) == {1}

    worker.outcomes.append(
        CameraRenderOutcome(
            generation=0,
            target_id=1,
            sequence=1,
            elapsed_s=1 / 30,
            mode="interpolated",
            reason=None,
            jpeg=None,
            render_backend=None,
            render_seconds=0.02,
            error="render failed",
        )
    )
    pipeline.poll(now=now[0])
    assert pipeline._frames_by_target == {}
    pipeline.close()
