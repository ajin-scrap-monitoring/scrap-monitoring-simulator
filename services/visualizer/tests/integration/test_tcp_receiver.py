from __future__ import annotations

import asyncio
import json
from typing import Any

from scrap_monitoring_visualizer.contracts import ContractParser
from scrap_monitoring_visualizer.receiver import SceneReceiver


async def _start(receiver: SceneReceiver) -> tuple[asyncio.Server, str, int]:
    server = await asyncio.start_server(receiver.handle_client, "127.0.0.1", 0)
    address = server.sockets[0].getsockname()
    return server, str(address[0]), int(address[1])


async def _send(host: str, port: int, chunks: tuple[bytes, ...]) -> bytes:
    reader, writer = await asyncio.open_connection(host, port)
    for chunk in chunks:
        writer.write(chunk)
        await writer.drain()
    writer.write_eof()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return response


def _records() -> tuple[bytes, bytes]:
    return _line(_definition()), _line(_segment())


def _definition() -> dict[str, Any]:
    return {
        "scene_version": 2,
        "type": "scene_definition",
        "environment_id": "contract-fixture-v2",
        "run_id": "fixture-run-a",
        "input_fingerprint_sha256": "0" * 64,
        "seed": 42,
        "scene": {
            "coordinate_system": "right-handed-z-up",
            "length_unit": "m",
            "angle_unit": "deg",
            "boundary_xy_m": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            "floor_z_m": 0.0,
            "top_z_m": 1.0,
            "inlet_positions_xy_m": [[0.5, 0.5]],
            "sensors": [
                {
                    "sensor_id": "sensor-a",
                    "p0_m": [0.5, 0.5, 1.0],
                    "u0": [0.0, 0.0, -1.0],
                    "u90": [1.0, 0.0, 0.0],
                },
                {
                    "sensor_id": "sensor-b",
                    "p0_m": [0.25, 0.25, 1.0],
                    "u0": [0.0, 0.0, -1.0],
                    "u90": [0.0, 1.0, 0.0],
                },
            ],
            "surface": {
                "cell_size_m": 1.0,
                "x_coordinates_m": [0.0, 1.0],
                "y_coordinates_m": [0.0, 1.0],
            },
        },
    }


def _scenario(elapsed_s: float) -> dict[str, Any]:
    return {
        "elapsed_s": elapsed_s,
        "surface_updated_at_s": elapsed_s,
        "cycle_index": 0,
        "phase": "filling",
        "phase_started_at_s": 0.0,
        "phase_ends_at_s": 10.0,
        "phase_duration_s": 10.0,
        "rate_factor": 1.0,
        "target_fill_ratio": 0.9,
        "surface_fill_ratio": 0.1,
        "surface_volume_m3": 0.1,
        "current_inlet_index": 0,
    }


def _segment(sequence: int = 1, elapsed_s: float = 0.1) -> dict[str, Any]:
    return {
        "scene_version": 2,
        "type": "scene_segment",
        "sequence": sequence,
        "left_sequence": sequence - 1,
        "right_sequence": sequence,
        "run_id": "fixture-run-a",
        "left": {
            "scenario": _scenario(elapsed_s - 0.1),
            "heights_m": [[0.0, 0.1], [0.2, 0.3]],
        },
        "right": {
            "scenario": _scenario(elapsed_s),
            "heights_m": [[0.0, 0.2], [0.3, 0.4]],
        },
    }


def _line(document: dict[str, Any]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode() + b"\n"


def _segment_line(sequence: int, elapsed_s: float) -> bytes:
    document: dict[str, Any] = _segment(sequence, elapsed_s)
    return json.dumps(document, separators=(",", ":")).encode() + b"\n"


def test_receiver_handles_packet_boundaries_without_response() -> None:
    async def exercise() -> None:
        receiver = SceneReceiver(ContractParser())
        server, host, port = await _start(receiver)
        header, segment = _records()
        payload = header + segment
        response = await _send(host, port, (payload[:11], payload[11:73], payload[73:]))
        server.close()
        await server.wait_closed()

        assert response == b""
        assert receiver.snapshot.records_accepted == 2
        assert receiver.state.segment is not None
        assert receiver.state.frame is not None
        assert receiver.state.connected is False

    asyncio.run(exercise())


def test_receiver_rejects_invalid_record_and_keeps_connection() -> None:
    async def exercise() -> None:
        receiver = SceneReceiver(ContractParser())
        server, host, port = await _start(receiver)
        header, _ = _records()
        invalid = b'{"type":"scene_segment"}\n'
        await _send(host, port, (header + invalid + _segment_line(2, 0.2),))
        server.close()
        await server.wait_closed()

        assert receiver.snapshot.records_rejected == 1
        assert receiver.snapshot.records_accepted == 2
        assert receiver.state.segment is not None
        assert receiver.state.segment.sequence == 2
        assert receiver.state.missing_sequences == 1

    asyncio.run(exercise())


def test_receiver_preserves_segment_sequence_across_reconnection() -> None:
    async def exercise() -> None:
        receiver = SceneReceiver(ContractParser())
        server, host, port = await _start(receiver)
        header, segment = _records()
        await _send(host, port, (header + segment,))
        await _send(host, port, (header + _segment_line(3, 0.3),))
        server.close()
        await server.wait_closed()

        assert receiver.state.segment is not None
        assert receiver.state.segment.sequence == 3
        assert receiver.state.connection_index == 2
        assert receiver.state.missing_sequences == 1

    asyncio.run(exercise())


def test_receiver_discards_partial_line() -> None:
    async def exercise() -> None:
        receiver = SceneReceiver(ContractParser())
        server, host, port = await _start(receiver)
        header, _ = _records()
        await _send(host, port, (header + b'{"partial":',))
        server.close()
        await server.wait_closed()

        assert receiver.snapshot.partial_lines_discarded == 1
        assert receiver.snapshot.records_accepted == 1

    asyncio.run(exercise())
