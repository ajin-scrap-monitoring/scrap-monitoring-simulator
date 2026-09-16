"""Verify live Browser pairs and the raw edge camera stream."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import struct
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


def _measure_unique_frames(
    frame_messages: list[bytes], received_at: list[float]
) -> dict[str, int | float]:
    if not frame_messages or len(frame_messages) != len(received_at):
        raise RuntimeError("camera frame sample is incomplete")
    frame_digests = tuple(hashlib.sha256(frame).digest() for frame in frame_messages)
    if len(set(frame_digests)) != len(frame_digests):
        raise RuntimeError(
            "camera stream repeated a JPEG during the unique frame sample"
        )
    network_unique_fps = (
        (len(received_at) - 1) / (received_at[-1] - received_at[0])
        if len(received_at) >= 2 and received_at[-1] > received_at[0]
        else 0.0
    )
    return {
        "network_unique_frames": len(frame_messages),
        "network_unique_fps": round(network_unique_fps, 3),
    }


async def _check_camera(
    websocket_url: str,
    timeout_s: float,
    sample_frames: int = 15,
) -> dict[str, Any]:
    async with asyncio.timeout(timeout_s):
        async with connect(
            websocket_url,
            open_timeout=timeout_s,
            close_timeout=2.0,
            max_queue=1,
            max_size=4_194_304,
            compression=None,
        ) as websocket:
            descriptor_message = await websocket.recv()
            frame_messages: list[bytes] = []
            received_at: list[float] = []
            while len(frame_messages) < sample_frames:
                message = await websocket.recv()
                if not isinstance(message, bytes):
                    raise RuntimeError("camera frame is not binary")
                frame_messages.append(message)
                received_at.append(time.monotonic())
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
    frame_message = frame_messages[-1]
    network_measurement = _measure_unique_frames(frame_messages, received_at)
    with Image.open(io.BytesIO(frame_message)) as image:
        image.load()
        if image.format != "JPEG" or image.size != (1920, 1080):
            raise RuntimeError(
                f"unexpected camera frame: format={image.format} size={image.size}"
            )
    return {
        "descriptor": descriptor,
        "jpeg_bytes": len(frame_message),
        **network_measurement,
    }


def _decode_visual_packet(message: bytes) -> tuple[dict[str, Any], bytes]:
    if len(message) < 4:
        raise RuntimeError("visual packet is shorter than its header")
    header_size = struct.unpack(">I", message[:4])[0]
    if header_size > len(message) - 4:
        raise RuntimeError("visual packet header exceeds packet size")
    metadata = json.loads(message[4 : 4 + header_size])
    if not isinstance(metadata, dict):
        raise RuntimeError("visual packet metadata must be an object")
    height_count = metadata.get("height_count")
    jpeg_bytes = metadata.get("jpeg_bytes")
    if not isinstance(height_count, int) or not isinstance(jpeg_bytes, int):
        raise RuntimeError("visual packet metadata has invalid lengths")
    jpeg_start = 4 + header_size + height_count * 4
    if jpeg_start + jpeg_bytes != len(message):
        raise RuntimeError("visual packet payload lengths are inconsistent")
    return cast(dict[str, Any], metadata), message[jpeg_start:]


async def _check_visual(websocket_url: str, timeout_s: float) -> dict[str, Any]:
    async with asyncio.timeout(timeout_s):
        async with connect(
            websocket_url,
            open_timeout=timeout_s,
            close_timeout=2.0,
            max_queue=1,
            max_size=4_194_304,
            compression=None,
        ) as websocket:
            descriptor_message = await websocket.recv()
            message = await websocket.recv()
    if not isinstance(descriptor_message, str) or not isinstance(message, bytes):
        raise RuntimeError("visual stream did not provide descriptor and packet")
    descriptor = json.loads(descriptor_message)
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("type") != "visual_stream_descriptor"
        or descriptor.get("version") != 1
        or descriptor.get("fps") != 30
    ):
        raise RuntimeError("unexpected visual descriptor")
    metadata, jpeg = _decode_visual_packet(message)
    target_id = metadata.get("target_id")
    left_sequence = metadata.get("left_sequence")
    right_sequence = metadata.get("right_sequence")
    if not isinstance(target_id, int) or not isinstance(left_sequence, int):
        raise RuntimeError("visual packet is missing shared target identity")
    if right_sequence != left_sequence + 1:
        raise RuntimeError("visual packet does not identify an adjacent segment")
    with Image.open(io.BytesIO(jpeg)) as image:
        image.load()
        if image.format != "JPEG" or image.size != (1920, 1080):
            raise RuntimeError("visual packet does not contain the camera JPEG")
    return {
        "target_id": target_id,
        "height_count": metadata["height_count"],
    }


def run(
    base_url: str,
    timeout_s: float,
    minimum_network_fps: float = 20.0,
) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    deadline = time.monotonic() + timeout_s
    _wait_for_live_status(base_url, deadline)
    root_status, root_body = _get(f"{base_url}/", 3.0)
    if root_status != 200 or b"/assets/main.js" not in root_body:
        raise RuntimeError("browser page does not expose the paired visual client")
    remaining_s = deadline - time.monotonic()
    if remaining_s <= 0.0:
        raise RuntimeError("integration deadline expired before stream validation")
    websocket_url = base_url.replace("http://", "ws://", 1).replace(
        "https://", "wss://", 1
    )
    visual = asyncio.run(
        _check_visual(f"{websocket_url}/visual/v1/stream", remaining_s)
    )
    camera = asyncio.run(
        _check_camera(f"{websocket_url}/camera/v1/stream", remaining_s)
    )
    if camera["network_unique_fps"] < minimum_network_fps:
        raise RuntimeError(
            "camera network FPS is below the CI regression floor: "
            f"{camera['network_unique_fps']} < {minimum_network_fps}"
        )
    _, current_status_body = _get(f"{base_url}/status", 3.0)
    current_status = cast(dict[str, Any], json.loads(current_status_body))
    synthetic_camera = current_status["synthetic_camera"]
    return {
        "received_sequence": current_status["received_sequence"],
        "camera_rendered_sequence": synthetic_camera["camera_rendered_sequence"],
        "camera_render_backend": synthetic_camera["camera_render_backend"],
        "camera_source_fps": synthetic_camera["camera_source_fps"],
        "visual": visual,
        **camera,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--minimum-network-fps", type=float, default=20.0)
    args = parser.parse_args()
    if args.timeout <= 0.0:
        parser.error("--timeout must be positive")
    if args.minimum_network_fps <= 0.0:
        parser.error("--minimum-network-fps must be positive")
    print(
        json.dumps(
            run(args.base_url, args.timeout, args.minimum_network_fps), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
