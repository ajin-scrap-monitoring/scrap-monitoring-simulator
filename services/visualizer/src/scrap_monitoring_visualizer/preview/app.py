"""FastAPI Browser shell with bounded HTTP admission."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

StatusProvider = Callable[[], Mapping[str, Any]]
_DEFAULT_STATIC_ROOT = Path("/workspace/visualizer/web/dist")


class RequestGate:
    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise ValueError("request limit must be positive")
        self._limit = limit
        self._active = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            if self._active >= self._limit:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._active <= 0:
                raise RuntimeError("request gate release without acquire")
            self._active -= 1


def create_preview_app(
    status_provider: StatusProvider,
    *,
    static_root: Path = _DEFAULT_STATIC_ROOT,
    max_requests: int = 16,
    request_timeout_s: float = 5.0,
) -> FastAPI:
    if request_timeout_s <= 0:
        raise ValueError("request timeout must be positive")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate = RequestGate(max_requests)

    @app.middleware("http")
    async def bounded_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not await gate.acquire():
            return JSONResponse({"detail": "request limit exceeded"}, status_code=503)
        try:
            async with asyncio.timeout(request_timeout_s):
                return await call_next(request)
        except TimeoutError:
            return JSONResponse({"detail": "request timeout"}, status_code=504)
        finally:
            await gate.release()

    @app.get("/", response_class=FileResponse)
    async def index() -> FileResponse:
        return FileResponse(
            static_root / "index.html", headers={"Cache-Control": "no-store"}
        )

    @app.get("/style.css", response_class=FileResponse)
    async def style() -> FileResponse:
        return FileResponse(
            static_root / "style.css", headers={"Cache-Control": "no-store"}
        )

    @app.get("/status")
    async def status() -> JSONResponse:
        return JSONResponse(
            dict(status_provider()), headers={"Cache-Control": "no-store"}
        )

    app.mount(
        "/assets",
        StaticFiles(directory=static_root / "assets", check_dir=False),
        name="assets",
    )
    return app
