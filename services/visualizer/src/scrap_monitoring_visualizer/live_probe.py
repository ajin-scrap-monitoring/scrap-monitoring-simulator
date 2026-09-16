"""Exercise real TCP, rendering process and HTTP preview boundaries."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
from pathlib import Path

import uvicorn

from scrap_monitoring_visualizer.contracts import ContractParser
from scrap_monitoring_visualizer.live import LiveCoordinator
from scrap_monitoring_visualizer.preview import (
    LatestFrameStore,
    create_preview_app,
)
from scrap_monitoring_visualizer.receiver import SceneReceiver
from scrap_monitoring_visualizer.rendering import RenderConfig
from scrap_monitoring_visualizer.rendering.worker import LatestRenderWorker


async def _http_get(port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode()
    )
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, body = response.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {
        key.lower(): value.strip()
        for key, value in (line.split(":", 1) for line in lines[1:])
    }
    return status, headers, body


async def run_probe(output_dir: Path, contract_root: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=False)
    frames = LatestFrameStore()
    worker = LatestRenderWorker(frames)
    worker.start()
    coordinator = LiveCoordinator(frames, worker, RenderConfig())
    receiver = SceneReceiver(
        ContractParser(contract_root), on_state=coordinator.state_changed
    )
    coordinator.bind_receiver(receiver)
    tcp_server = await asyncio.start_server(receiver.handle_client, "127.0.0.1", 0)
    tcp_port = int(tcp_server.sockets[0].getsockname()[1])

    app = create_preview_app(frames, coordinator.status)
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
    try:
        before_status, _, _ = await _http_get(http_port, "/frame.png")
        reader, writer = await asyncio.open_connection("127.0.0.1", tcp_port)
        payload = (contract_root / "fixtures/scene.v1.jsonl").read_bytes()
        writer.write(payload[:17])
        await writer.drain()
        writer.write(payload[17:])
        await writer.drain()
        writer.write_eof()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()

        for _ in range(500):
            worker.poll()
            if frames.get() is not None:
                break
            await asyncio.sleep(0.02)
        frame_status, frame_headers, frame_body = await _http_get(
            http_port, "/frame.png"
        )
        status_code, _, status_body = await _http_get(http_port, "/status")
        root_status, _, root_body = await _http_get(http_port, "/")
        status_payload = json.loads(status_body)
        if frames.get() is None:
            raise RuntimeError(
                worker.last_error or "live renderer did not produce a frame"
            )
        if not frame_body.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("preview frame is not PNG")
        (output_dir / "live.png").write_bytes(frame_body)
        result: dict[str, object] = {
            "frame_before_frame": before_status,
            "tcp_response_bytes": len(response),
            "frame_status": frame_status,
            "frame_revision": int(frame_headers["x-frame-revision"]),
            "frame_sequence": int(frame_headers["x-sequence"]),
            "status_code": status_code,
            "root_status": root_status,
            "root_has_preview": b"/frame.png?revision=" in root_body,
            "root_has_camera": b'new URL("/camera/v1/stream"' in root_body,
            "connected": status_payload["connected"],
            "received_sequence": status_payload["received_sequence"],
            "rendered_sequence": status_payload["rendered_sequence"],
            "recording_present": "recording" in status_payload,
        }
        (output_dir / "live.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return result
    finally:
        http_server.should_exit = True
        await http_task
        tcp_server.close()
        await tcp_server.wait_closed()
        worker.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/output/live"))
    parser.add_argument(
        "--contracts", type=Path, default=Path("/workspace/contracts/scene/v1")
    )
    args = parser.parse_args()
    print(
        json.dumps(asyncio.run(run_probe(args.output, args.contracts)), sort_keys=True)
    )


if __name__ == "__main__":
    main()
