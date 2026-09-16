from __future__ import annotations

import json
from pathlib import Path

import pytest

from scrap_monitoring_visualizer.synthetic_camera import SyntheticCameraConfig


def test_default_camera_profile_is_packaged_and_valid() -> None:
    config = SyntheticCameraConfig.from_file()

    assert config.version == 1
    assert config.backend == "auto"
    assert (config.video.width, config.video.height, config.video.fps) == (
        1920,
        1080,
        30,
    )
    assert config.video.max_frame_bytes == 4_194_304
    assert config.camera.position_normalized == (0.0, -0.485, 1.36)
    assert config.camera.target_normalized == (0.0, 0.125, 0.75)
    assert config.camera.view_angle_deg == 45.0
    assert config.scrap_material.metallic > config.wall_material.metallic


def test_external_camera_profile_override_uses_same_validation(
    tmp_path: Path,
) -> None:
    default = Path(
        "src/scrap_monitoring_visualizer/synthetic_camera/profiles/default.v1.json"
    )
    document = json.loads(default.read_text(encoding="utf-8"))
    document["video"]["fps"] = 24
    override = tmp_path / "camera.json"
    override.write_text(json.dumps(document), encoding="utf-8")

    config = SyntheticCameraConfig.from_file(str(override))

    assert config.video.fps == 24


@pytest.mark.parametrize(
    "mutation",
    [
        lambda profile: profile.update(version=2),
        lambda profile: profile["video"].update(fps=0),
        lambda profile: profile["video"].update(width=3841),
        lambda profile: profile["camera"].update(view_up=[0, 0, 2]),
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
