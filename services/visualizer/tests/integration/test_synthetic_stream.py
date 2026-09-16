from __future__ import annotations

import asyncio
import json
import socket
from typing import Any, cast

import uvicorn
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.routing import APIWebSocketRoute
from websockets.asyncio.client import connect

from scrap_monitoring_visualizer.synthetic_camera import (
    LatestJpegStore,
    install_camera_routes,
)


class FakeWebSocket:
    def __init__(self, disconnect_after: int = 1) -> None:
        self.accepted = False
        self.text_messages: list[str] = []
        self.binary_messages: list[bytes] = []
        self.close_calls: list[tuple[int, str | None]] = []
        self.disconnect_after = disconnect_after

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        self.text_messages.append(message)

    async def send_bytes(self, message: bytes) -> None:
        self.binary_messages.append(message)
        if len(self.binary_messages) >= self.disconnect_after:
            raise WebSocketDisconnect()

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_calls.append((code, reason))

    async def receive(self) -> dict[str, str]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def test_camera_websocket_sends_exact_descriptor_then_latest_jpeg() -> None:
    async def exercise() -> None:
        store = LatestJpegStore()
        store.publish(
            b"\xff\xd8latest\xff\xd9",
            sequence=7,
            elapsed_s=3.0,
            render_backend="test",
        )
        app = FastAPI()
        install_camera_routes(
            app,
            store,
            lambda: {
                "camera_width": 1920,
                "camera_height": 1080,
                "camera_frame_revision": 1,
            },
            30,
        )
        route = next(
            route
            for route in app.routes
            if isinstance(route, APIWebSocketRoute)
            and route.path == "/camera/v1/stream"
        )
        websocket = FakeWebSocket()

        await route.endpoint(cast(Any, websocket))

        assert websocket.accepted
        assert json.loads(websocket.text_messages[0]) == {
            "type": "camera_stream_descriptor",
            "version": 1,
            "format": "MJPEG",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "max_frame_bytes": 4_194_304,
        }
        assert websocket.binary_messages == [b"\xff\xd8latest\xff\xd9"]

    asyncio.run(exercise())


def test_camera_routes_do_not_install_a_standalone_page() -> None:
    app = FastAPI()
    install_camera_routes(
        app,
        LatestJpegStore(),
        lambda: {"camera_width": 1920, "camera_height": 1080},
    )

    assert all(getattr(route, "path", None) != "/camera/" for route in app.routes)


def test_camera_websocket_sends_each_store_revision_once() -> None:
    async def exercise() -> None:
        store = LatestJpegStore()
        first = b"\xff\xd8first\xff\xd9"
        second = b"\xff\xd8second\xff\xd9"
        store.publish(
            first,
            sequence=7,
            elapsed_s=3.0,
            render_backend="test",
        )
        app = FastAPI()
        install_camera_routes(
            app,
            store,
            lambda: {"camera_width": 1920, "camera_height": 1080},
            fps=60,
        )
        route = next(
            route
            for route in app.routes
            if isinstance(route, APIWebSocketRoute)
            and route.path == "/camera/v1/stream"
        )
        websocket = FakeWebSocket(disconnect_after=2)
        task = asyncio.create_task(route.endpoint(cast(Any, websocket)))
        while not websocket.binary_messages:
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.05)
        assert websocket.binary_messages == [first]

        store.publish(
            second,
            sequence=8,
            elapsed_s=4.0,
            render_backend="test",
        )
        await task

        assert websocket.binary_messages == [first, second]

    asyncio.run(exercise())


def test_uvicorn_serves_camera_websocket_transport() -> None:
    async def exercise() -> None:
        store = LatestJpegStore()
        jpeg = b"\xff\xd8transport\xff\xd9"
        store.publish(
            jpeg,
            sequence=9,
            elapsed_s=4.0,
            render_backend="test",
        )
        app = FastAPI()
        install_camera_routes(
            app,
            store,
            lambda: {"camera_width": 1920, "camera_height": 1080},
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
            async with connect(f"ws://127.0.0.1:{port}/camera/v1/stream") as websocket:
                descriptor = json.loads(await websocket.recv())
                frame = await websocket.recv()

            assert descriptor["fps"] == 30
            assert frame == jpeg
        finally:
            server.should_exit = True
            await task

    asyncio.run(exercise())


def test_uvicorn_releases_no_frame_clients_after_disconnect() -> None:
    async def exercise() -> None:
        app = FastAPI()
        install_camera_routes(
            app,
            LatestJpegStore(),
            lambda: {"camera_width": 1920, "camera_height": 1080},
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
            for _ in range(6):
                async with connect(
                    f"ws://127.0.0.1:{port}/camera/v1/stream"
                ) as websocket:
                    descriptor = json.loads(await websocket.recv())
                    assert descriptor["type"] == "camera_stream_descriptor"
                await asyncio.sleep(0.01)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(exercise())


def test_camera_websocket_limits_concurrent_clients() -> None:
    async def exercise() -> None:
        app = FastAPI()
        install_camera_routes(
            app,
            LatestJpegStore(),
            lambda: {"camera_width": 1920, "camera_height": 1080},
            fps=60,
        )
        route = next(
            route
            for route in app.routes
            if isinstance(route, APIWebSocketRoute)
            and route.path == "/camera/v1/stream"
        )
        active = [FakeWebSocket() for _ in range(4)]
        tasks = [
            asyncio.create_task(route.endpoint(cast(Any, websocket)))
            for websocket in active
        ]
        while not all(websocket.accepted for websocket in active):
            await asyncio.sleep(0)
        rejected = FakeWebSocket()

        await route.endpoint(cast(Any, rejected))

        assert rejected.close_calls == [(1013, "camera client limit exceeded")]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(exercise())
