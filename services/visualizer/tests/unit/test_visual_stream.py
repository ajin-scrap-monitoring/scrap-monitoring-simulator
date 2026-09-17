from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.routing import APIWebSocketRoute

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneSegment,
    materialize_keyframe,
)
from scrap_monitoring_visualizer.geometry import build_scene_geometry_topology
from scrap_monitoring_visualizer.live import LiveCoordinator
from scrap_monitoring_visualizer.state import ExecutionState
from scrap_monitoring_visualizer.synthetic_camera.models import (
    InterpolatedFrame,
    SyntheticCameraConfig,
)
from scrap_monitoring_visualizer.visual_stream import (
    LatestVisualStore,
    _descriptor,
    encode_visual_snapshot,
    install_visual_routes,
)

CONTRACT_ROOT = Path("../contracts/scene/v2")


def _records() -> tuple[SceneDefinition, SceneSegment]:
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
    return definition, segment


def _frame(target_id: int) -> InterpolatedFrame:
    definition, segment = _records()
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


def test_visual_descriptor_matches_static_topology_and_camera_materials() -> None:
    definition, _ = _records()
    config = SyntheticCameraConfig.from_file()
    topology = build_scene_geometry_topology(definition)

    descriptor = _descriptor(definition, config)
    model = descriptor["model"]

    assert descriptor["version"] == 1
    assert descriptor["fps"] == 30
    assert model == {
        "boundary_xy_m": definition.scene.boundary_xy_m,
        "floor_z_m": definition.scene.floor_z_m,
        "top_z_m": definition.scene.top_z_m,
        "inlet_positions_xy_m": definition.scene.inlet_positions_xy_m,
        "surface_grid": {
            "x_coordinates_m": definition.scene.surface.x_coordinates_m,
            "y_coordinates_m": definition.scene.surface.y_coordinates_m,
        },
        "topology": {
            "floor": {
                "vertices_m": topology.floor.vertices,
                "faces": topology.floor.faces,
            },
            "walls": {
                "vertices_m": topology.walls.vertices,
                "faces": topology.walls.faces,
            },
            "surface": {
                "vertices_xy_m": topology.surface_xy,
                "faces": topology.surface_faces,
                "boundary_edges": topology.surface_boundary_edges,
            },
        },
        "background_color": config.background_color,
        "materials": {
            "floor": {
                "color": config.floor_material.color,
                "metallic": config.floor_material.metallic,
                "roughness": config.floor_material.roughness,
            },
            "wall": {
                "color": config.wall_material.color,
                "metallic": config.wall_material.metallic,
                "roughness": config.wall_material.roughness,
            },
            "scrap": {
                "color": config.scrap_material.color,
                "metallic": config.scrap_material.metallic,
                "roughness": config.scrap_material.roughness,
            },
        },
    }


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


@pytest.mark.parametrize("source_change", ["disconnected", "new_run"])
def test_visual_route_reconnects_when_the_source_changes(source_change: str) -> None:
    class FakeWebSocket:
        def __init__(self, change_definition: Callable[[], None]) -> None:
            self.accepted = False
            self.text_messages: list[str] = []
            self.close_calls: list[tuple[int, str | None]] = []
            self._change_definition = change_definition

        async def accept(self) -> None:
            self.accepted = True

        async def send_text(self, message: str) -> None:
            self.text_messages.append(message)
            self._change_definition()

        async def send_bytes(self, message: bytes) -> None:
            raise AssertionError(f"stale frame sent after run change: {len(message)}")

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.close_calls.append((code, reason))

        async def receive(self) -> dict[str, str]:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def exercise() -> None:
        definition, _ = _records()
        current_definition: SceneDefinition | None = definition

        def change_definition() -> None:
            nonlocal current_definition
            current_definition = (
                None
                if source_change == "disconnected"
                else replace(definition, run_id=f"{definition.run_id}-next")
            )

        app = FastAPI()
        install_visual_routes(
            app,
            LatestVisualStore(),
            lambda: current_definition,
            SyntheticCameraConfig.from_file(),
        )
        route = next(
            route
            for route in app.routes
            if isinstance(route, APIWebSocketRoute)
            and route.path == "/visual/v1/stream"
        )
        websocket = FakeWebSocket(change_definition)

        await route.endpoint(cast(Any, websocket))

        assert websocket.accepted
        assert len(websocket.text_messages) == 1
        assert websocket.close_calls == [(1012, "visual source changed")]

    asyncio.run(exercise())


def test_live_coordinator_hides_definition_and_clears_visual_on_disconnect() -> None:
    definition, segment = _records()
    store = LatestVisualStore()
    store.publish(_frame(10), b"\xff\xd8live\xff\xd9")
    coordinator = LiveCoordinator(store)
    connected = ExecutionState(
        header=definition,
        segment=segment,
        connected=True,
    )
    coordinator.state_changed(connected)
    assert coordinator.definition is definition
    assert store.get() is not None

    coordinator.state_changed(replace(connected, connected=False))

    assert coordinator.definition is None
    assert store.get() is None
