from __future__ import annotations

from pathlib import Path
from typing import Any

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneFrame,
    SceneSegment,
    materialize_keyframe,
)
from scrap_monitoring_visualizer.synthetic_camera import SyntheticCameraConfig
from scrap_monitoring_visualizer.synthetic_camera.models import (
    InterpolatedFrame,
    RenderedCameraFrame,
)
from scrap_monitoring_visualizer.synthetic_camera.worker import (
    CameraRenderRequest,
    _render,
)

CONTRACT_ROOT = Path("../contracts/scene/v2")


class RecordingRenderer:
    def __init__(self) -> None:
        self.interpolation: InterpolatedFrame | None = None

    def render(
        self,
        header: SceneDefinition,
        frame: SceneFrame,
        config: SyntheticCameraConfig,
        *,
        interpolation: InterpolatedFrame | None = None,
    ) -> RenderedCameraFrame:
        del header
        self.interpolation = interpolation
        return RenderedCameraFrame(
            jpeg=b"\xff\xd8frame\xff\xd9",
            sequence=frame.sequence,
            elapsed_s=frame.scenario.elapsed_s,
            width=config.video.width,
            height=config.video.height,
            render_backend="test",
        )


def test_worker_passes_inlet_interpolation_to_renderer() -> None:
    parser = ContractParser(CONTRACT_ROOT)
    lines = (
        (CONTRACT_ROOT / "fixtures/scene.v2.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    header = parser.parse_line(lines[0]).value
    segment = parser.parse_line(lines[1]).value
    assert isinstance(header, SceneDefinition)
    assert isinstance(segment, SceneSegment)
    frame = materialize_keyframe(
        header, segment.right, segment.right_sequence, segment.run_id
    )
    interpolation = InterpolatedFrame(
        frame=frame,
        left_sequence=frame.sequence,
        right_sequence=frame.sequence + 1,
        alpha=0.5,
        mode="interpolated",
        left_inlet_index=0,
        right_inlet_index=1,
    )
    renderer = RecordingRenderer()
    request = CameraRenderRequest(
        header=header,
        frame=interpolation,
        config=SyntheticCameraConfig.from_file(),
    )

    outcome = _render(renderer, 3, request)

    assert renderer.interpolation is interpolation
    assert outcome.generation == 3
    assert outcome.error is None


def test_worker_reports_renderer_failure() -> None:
    class FailedRenderer:
        def render(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            raise RuntimeError("render failed")

    parser = ContractParser(CONTRACT_ROOT)
    lines = (
        (CONTRACT_ROOT / "fixtures/scene.v2.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    header = parser.parse_line(lines[0]).value
    segment = parser.parse_line(lines[1]).value
    assert isinstance(header, SceneDefinition)
    assert isinstance(segment, SceneSegment)
    frame = materialize_keyframe(
        header, segment.right, segment.right_sequence, segment.run_id
    )
    interpolation = InterpolatedFrame(
        frame=frame,
        left_sequence=frame.sequence,
        right_sequence=frame.sequence,
        alpha=1.0,
        mode="exact",
        left_inlet_index=0,
        right_inlet_index=0,
    )

    outcome = _render(
        FailedRenderer(),
        4,
        CameraRenderRequest(
            header=header,
            frame=interpolation,
            config=SyntheticCameraConfig.from_file(),
        ),
    )

    assert outcome.generation == 4
    assert outcome.error == "render failed"
