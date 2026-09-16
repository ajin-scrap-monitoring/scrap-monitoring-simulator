"""Pure temporal interpolation for complete SceneFrame surface snapshots."""

from __future__ import annotations

import math
from dataclasses import replace

from scrap_monitoring_visualizer.contracts.models import SceneFrame, Surface

from .models import InterpolatedFrame


class InterpolationError(ValueError):
    """A segment that must not be interpolated."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_interpolation_segment(
    left: SceneFrame,
    right: SceneFrame,
    *,
    max_gap_s: float,
) -> float:
    if max_gap_s <= 0.0 or not math.isfinite(max_gap_s):
        raise ValueError("maximum interpolation gap must be positive and finite")
    if left.run_id != right.run_id:
        raise InterpolationError("run", "cannot interpolate across run boundaries")
    if right.sequence != left.sequence + 1:
        raise InterpolationError(
            "sequence", "cannot interpolate across a sequence discontinuity"
        )
    duration_s = right.scenario.elapsed_s - left.scenario.elapsed_s
    if duration_s <= 0.0:
        raise InterpolationError(
            "time", "interpolation requires increasing simulation time"
        )
    if duration_s > max_gap_s:
        raise InterpolationError(
            "gap", "interpolation interval exceeds the configured limit"
        )
    left_surface = left.surface
    right_surface = right.surface
    if (
        left_surface.cell_size_m != right_surface.cell_size_m
        or left_surface.x_coordinates_m != right_surface.x_coordinates_m
        or left_surface.y_coordinates_m != right_surface.y_coordinates_m
        or len(left_surface.heights_m) != len(right_surface.heights_m)
        or any(
            len(left_row) != len(right_row)
            for left_row, right_row in zip(
                left_surface.heights_m,
                right_surface.heights_m,
                strict=True,
            )
        )
    ):
        raise InterpolationError(
            "grid", "cannot interpolate across surface grid changes"
        )
    return duration_s


def _linear(left: float, right: float, alpha: float) -> float:
    return left + (right - left) * alpha


def interpolate_frames(
    left: SceneFrame,
    right: SceneFrame,
    elapsed_s: float,
    *,
    max_gap_s: float,
) -> InterpolatedFrame:
    duration_s = validate_interpolation_segment(left, right, max_gap_s=max_gap_s)
    if not math.isfinite(elapsed_s):
        raise ValueError("frame time must be finite")
    if elapsed_s < left.scenario.elapsed_s or elapsed_s > right.scenario.elapsed_s:
        raise ValueError("frame time must be inside the interpolation segment")
    alpha = (elapsed_s - left.scenario.elapsed_s) / duration_s
    if math.isclose(alpha, 0.0, rel_tol=0.0, abs_tol=1e-12):
        return InterpolatedFrame(
            frame=left,
            left_sequence=left.sequence,
            right_sequence=right.sequence,
            alpha=0.0,
            mode="exact",
            left_inlet_index=left.scenario.current_inlet_index,
            right_inlet_index=right.scenario.current_inlet_index,
        )
    if math.isclose(alpha, 1.0, rel_tol=0.0, abs_tol=1e-12):
        return InterpolatedFrame(
            frame=right,
            left_sequence=left.sequence,
            right_sequence=right.sequence,
            alpha=1.0,
            mode="exact",
            left_inlet_index=left.scenario.current_inlet_index,
            right_inlet_index=right.scenario.current_inlet_index,
        )

    heights = tuple(
        tuple(
            _linear(left_height, right_height, alpha)
            for left_height, right_height in zip(left_row, right_row, strict=True)
        )
        for left_row, right_row in zip(
            left.surface.heights_m,
            right.surface.heights_m,
            strict=True,
        )
    )
    surface = Surface(
        cell_size_m=left.surface.cell_size_m,
        x_coordinates_m=left.surface.x_coordinates_m,
        y_coordinates_m=left.surface.y_coordinates_m,
        heights_m=heights,
    )
    left_scenario = left.scenario
    right_scenario = right.scenario
    scenario = replace(
        left_scenario,
        elapsed_s=elapsed_s,
        surface_updated_at_s=min(
            elapsed_s,
            _linear(
                left_scenario.surface_updated_at_s,
                right_scenario.surface_updated_at_s,
                alpha,
            ),
        ),
        surface_fill_ratio=_linear(
            left_scenario.surface_fill_ratio,
            right_scenario.surface_fill_ratio,
            alpha,
        ),
        surface_volume_m3=_linear(
            left_scenario.surface_volume_m3,
            right_scenario.surface_volume_m3,
            alpha,
        ),
    )
    return InterpolatedFrame(
        frame=replace(left, scenario=scenario, surface=surface),
        left_sequence=left.sequence,
        right_sequence=right.sequence,
        alpha=alpha,
        mode="interpolated",
        left_inlet_index=left.scenario.current_inlet_index,
        right_inlet_index=right.scenario.current_inlet_index,
    )


def exact_frame(frame: SceneFrame) -> InterpolatedFrame:
    return InterpolatedFrame(
        frame=frame,
        left_sequence=frame.sequence,
        right_sequence=frame.sequence,
        alpha=1.0,
        mode="exact",
        left_inlet_index=frame.scenario.current_inlet_index,
        right_inlet_index=frame.scenario.current_inlet_index,
    )
