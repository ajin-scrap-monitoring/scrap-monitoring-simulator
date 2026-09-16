from __future__ import annotations

import json
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneSegment,
    materialize_keyframe,
)
from scrap_monitoring_visualizer.synthetic_camera.models import InterpolatedFrame
from scrap_monitoring_visualizer.visual_stream import (
    LatestVisualStore,
    encode_visual_snapshot,
)

CONTRACT_ROOT = Path("../contracts/scene/v2")


def _frame(target_id: int) -> InterpolatedFrame:
    parser = ContractParser(CONTRACT_ROOT)
    lines = (
        (CONTRACT_ROOT / "fixtures/scene.v2.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    definition = parser.parse_line(lines[0]).value
    segment = parser.parse_line(lines[1]).value
    assert isinstance(definition, SceneDefinition)
    assert isinstance(segment, SceneSegment)
    frame = materialize_keyframe(
        definition, segment.right, segment.right_sequence, segment.run_id
    )
    return InterpolatedFrame(
        frame=replace(
            frame,
            scenario=replace(frame.scenario, elapsed_s=target_id / 30.0),
        ),
        left_sequence=segment.left_sequence,
        right_sequence=segment.right_sequence,
        alpha=0.5,
        mode="interpolated",
        left_inlet_index=0,
        right_inlet_index=0,
        target_id=target_id,
    )


def test_latest_visual_store_replaces_only_with_newer_target() -> None:
    store = LatestVisualStore()
    first = store.publish(_frame(10), b"\xff\xd8first\xff\xd9")
    rejected = store.publish(_frame(9), b"\xff\xd8older\xff\xd9")
    replacement = store.publish(_frame(11), b"\xff\xd8next\xff\xd9")

    assert rejected is first
    assert store.get() is replacement
    assert replacement.jpeg == b"\xff\xd8next\xff\xd9"
    store.clear()
    assert store.get() is None


def test_visual_packet_has_json_header_float32_heights_and_jpeg() -> None:
    snapshot = LatestVisualStore().publish(_frame(11), b"\xff\xd8frame\xff\xd9")

    packet = encode_visual_snapshot(snapshot)
    header_length = struct.unpack(">I", packet[:4])[0]
    header_end = 4 + header_length
    metadata = json.loads(packet[4:header_end])
    heights_end = header_end + metadata["height_count"] * 4

    assert metadata == {
        "target_id": 11,
        "target_elapsed_s": 11 / 30.0,
        "left_sequence": 0,
        "right_sequence": 1,
        "alpha": 0.5,
        "height_count": 4,
        "jpeg_bytes": 9,
        "shared": {
            "cycle_index": 0,
            "phase": "filling",
            "target_fill_ratio": 0.9,
            "surface_fill_ratio": 0.1,
            "surface_volume_m3": 0.1,
            "current_inlet_index": 0,
        },
    }
    assert struct.unpack("<4f", packet[header_end:heights_end]) == pytest.approx(
        (0.0, 0.1, 0.2, 0.3)
    )
    assert packet[heights_end:] == b"\xff\xd8frame\xff\xd9"
