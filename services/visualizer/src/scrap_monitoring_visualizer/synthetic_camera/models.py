"""Configuration and frame models for the synthetic camera boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from scrap_monitoring_visualizer.contracts.models import SceneFrame

type RenderBackend = Literal["auto", "osmesa", "egl"]
type InterpolationMode = Literal["exact", "interpolated", "hold"]
type RgbColor = tuple[float, float, float]
type Vector3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class VideoConfig:
    width: int
    height: int
    fps: int
    jpeg_quality: int
    max_frame_bytes: int


@dataclass(frozen=True, slots=True)
class TimingConfig:
    max_interpolation_gap_s: float
    max_segment_frames: int


@dataclass(frozen=True, slots=True)
class CameraPose:
    position_normalized: Vector3
    target_normalized: Vector3
    view_up: Vector3
    view_angle_deg: float


@dataclass(frozen=True, slots=True)
class MaterialConfig:
    color: RgbColor
    metallic: float
    roughness: float


@dataclass(frozen=True, slots=True)
class LightConfig:
    position_normalized: Vector3
    color: RgbColor
    intensity: float


@dataclass(frozen=True, slots=True)
class ImageEffectsConfig:
    noise_standard_deviation: float
    vignette_strength: float


@dataclass(frozen=True, slots=True)
class SyntheticCameraConfig:
    """Complete, versioned synthetic camera profile."""

    version: Literal[1]
    backend: RenderBackend
    video: VideoConfig
    timing: TimingConfig
    camera: CameraPose
    background_color: RgbColor
    floor_material: MaterialConfig
    wall_material: MaterialConfig
    scrap_material: MaterialConfig
    chute_material: MaterialConfig
    lights: tuple[LightConfig, ...]
    effects: ImageEffectsConfig

    @classmethod
    def from_file(cls, path: str | None = None) -> SyntheticCameraConfig:
        from .config import load_camera_config

        return load_camera_config(path)

    def validate(self) -> None:
        from .config import validate_camera_config

        validate_camera_config(self)


@dataclass(frozen=True, slots=True)
class InterpolatedFrame:
    frame: SceneFrame
    left_sequence: int
    right_sequence: int
    alpha: float
    mode: InterpolationMode
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class FrameTarget:
    elapsed_s: float
    mode: InterpolationMode
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RenderedCameraFrame:
    jpeg: bytes
    sequence: int
    elapsed_s: float
    width: int
    height: int
    render_backend: str


@dataclass(frozen=True, slots=True)
class StreamDescriptor:
    type: Literal["camera_stream_descriptor"]
    version: Literal[1]
    format: Literal["MJPEG"]
    width: int
    height: int
    fps: int
    max_frame_bytes: int
