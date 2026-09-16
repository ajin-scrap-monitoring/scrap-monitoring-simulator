"""Strict loading and validation for synthetic camera profiles."""

from __future__ import annotations

import json
import math
from importlib.resources import files
from pathlib import Path
from typing import Any, Never, cast

from .models import (
    CameraPose,
    ImageEffectsConfig,
    LightConfig,
    MaterialConfig,
    RenderBackend,
    RgbColor,
    SyntheticCameraConfig,
    TimingConfig,
    Vector3,
    VideoConfig,
)

_TOP_LEVEL_KEYS = {
    "version",
    "backend",
    "video",
    "timing",
    "camera",
    "background_color",
    "materials",
    "lights",
    "effects",
}
_MAX_PROFILE_BYTES = 65_536
_MAX_LIGHTS = 8


def _reject_constant(value: str) -> Never:
    raise ValueError(f"non-finite JSON number: {value}")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _mapping(value: Any, name: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    unexpected = set(value) - keys
    missing = keys - set(value)
    if unexpected:
        raise ValueError(f"{name} has unknown fields: {sorted(unexpected)}")
    if missing:
        raise ValueError(f"{name} is missing fields: {sorted(missing)}")
    return cast(dict[str, Any], value)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _vector(value: Any, name: str) -> Vector3:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} must contain three numbers")
    return cast(Vector3, tuple(_number(item, name) for item in value))


def _color(value: Any, name: str) -> RgbColor:
    color = _vector(value, name)
    if any(component < 0.0 or component > 1.0 for component in color):
        raise ValueError(f"{name} components must be between 0 and 1")
    return color


def _material(value: Any, name: str) -> MaterialConfig:
    item = _mapping(value, name, {"color", "metallic", "roughness"})
    return MaterialConfig(
        color=_color(item["color"], f"{name}.color"),
        metallic=_number(item["metallic"], f"{name}.metallic"),
        roughness=_number(item["roughness"], f"{name}.roughness"),
    )


def _from_document(document: Any) -> SyntheticCameraConfig:
    root = _mapping(document, "camera profile", _TOP_LEVEL_KEYS)
    if _integer(root["version"], "version") != 1:
        raise ValueError("camera profile version must be 1")
    backend = root["backend"]
    if not isinstance(backend, str) or backend not in {"auto", "osmesa", "egl"}:
        raise ValueError("backend must be auto, osmesa or egl")

    video = _mapping(
        root["video"],
        "video",
        {"width", "height", "fps", "jpeg_quality", "max_frame_bytes"},
    )
    timing = _mapping(
        root["timing"],
        "timing",
        {"max_interpolation_gap_s", "max_segment_frames"},
    )
    camera = _mapping(
        root["camera"],
        "camera",
        {"position_normalized", "target_normalized", "view_up", "view_angle_deg"},
    )
    materials = _mapping(
        root["materials"],
        "materials",
        {"floor", "wall", "scrap", "chute"},
    )
    effects = _mapping(
        root["effects"],
        "effects",
        {"noise_standard_deviation", "vignette_strength"},
    )
    raw_lights = root["lights"]
    if not isinstance(raw_lights, list) or not raw_lights:
        raise ValueError("lights must be a non-empty array")
    if len(raw_lights) > _MAX_LIGHTS:
        raise ValueError(f"lights must contain at most {_MAX_LIGHTS} entries")
    lights: list[LightConfig] = []
    for index, raw_light in enumerate(raw_lights):
        light = _mapping(
            raw_light,
            f"lights[{index}]",
            {"position_normalized", "color", "intensity"},
        )
        lights.append(
            LightConfig(
                position_normalized=_vector(
                    light["position_normalized"],
                    f"lights[{index}].position_normalized",
                ),
                color=_color(light["color"], f"lights[{index}].color"),
                intensity=_number(light["intensity"], f"lights[{index}].intensity"),
            )
        )

    config = SyntheticCameraConfig(
        version=1,
        backend=cast(RenderBackend, backend),
        video=VideoConfig(
            width=_integer(video["width"], "video.width"),
            height=_integer(video["height"], "video.height"),
            fps=_integer(video["fps"], "video.fps"),
            jpeg_quality=_integer(video["jpeg_quality"], "video.jpeg_quality"),
            max_frame_bytes=_integer(video["max_frame_bytes"], "video.max_frame_bytes"),
        ),
        timing=TimingConfig(
            max_interpolation_gap_s=_number(
                timing["max_interpolation_gap_s"],
                "timing.max_interpolation_gap_s",
            ),
            max_segment_frames=_integer(
                timing["max_segment_frames"], "timing.max_segment_frames"
            ),
        ),
        camera=CameraPose(
            position_normalized=_vector(
                camera["position_normalized"], "camera.position_normalized"
            ),
            target_normalized=_vector(
                camera["target_normalized"], "camera.target_normalized"
            ),
            view_up=_vector(camera["view_up"], "camera.view_up"),
            view_angle_deg=_number(camera["view_angle_deg"], "camera.view_angle_deg"),
        ),
        background_color=_color(root["background_color"], "background_color"),
        floor_material=_material(materials["floor"], "materials.floor"),
        wall_material=_material(materials["wall"], "materials.wall"),
        scrap_material=_material(materials["scrap"], "materials.scrap"),
        chute_material=_material(materials["chute"], "materials.chute"),
        lights=tuple(lights),
        effects=ImageEffectsConfig(
            noise_standard_deviation=_number(
                effects["noise_standard_deviation"],
                "effects.noise_standard_deviation",
            ),
            vignette_strength=_number(
                effects["vignette_strength"], "effects.vignette_strength"
            ),
        ),
    )
    config.validate()
    return config


