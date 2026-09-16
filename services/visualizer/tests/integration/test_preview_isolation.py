from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import uvicorn

from scrap_monitoring_visualizer.contracts import ContractParser
from scrap_monitoring_visualizer.preview import (
    create_preview_app,
)
from scrap_monitoring_visualizer.receiver import SceneReceiver

CONTRACT_ROOT = Path("../contracts/scene/v2")


def test_incomplete_browser_request_does_not_block_tcp_receiver() -> None:
    async def exercise() -> None:
        receiver = SceneReceiver(ContractParser(CONTRACT_ROOT))
        tcp_server = await asyncio.start_server(receiver.handle_client, "127.0.0.1", 0)
        tcp_port = int(tcp_server.sockets[0].getsockname()[1])

        app = create_preview_app(lambda: {"connected": False})
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        http_port = int(listener.getsockname()[1])
        http_server = uvicorn.Server(
            uvicorn.Config(app, lifespan="off", access_log=False, log_level="error")
        )
        http_task = asyncio.create_task(http_server.serve(sockets=[listener]))
        while not http_server.started:
            await asyncio.sleep(0.01)
        slow_reader, slow_writer = await asyncio.open_connection("127.0.0.1", http_port)
        del slow_reader
        slow_writer.write(b"GET /status HTTP/1.1\r\nHost: localhost\r\n")
        await slow_writer.drain()
        try:
            tcp_reader, tcp_writer = await asyncio.open_connection(
                "127.0.0.1", tcp_port
            )
            tcp_writer.write((CONTRACT_ROOT / "fixtures/scene.v2.jsonl").read_bytes())
            await tcp_writer.drain()
            tcp_writer.write_eof()
            assert await asyncio.wait_for(tcp_reader.read(), timeout=2) == b""
            tcp_writer.close()
            await tcp_writer.wait_closed()
            assert receiver.state.segment is not None
            assert receiver.state.segment.sequence == 1
        finally:
            slow_writer.close()
            await slow_writer.wait_closed()
            http_server.should_exit = True
            await http_task
            tcp_server.close()
            await tcp_server.wait_closed()

    asyncio.run(exercise())
