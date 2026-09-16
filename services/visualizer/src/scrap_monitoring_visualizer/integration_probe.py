"""Verify the live browser and synthetic camera boundaries of a running server."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import time
import urllib.error
import urllib.request
from typing import Any, cast

from PIL import Image
from websockets.asyncio.client import connect


def _get(url: str, timeout_s: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return response.status, response.read()


def _wait_for_live_status(base_url: str, deadline: float) -> dict[str, Any]:
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status_code, body = _get(f"{base_url}/status", 3.0)
            value: object = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError("status response must be an object")
            status = cast(dict[str, Any], value)
            camera = status.get("synthetic_camera", {})
            if (
                status_code == 200
                and status.get("connected") is True
                and isinstance(status.get("received_sequence"), int)
                and isinstance(status.get("rendered_sequence"), int)
                and camera.get("camera_enabled") is True
                and camera.get("camera_width") == 1920
                and camera.get("camera_height") == 1080
                and camera.get("camera_fps") == 30
                and isinstance(camera.get("camera_rendered_sequence"), int)
                and camera.get("camera_render_error") is None
            ):
                return status
        except (OSError, ValueError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(0.25)
    if last_error is not None:
        raise RuntimeError("live status did not become ready") from last_error
    raise RuntimeError("live status did not become ready before the deadline")


async def _check_camera(websocket_url: str, timeout_s: float) -> dict[str, Any]:
    async with connect(
        websocket_url,
        open_timeout=timeout_s,
        close_timeout=2.0,
        max_queue=1,
        max_size=4_194_304,
        compression=None,
    ) as websocket:
        descriptor_message = await asyncio.wait_for(websocket.recv(), timeout_s)
        frame_message = await asyncio.wait_for(websocket.recv(), timeout_s)
    if not isinstance(descriptor_message, str):
        raise RuntimeError("camera descriptor is not a text frame")
    descriptor = json.loads(descriptor_message)
    expected = {
        "type": "camera_stream_descriptor",
        "version": 1,
        "format": "MJPEG",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "max_frame_bytes": 4_194_304,
    }
    if descriptor != expected:
        raise RuntimeError(f"unexpected camera descriptor: {descriptor}")
    if not isinstance(frame_message, bytes):
        raise RuntimeError("camera frame is not binary")
    with Image.open(io.BytesIO(frame_message)) as image:
        image.load()
        if image.format != "JPEG" or image.size != (1920, 1080):
            raise RuntimeError(
                f"unexpected camera frame: format={image.format} size={image.size}"
            )
    return {"descriptor": descriptor, "jpeg_bytes": len(frame_message)}


def run(base_url: str, timeout_s: float) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    deadline = time.monotonic() + timeout_s
    status = _wait_for_live_status(base_url, deadline)
    root_status, root_body = _get(f"{base_url}/", 3.0)
    frame_status, frame_body = _get(f"{base_url}/frame.png", 5.0)
    if root_status != 200 or b'new URL("/camera/v1/stream"' not in root_body:
        raise RuntimeError("browser page does not expose both live views")
    if frame_status != 200 or not frame_body.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("browser renderer did not expose a PNG frame")
    remaining_s = deadline - time.monotonic()
    if remaining_s <= 0.0:
        raise RuntimeError("integration deadline expired before camera validation")
    websocket_url = base_url.replace("http://", "ws://", 1).replace(
        "https://", "wss://", 1
    )
    camera = asyncio.run(
        _check_camera(f"{websocket_url}/camera/v1/stream", remaining_s)
    )
    synthetic_camera = status["synthetic_camera"]
    return {
        "received_sequence": status["received_sequence"],
        "rendered_sequence": status["rendered_sequence"],
        "camera_rendered_sequence": synthetic_camera["camera_rendered_sequence"],
        "camera_render_backend": synthetic_camera["camera_render_backend"],
        "browser_png_bytes": len(frame_body),
        **camera,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.timeout <= 0.0:
        parser.error("--timeout must be positive")
    print(json.dumps(run(args.base_url, args.timeout), sort_keys=True))


if __name__ == "__main__":
    main()