def validate_camera_config(config: SyntheticCameraConfig) -> None:
    video = config.video
    if video.width <= 0 or video.height <= 0:
        raise ValueError("video dimensions must be positive")
    if video.width > 3_840 or video.height > 2_160:
        raise ValueError("video dimensions exceed the configured limit")
    if video.fps <= 0 or video.fps > 60:
        raise ValueError("video fps must be between 1 and 60")
    if video.jpeg_quality < 1 or video.jpeg_quality > 95:
        raise ValueError("JPEG quality must be between 1 and 95")
    if video.max_frame_bytes <= 0 or video.max_frame_bytes > 4_194_304:
        raise ValueError("maximum JPEG size must be between 1 and 4194304 bytes")
    timing = config.timing
    if (
        not math.isfinite(timing.max_interpolation_gap_s)
        or timing.max_interpolation_gap_s <= 0.0
    ):
        raise ValueError("maximum interpolation gap must be positive")
    if timing.max_segment_frames <= 0 or timing.max_segment_frames > 3_000:
        raise ValueError("maximum segment frame count must be between 1 and 3000")
    if not 1.0 <= config.camera.view_angle_deg < 180.0:
        raise ValueError("camera view angle must be between 1 and 180 degrees")
    view_up_length = math.sqrt(sum(value * value for value in config.camera.view_up))
    if not math.isclose(view_up_length, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("camera view_up must be a unit vector")
    if config.camera.position_normalized == config.camera.target_normalized:
        raise ValueError("camera position and target must differ")
    direction = tuple(
        target - position
        for position, target in zip(
            config.camera.position_normalized,
            config.camera.target_normalized,
            strict=True,
        )
    )
    direction_length = math.sqrt(sum(value * value for value in direction))
    alignment = (
        sum(
            value * view_up
            for value, view_up in zip(direction, config.camera.view_up, strict=True)
        )
        / direction_length
    )
    if math.isclose(abs(alignment), 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("camera view_up must not be parallel to the view direction")
    colors = (
        config.background_color,
        config.floor_material.color,
        config.wall_material.color,
        config.scrap_material.color,
        config.chute_material.color,
        *(light.color for light in config.lights),
    )
    if any(
        not math.isfinite(component) or component < 0.0 or component > 1.0
        for color in colors
        for component in color
    ):
        raise ValueError("color components must be finite and between 0 and 1")
    for name, material in (
        ("floor", config.floor_material),
        ("wall", config.wall_material),
        ("scrap", config.scrap_material),
        ("chute", config.chute_material),
    ):
        if not 0.0 <= material.metallic <= 1.0:
            raise ValueError(f"{name} metallic value must be between 0 and 1")
        if not 0.0 <= material.roughness <= 1.0:
            raise ValueError(f"{name} roughness value must be between 0 and 1")
    if not 1 <= len(config.lights) <= _MAX_LIGHTS:
        raise ValueError(f"lights must contain between 1 and {_MAX_LIGHTS} entries")
    if any(
        not math.isfinite(light.intensity) or light.intensity <= 0.0
        for light in config.lights
    ):
        raise ValueError("light intensity must be positive")
    if not 0.0 <= config.effects.noise_standard_deviation <= 0.25:
        raise ValueError("noise standard deviation must be between 0 and 0.25")
    if not 0.0 <= config.effects.vignette_strength <= 1.0:
        raise ValueError("vignette strength must be between 0 and 1")


def load_camera_config(path: str | None = None) -> SyntheticCameraConfig:
    if path is None:
        resource = files(
            "scrap_monitoring_visualizer.synthetic_camera.profiles"
        ).joinpath("default.v1.json")
        raw = resource.read_bytes()
    else:
        with Path(path).open("rb") as source:
            raw = source.read(_MAX_PROFILE_BYTES + 1)
    if len(raw) > _MAX_PROFILE_BYTES:
        raise ValueError("camera profile exceeds the configured byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("camera profile must be UTF-8") from error
    try:
        document = json.loads(
            text,
            object_pairs_hook=_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid camera profile JSON at byte {error.pos}") from error
    return _from_document(document)
