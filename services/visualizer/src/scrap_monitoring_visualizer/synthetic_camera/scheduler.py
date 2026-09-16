"""Bounded frame-time selection for live and replay camera segments."""

from __future__ import annotations

import math
from collections.abc import Iterator

from scrap_monitoring_visualizer.contracts.models import SceneFrame

from .interpolation import (
    InterpolationError,
    exact_frame,
    interpolate_frames,
    validate_interpolation_segment,
)
from .models import FrameTarget, InterpolatedFrame, TimingConfig


def schedule_segment(
    left: SceneFrame,
    right: SceneFrame,
    *,
    fps: int,
    timing: TimingConfig,
) -> tuple[FrameTarget, ...]:
    if fps <= 0 or fps > 60:
        raise ValueError("frame rate must be between 1 and 60")
    try:
        duration_s = validate_interpolation_segment(
            left,
            right,
            max_gap_s=timing.max_interpolation_gap_s,
        )
    except InterpolationError as error:
        return (
            FrameTarget(
                elapsed_s=right.scenario.elapsed_s,
                mode="hold",
                reason=error.code,
            ),
        )

    if duration_s > timing.max_segment_frames / fps:
        return (
            FrameTarget(
                elapsed_s=right.scenario.elapsed_s,
                mode="hold",
                reason="frame_limit",
            ),
        )

    regular_count = math.floor(duration_s * fps + 1e-9)
    candidate_times = [
        left.scenario.elapsed_s + index / fps
        for index in range(1, regular_count + 1)
        if left.scenario.elapsed_s + index / fps < right.scenario.elapsed_s - 1e-12
    ]
    candidate_times.append(right.scenario.elapsed_s)
    if len(candidate_times) > timing.max_segment_frames:
        return (
            FrameTarget(
                elapsed_s=right.scenario.elapsed_s,
                mode="hold",
                reason="frame_limit",
            ),
        )
    return tuple(
        FrameTarget(
            elapsed_s=elapsed_s,
            mode=(
                "exact"
                if math.isclose(
                    elapsed_s,
                    right.scenario.elapsed_s,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
                else "interpolated"
            ),
        )
        for elapsed_s in candidate_times
    )


def materialize_target(
    left: SceneFrame,
    right: SceneFrame,
    target: FrameTarget,
    *,
    timing: TimingConfig,
) -> InterpolatedFrame:
    if target.mode == "hold":
        return InterpolatedFrame(
            frame=right,
            left_sequence=left.sequence,
            right_sequence=right.sequence,
            alpha=1.0,
            mode="hold",
            left_inlet_index=right.scenario.current_inlet_index,
            right_inlet_index=right.scenario.current_inlet_index,
            reason=target.reason,
        )
    return interpolate_frames(
        left,
        right,
        target.elapsed_s,
        max_gap_s=timing.max_interpolation_gap_s,
    )


def replay_frames(
    frames: tuple[SceneFrame, ...],
    *,
    fps: int,
    timing: TimingConfig,
) -> Iterator[InterpolatedFrame]:
    if not frames:
        return
    yield exact_frame(frames[0])
    for left, right in zip(frames, frames[1:], strict=False):
        for target in schedule_segment(left, right, fps=fps, timing=timing):
            yield materialize_target(left, right, target, timing=timing)
