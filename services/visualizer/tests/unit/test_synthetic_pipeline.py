from __future__ import annotations

from collections import deque
from dataclasses import replace
from pathlib import Path

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneFrame,
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

CONTRACT_ROOT = Path("../contracts/scene/v1")


class FakeWorker:
    def __init__(self) -> None:
        self.last_error: str | None = None
        self.replaced_pending = 0
        self.started = False
        self.alive = True
        self.invalidations = 0
        self.submitted: list[CameraRenderRequest] = []
        self.outcomes: deque[CameraRenderOutcome] = deque()

    def start(self) -> None:
        self.started = True

    @property
    def is_alive(self) -> bool:
        return self.alive

    def submit(self, request: CameraRenderRequest) -> None:
        self.submitted.append(request)

    def invalidate(self) -> None:
        self.invalidations += 1

    def poll(self) -> CameraRenderOutcome | None:
        return self.outcomes.popleft() if self.outcomes else None

    def close(self, timeout_s: float = 5.0) -> None:
        del timeout_s


def _records() -> tuple[SceneDefinition, SceneFrame]:
    parser = ContractParser(CONTRACT_ROOT)
    lines = (
        (CONTRACT_ROOT / "fixtures/scene.v1.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    values = tuple(parser.parse_line(line).value for line in lines)
    assert isinstance(values[0], SceneDefinition)
    assert isinstance(values[1], SceneFrame)
    return values[0], values[1]


def test_latest_jpeg_store_replaces_without_history() -> None:
    store = LatestJpegStore(16)

    first = store.publish(
        b"\xff\xd8first\xff\xd9", sequence=1, elapsed_s=1.0, render_backend="test"
    )
    second = store.publish(
        b"\xff\xd8last\xff\xd9", sequence=2, elapsed_s=2.0, render_backend="test"
    )

    assert first.revision == 1
    assert second.revision == 2
    assert store.get() == second
    store.clear()
    assert store.get() is None
    assert store.revision == 3


def test_latest_jpeg_store_does_not_publish_repeated_jpeg_bytes() -> None:
    store = LatestJpegStore(16)
    jpeg = b"\xff\xd8same\xff\xd9"

    first = store.publish(jpeg, sequence=1, elapsed_s=1.0, render_backend="test")
    repeated = store.publish(jpeg, sequence=2, elapsed_s=2.0, render_backend="test")

    assert repeated is first
    assert store.revision == 1
    current = store.get()
    assert current == first
    assert current is not None
    assert current.sequence == 1


def test_latest_jpeg_store_rejects_invalid_or_oversized_frames() -> None:
    store = LatestJpegStore(5)

    for frame in (b"not-jpeg", b"\xff\xd8xx\xff\xd9"):
        try:
            store.publish(frame, sequence=1, elapsed_s=1.0, render_backend="test")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid frame was accepted")


def test_pipeline_paces_interpolated_samples_and_publishes_outcome() -> None:
    header, frame = _records()
    right = replace(
        frame,
        sequence=2,
        scenario=replace(
            frame.scenario,
            elapsed_s=2.0,
            surface_updated_at_s=2.0,
        ),
    )
    config = SyntheticCameraConfig.from_file()
    worker = FakeWorker()
    now = [10.0]
    pipeline = SyntheticCameraPipeline(
        config,
        worker=worker,
        clock=lambda: now[0],
    )
    pipeline.start()
    pipeline.state_changed(ExecutionState(header=header, frame=frame, connected=True))
    pipeline.state_changed(ExecutionState(header=header, frame=right, connected=True))

    assert worker.started
    assert len(worker.submitted) == 1
    pipeline.poll(now=10.0 + 1 / 30)
    assert len(worker.submitted) == 2
    assert worker.submitted[-1].frame.mode == "interpolated"

    worker.outcomes.append(
        CameraRenderOutcome(
            generation=0,
            sequence=1,
            elapsed_s=1.0 + 1 / 30,
            mode="interpolated",
            reason=None,
            jpeg=b"\xff\xd8frame\xff\xd9",
            render_backend="vtkEGLRenderWindow",
            render_seconds=0.02,
            error=None,
        )
    )
    pipeline.poll(now=10.1)

    snapshot = pipeline.store.get()
    assert snapshot is not None
    assert snapshot.render_backend == "vtkEGLRenderWindow"
    assert pipeline.status()["camera_frames_rendered"] == 1
    assert pipeline.status()["camera_last_render_ms"] == 20.0
    pipeline.close()


def test_pipeline_submits_due_segment_end_before_next_segment() -> None:
    header, frame = _records()
    second = replace(
        frame,
        sequence=2,
        scenario=replace(
            frame.scenario,
            elapsed_s=2.0,
            surface_updated_at_s=2.0,
        ),
    )
    third = replace(
        frame,
        sequence=3,
        scenario=replace(
            frame.scenario,
            elapsed_s=3.0,
            surface_updated_at_s=3.0,
        ),
    )
    worker = FakeWorker()
    now = [10.0]
    pipeline = SyntheticCameraPipeline(
        SyntheticCameraConfig.from_file(),
        worker=worker,
        clock=lambda: now[0],
    )
    pipeline.state_changed(ExecutionState(header=header, frame=frame, connected=True))
    pipeline.state_changed(ExecutionState(header=header, frame=second, connected=True))

    now[0] = 10.99
    pipeline.state_changed(ExecutionState(header=header, frame=third, connected=True))

    assert worker.submitted[-1].frame.frame.scenario.elapsed_s == 2.0
    assert worker.submitted[-1].frame.mode == "exact"
    pipeline.close()


def test_pipeline_source_fps_decays_when_rendering_stalls() -> None:
    worker = FakeWorker()
    now = [10.0]
    pipeline = SyntheticCameraPipeline(
        SyntheticCameraConfig.from_file(),
        worker=worker,
        clock=lambda: now[0],
    )
    for index, published_at in enumerate((10.0, 10.1, 10.2), start=1):
        worker.outcomes.append(
            CameraRenderOutcome(
                generation=0,
                sequence=index,
                elapsed_s=float(index),
                mode="interpolated",
                reason=None,
                jpeg=b"\xff\xd8frame\xff\xd9",
                render_backend="test",
                render_seconds=0.02,
                error=None,
            )
        )
        now[0] = published_at
        pipeline.poll()

    assert pipeline.status()["camera_source_fps"] == 10.0
    now[0] = 14.2
    assert pipeline.status()["camera_source_fps"] == 0.476
    now[0] = 15.3
    assert pipeline.status()["camera_source_fps"] == 0.0
    pipeline.close()


def test_pipeline_clears_frame_when_run_changes() -> None:
    header, frame = _records()
    config = SyntheticCameraConfig.from_file()
    worker = FakeWorker()
    pipeline = SyntheticCameraPipeline(config, worker=worker)
    pipeline.store.publish(
        b"\xff\xd8old\xff\xd9",
        sequence=1,
        elapsed_s=1.0,
        render_backend="test",
    )

    pipeline.state_changed(
        ExecutionState(header=replace(header, run_id="next"), connected=True)
    )

    assert pipeline.store.get() is None
    assert worker.invalidations == 1
    pipeline.close()


def test_pipeline_fails_when_renderer_process_exits() -> None:
    config = SyntheticCameraConfig.from_file()
    worker = FakeWorker()
    pipeline = SyntheticCameraPipeline(config, worker=worker)
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


def test_pipeline_clears_frame_when_source_disconnects() -> None:
    header, frame = _records()
    worker = FakeWorker()
    pipeline = SyntheticCameraPipeline(
        SyntheticCameraConfig.from_file(),
        worker=worker,
    )
    pipeline.state_changed(ExecutionState(header=header, frame=frame, connected=True))
    pipeline.store.publish(
        b"\xff\xd8live\xff\xd9",
        sequence=frame.sequence,
        elapsed_s=frame.scenario.elapsed_s,
        render_backend="test",
    )

    pipeline.state_changed(ExecutionState(header=header, frame=frame, connected=False))

    assert pipeline.store.get() is None
    assert worker.invalidations == 2
    pipeline.close()
