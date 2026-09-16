from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from scrap_monitoring_visualizer.contracts import ContractParser
from scrap_monitoring_visualizer.receiver import SceneReceiver

CONTRACT_ROOT = Path("../contracts/scene/v1")
FIXTURE_PATH = CONTRACT_ROOT / "fixtures/scene.v1.jsonl"


async def _start(
    receiver: SceneReceiver,
) -> tuple[asyncio.Server, str, int]:
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


def _fixture_lines() -> tuple[bytes, bytes]:
    header, frame = FIXTURE_PATH.read_bytes().splitlines(keepends=True)
    return header, frame


def _frame(sequence: int, elapsed_s: float) -> bytes:
    _, line = _fixture_lines()
    document: dict[str, Any] = json.loads(line)
    document["sequence"] = sequence
    document["scenario"]["elapsed_s"] = elapsed_s
    return json.dumps(document, separators=(",", ":")).encode() + b"\n"


def test_receiver_handles_packet_boundaries_without_response() -> None:
    async def exercise() -> None:
        receiver = SceneReceiver(ContractParser(CONTRACT_ROOT))
        server, host, port = await _start(receiver)
        header, frame = _fixture_lines()
        payload = header + frame

        response = await _send(host, port, (payload[:11], payload[11:73], payload[73:]))
        server.close()
        await server.wait_closed()

        assert response == b""
        assert receiver.snapshot.records_accepted == 2
        assert receiver.state.frame is not None
        assert receiver.state.connected is False

    asyncio.run(exercise())


def test_receiver_rejects_invalid_record_and_keeps_connection() -> None:
    async def exercise() -> None:
        receiver = SceneReceiver(ContractParser(CONTRACT_ROOT))
        server, host, port = await _start(receiver)
        header, _ = _fixture_lines()
        invalid = b'{"type":"scene_frame"}\n'

        await _send(host, port, (header + invalid + _frame(2, 2.0),))
        server.close()
        await server.wait_closed()

        assert receiver.snapshot.records_rejected == 1
        assert receiver.snapshot.records_accepted == 2
        assert receiver.state.frame is not None
        assert receiver.state.frame.sequence == 2
        assert receiver.state.missing_sequences == 1

    asyncio.run(exercise())


def test_receiver_preserves_sequence_across_reconnection() -> None:
    async def exercise() -> None:
        receiver = SceneReceiver(ContractParser(CONTRACT_ROOT))
        server, host, port = await _start(receiver)
        header, frame = _fixture_lines()

        await _send(host, port, (header + frame,))
        await _send(host, port, (header + _frame(3, 3.0),))
        server.close()
        await server.wait_closed()

        assert receiver.state.frame is not None
        assert receiver.state.frame.sequence == 3
        assert receiver.state.connection_index == 2
        assert receiver.state.missing_sequences == 1

    asyncio.run(exercise())


def test_receiver_discards_partial_line() -> None:
    async def exercise() -> None:
        receiver = SceneReceiver(ContractParser(CONTRACT_ROOT))
        server, host, port = await _start(receiver)
        header, _ = _fixture_lines()

        await _send(host, port, (header + b'{"partial":',))
        server.close()
        await server.wait_closed()

        assert receiver.snapshot.partial_lines_discarded == 1
        assert receiver.snapshot.records_accepted == 1

    asyncio.run(exercise())


def test_receiver_accepts_complete_prefix_before_framing_error() -> None:
    async def exercise() -> None:
        receiver = SceneReceiver(ContractParser(CONTRACT_ROOT))
        server, host, port = await _start(receiver)
        header, _ = _fixture_lines()

        await _send(host, port, (header + _frame(1, 1.0) + b"x" * 1_048_576,))
        server.close()
        await server.wait_closed()

        assert receiver.snapshot.records_accepted == 2
        assert receiver.state.frame is not None
        assert receiver.state.frame.sequence == 1
        assert receiver.snapshot.last_error == "record byte limit exceeded"

    asyncio.run(exercise())


def test_receiver_rejects_additional_producer() -> None:
    async def exercise() -> None:
        receiver = SceneReceiver(ContractParser(CONTRACT_ROOT), header_timeout_s=1)
        server, host, port = await _start(receiver)
        first_reader, first_writer = await asyncio.open_connection(host, port)
        await asyncio.sleep(0)
        second_reader, second_writer = await asyncio.open_connection(host, port)

        assert await asyncio.wait_for(second_reader.read(), timeout=1) == b""
        assert receiver.snapshot.connections_rejected == 1
        first_writer.close()
        await first_writer.wait_closed()
        second_writer.close()
        await second_writer.wait_closed()
        del first_reader
        server.close()
        await server.wait_closed()

    asyncio.run(exercise())
