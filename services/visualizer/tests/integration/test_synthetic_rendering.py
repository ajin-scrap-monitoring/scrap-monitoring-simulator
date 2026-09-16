from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from pathlib import Path

from PIL import Image

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneFrame,
)
from scrap_monitoring_visualizer.synthetic_camera import SyntheticCameraConfig
from scrap_monitoring_visualizer.synthetic_camera.renderer import VtkPbrRenderer

CONTRACT_ROOT = Path("../contracts/scene/v1")


def test_vtk_backend_renders_perspective_mjpeg_source_frame() -> None:
    parser = ContractParser(CONTRACT_ROOT)
    records = tuple(
        parser.parse_line(line).value
        for line in (CONTRACT_ROOT / "fixtures/scene.v1.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    assert isinstance(records[0], SceneDefinition)
    assert isinstance(records[1], SceneFrame)
    config = SyntheticCameraConfig.from_file()
    config = replace(
        config,
        video=replace(config.video, width=320, height=180, jpeg_quality=70),
        effects=replace(
            config.effects,
            noise_standard_deviation=0.0,
            vignette_strength=0.0,
        ),
    )

    renderer = VtkPbrRenderer()
    try:
        frame = renderer.render(records[0], records[1], config)
        updated = renderer.render(
            records[0],
            replace(
                records[1],
                sequence=records[1].sequence + 1,
                surface=replace(
                    records[1].surface,
                    heights_m=tuple(
                        tuple(height + 0.05 for height in row)
                        for row in records[1].surface.heights_m
                    ),
                ),
            ),
            config,
        )
    finally:
        renderer.close()

    assert frame.jpeg.startswith(b"\xff\xd8")
    assert frame.jpeg.endswith(b"\xff\xd9")
    assert frame.render_backend.startswith("vtk")
    assert updated.jpeg != frame.jpeg
    with Image.open(BytesIO(frame.jpeg)) as image:
        assert image.format == "JPEG"
        assert image.size == (320, 180)
