from __future__ import annotations

import json
from pathlib import Path

import pytest

from scrap_monitoring_visualizer.synthetic_camera import SyntheticCameraConfig


def test_default_camera_profile_is_packaged_and_valid() -> None:
    config = SyntheticCameraConfig.from_file()

    assert config.version == 1
    assert config.backend == "osmesa"
    assert (config.video.width, config.video.height, config.video.fps) == (
        1920,
        1080,
        30,
    )
    assert (config.video.raster_width, config.video.raster_height) == (576, 324)
    assert config.video.jpeg_quality == 60
    assert config.video.max_frame_bytes == 4_194_304
    assert config.camera.position_normalized == (
        0.0,
        -0.354685843,
        1.448540394,
    )
    assert config.camera.target_normalized == (
        0.0,
        0.255314157,
        0.498540394,
    )
    assert config.camera.view_angle_deg == 45.0
    assert config.machine.conveyor_width_m == 1.0
    assert config.machine.outlet_width_m == 0.9
    assert config.machine.conveyor_length_m == 2.0
    assert config.machine.conveyor_center_above_wall_m == 1.25
    assert config.machine.conveyor_body_height_m == 0.24
    assert config.machine.duct_height_m == 0.24
    assert config.machine.tip_fraction == 0.75
    assert config.machine.tip_drop_m == 0.08
    assert config.scrap_material.metallic > config.wall_material.metallic


def test_external_camera_profile_override_uses_same_validation(
    tmp_path: Path,
) -> None:
    default = Path(
        "src/scrap_monitoring_visualizer/synthetic_camera/profiles/default.v1.json"
    )
    document = json.loads(default.read_text(encoding="utf-8"))
    document["video"]["jpeg_quality"] = 80
    override = tmp_path / "camera.json"
    override.write_text(json.dumps(document), encoding="utf-8")

    config = SyntheticCameraConfig.from_file(str(override))

    assert config.video.jpeg_quality == 80


@pytest.mark.parametrize(
    ("field", "value"),
    [("width", 1280), ("height", 720), ("fps", 24)],
)
def test_camera_profile_rejects_non_v1_output_contract(
    tmp_path: Path,
    field: str,
    value: int,
) -> None:
    default = Path(
        "src/scrap_monitoring_visualizer/synthetic_camera/profiles/default.v1.json"
    )
    document = json.loads(default.read_text(encoding="utf-8"))
    document["video"][field] = value
    if field in {"width", "height"}:
        document["video"]["raster_width"] = 640
        document["video"]["raster_height"] = 360
    override = tmp_path / "camera.json"
    override.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="1920 x 1080 at 30 FPS"):
        SyntheticCameraConfig.from_file(str(override))


def test_external_v1_profile_without_raster_dimensions_remains_valid(
    tmp_path: Path,
) -> None:
    default = Path(
        "src/scrap_monitoring_visualizer/synthetic_camera/profiles/default.v1.json"
    )
    document = json.loads(default.read_text(encoding="utf-8"))
    del document["video"]["raster_width"]
    del document["video"]["raster_height"]
    override = tmp_path / "camera.json"
    override.write_text(json.dumps(document), encoding="utf-8")

    config = SyntheticCameraConfig.from_file(str(override))

    assert config.video.raster_width == config.video.width
    assert config.video.raster_height == config.video.height


def test_open_chute_clearance_uses_the_body_below_the_deck(tmp_path: Path) -> None:
    default = Path(
        "src/scrap_monitoring_visualizer/synthetic_camera/profiles/default.v1.json"
    )
    document = json.loads(default.read_text(encoding="utf-8"))
    document["machine"].update(
        conveyor_center_above_wall_m=0.13,
        duct_height_m=0.5,
    )
    override = tmp_path / "camera.json"
    override.write_text(json.dumps(document), encoding="utf-8")

    config = SyntheticCameraConfig.from_file(str(override))

    assert config.machine.conveyor_center_above_wall_m == 0.13


@pytest.mark.parametrize(
    "mutation",
    [
        lambda profile: profile.update(version=2),
        lambda profile: profile["video"].update(fps=0),
        lambda profile: profile["video"].update(width=3841),
        lambda profile: profile["video"].update(raster_width=1920),
        lambda profile: profile["video"].update(raster_height=541),
        lambda profile: profile["camera"].update(view_up=[0, 0, 2]),
        lambda profile: profile["machine"].update(conveyor_width_m=0),
        lambda profile: profile["machine"].update(outlet_width_m=1.1),
        lambda profile: profile["machine"].update(outlet_width_m=0.1),
        lambda profile: profile["machine"].update(conveyor_center_above_wall_m=0.1),
        lambda profile: profile["machine"].update(duct_height_m=0.2),
        lambda profile: profile["machine"].update(tip_fraction=0.74),
        lambda profile: profile["machine"].update(tip_drop_m=0.2),
        lambda profile: profile["materials"]["scrap"].update(metallic=1.1),
        lambda profile: profile.update(unexpected=True),
    ],
)
def test_camera_profile_rejects_invalid_values(
    tmp_path: Path, mutation: object
) -> None:
    default = Path(
        "src/scrap_monitoring_visualizer/synthetic_camera/profiles/default.v1.json"
    )
    document = json.loads(default.read_text(encoding="utf-8"))
    assert callable(mutation)
    mutation(document)
    override = tmp_path / "invalid.json"
    override.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError):
        SyntheticCameraConfig.from_file(str(override))


def test_camera_profile_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    override = tmp_path / "duplicate.json"
    override.write_text('{"version":1,"version":1}', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON key"):
        SyntheticCameraConfig.from_file(str(override))


def test_camera_profile_normalizes_integer_float_overflow(tmp_path: Path) -> None:
    default = Path(
        "src/scrap_monitoring_visualizer/synthetic_camera/profiles/default.v1.json"
    )
    document = json.loads(default.read_text(encoding="utf-8"))
    document["timing"]["max_interpolation_gap_s"] = 10**309
    override = tmp_path / "overflow.json"
    override.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
        ValueError, match="timing.max_interpolation_gap_s must be finite"
    ):
        SyntheticCameraConfig.from_file(str(override))
