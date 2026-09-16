"""Validate the synthetic camera renderer inside the release image."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, replace
from io import BytesIO
from pathlib import Path

from PIL import Image

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneFrame,
)

from .models import SyntheticCameraConfig
from .renderer import VtkPbrRenderer


@dataclass(frozen=True, slots=True)
class SyntheticCameraProbeResult:
    width: int
    height: int
    format: str
    perspective: bool
    render_backend: str
    frame_bytes: int
    frame_path: str


def run_probe(
    output_dir: Path,
    contract_root: Path,
) -> SyntheticCameraProbeResult:
    output_dir.mkdir(parents=True, exist_ok=False)
    parser = ContractParser(contract_root)
    records = tuple(
        parser.parse_line(line).value
        for line in (contract_root / "fixtures/scene.v1.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    definition, scene_frame = records
    if not isinstance(definition, SceneDefinition) or not isinstance(
        scene_frame, SceneFrame
    ):
        raise RuntimeError("contract fixture does not contain a header and frame")
    config = SyntheticCameraConfig.from_file()
    config = replace(
        config,
        video=replace(
            config.video,
            raster_width=320,
            raster_height=180,
        ),
    )
    renderer = VtkPbrRenderer()
    try:
        rendered = renderer.render(definition, scene_frame, config)
    finally:
        renderer.close()
    with Image.open(BytesIO(rendered.jpeg)) as image:
        image.load()
        if image.format != "JPEG" or image.size != (1920, 1080):
            raise RuntimeError("synthetic camera produced an unexpected JPEG")
    frame_path = output_dir / "camera.jpg"
    frame_path.write_bytes(rendered.jpeg)
    result = SyntheticCameraProbeResult(
        width=rendered.width,
        height=rendered.height,
        format="MJPEG",
        perspective=True,
        render_backend=rendered.render_backend,
        frame_bytes=len(rendered.jpeg),
        frame_path=frame_path.name,
    )
    (output_dir / "camera.json").write_text(
        json.dumps(asdict(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/camera-probe"))
    parser.add_argument(
        "--contracts",
        type=Path,
        default=Path("/workspace/contracts/scene/v1"),
    )
    args = parser.parse_args()
    print(json.dumps(asdict(run_probe(args.output, args.contracts)), sort_keys=True))


if __name__ == "__main__":
    main()
