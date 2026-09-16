from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import uvicorn

from scrap_monitoring_visualizer.preview import RequestGate, create_preview_app


async def _request(port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode()
    )
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    header_bytes, body = response.split(b"\r\n\r\n", 1)
    header_lines = header_bytes.decode("latin-1").split("\r\n")
    return (
        int(header_lines[0].split()[1]),
        {
            key.lower(): value.strip()
            for key, value in (line.split(":", 1) for line in header_lines[1:])
        },
        body,
    )


def test_request_gate_rejects_excess_concurrency() -> None:
    async def exercise() -> None:
        gate = RequestGate(1)
        assert await gate.acquire() is True
        assert await gate.acquire() is False
        await gate.release()
        assert await gate.acquire() is True
        await gate.release()

    asyncio.run(exercise())


def test_preview_serves_static_shell_and_status_without_png_endpoint(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        static_root = tmp_path / "web"
        assets = static_root / "assets"
        assets.mkdir(parents=True)
        (static_root / "index.html").write_text("<main>WebGL</main>", encoding="utf-8")
        (static_root / "style.css").write_text("body{}", encoding="utf-8")
        (assets / "app.js").write_text("export {};", encoding="utf-8")
        app = create_preview_app(
            lambda: {"connected": True, "visual_target_id": 7},
            static_root=static_root,
        )
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        server = uvicorn.Server(
            uvicorn.Config(app, lifespan="off", access_log=False, log_level="error")
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            await asyncio.sleep(0.01)
        try:
            status, headers, body = await _request(port, "/")
            assert status == 200
            assert headers["cache-control"] == "no-store"
            assert body == b"<main>WebGL</main>"

            status, headers, body = await _request(port, "/status")
            assert status == 200
            assert headers["cache-control"] == "no-store"
            assert body == b'{"connected":true,"visual_target_id":7}'

            status, _, _ = await _request(port, "/frame.png")
            assert status == 404
        finally:
            server.should_exit = True
            await task

    asyncio.run(exercise())
