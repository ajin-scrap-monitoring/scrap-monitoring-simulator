from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import MappingProxyType

import pytest
from PIL import Image

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneSegment,
    materialize_keyframe,
)
from scrap_monitoring_visualizer.synthetic_camera import SyntheticCameraConfig
from scrap_monitoring_visualizer.synthetic_camera.renderer import VtkPbrRenderer

CONTRACT_ROOT = Path("../contracts/scene/v2")


def test_vtk_backend_renders_perspective_mjpeg_source_frame() -> None:
    parser = ContractParser(CONTRACT_ROOT)
    records = tuple(
        parser.parse_line(line).value
        for line in (CONTRACT_ROOT / "fixtures/scene.v2.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    assert isinstance(records[0], SceneDefinition)
    assert isinstance(records[1], SceneSegment)
    scene_frame = materialize_keyframe(
        records[0], records[1].right, records[1].right_sequence, records[1].run_id
    )
    config = SyntheticCameraConfig.from_file()
    config = replace(
        config,
        video=replace(
            config.video,
            raster_width=160,
            raster_height=90,
            jpeg_quality=70,
        ),
        effects=replace(
            config.effects,
            noise_standard_deviation=0.0,
            vignette_strength=0.0,
        ),
    )

    renderer = VtkPbrRenderer()
    try:
        frame = renderer.render(records[0], scene_frame, config)
        updated = renderer.render(
            records[0],
            replace(
                scene_frame,
                sequence=scene_frame.sequence + 1,
                surface=replace(
                    scene_frame.surface,
                    heights_m=tuple(
                        tuple(height + 0.05 for height in row)
                        for row in scene_frame.surface.heights_m
                    ),
                ),
            ),
            config,
        )
        plotter = renderer._plotter
        assert plotter is not None
        actors = tuple(plotter.actors.values())
        assert actors
        assert all(not actor.HasTranslucentPolygonalGeometry() for actor in actors)
    finally:
        renderer.close()

    assert frame.jpeg.startswith(b"\xff\xd8")
    assert frame.jpeg.endswith(b"\xff\xd9")
    assert frame.render_backend.startswith("vtk")
    assert isinstance(frame.stage_seconds, MappingProxyType)
    assert set(frame.stage_seconds) == {
        "scene_update",
        "vtk_render",
        "framebuffer_readback",
        "effects",
        "resize",
        "jpeg_encode",
    }
    assert all(duration >= 0.0 for duration in frame.stage_seconds.values())
    with pytest.raises(TypeError):
        frame.stage_seconds["vtk_render"] = 0.0  # type: ignore[index]
    assert updated.jpeg != frame.jpeg
    with Image.open(BytesIO(frame.jpeg)) as image:
        assert image.format == "JPEG"
        assert image.size == (1920, 1080)
