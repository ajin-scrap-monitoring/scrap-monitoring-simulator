"""Measure source, network and Browser presentation FPS through Chrome CDP."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, cast

from websockets.asyncio.client import connect


@dataclass(frozen=True, slots=True)
class BrowserProbeResult:
    duration_s: int
    measured_duration_s: float
    minimum_fps: float
    minimum_stable_ratio: float
    stable_samples: int
    total_samples: int
    source_fps: float
    network_fps: float
    presented_fps: float
    received_frames: int
    presented_frames: int
    dropped_before_decode: int
    dropped_before_present: int
    decode_errors: int
    max_pending_decode: int
    max_decode_inflight: int
    max_pending_present: int


@dataclass(frozen=True, slots=True)
class BrowserPageState:
    visibility_state: str
    hidden: bool
    has_focus: bool


def _http_json(url: str, *, method: str = "GET") -> dict[str, Any]:
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=5.0) as response:
        value: object = json.loads(response.read())
    if not isinstance(value, dict):
        raise RuntimeError("Chrome DevTools response must be an object")
    return cast(dict[str, Any], value)


def _open_target(cdp_url: str, page_url: str) -> tuple[str, str]:
    encoded_page = urllib.parse.quote(page_url, safe="")
    target = _http_json(
        f"{cdp_url.rstrip('/')}/json/new?{encoded_page}",
        method="PUT",
    )
    target_id = target.get("id")
    websocket_url = target.get("webSocketDebuggerUrl")
    if not isinstance(target_id, str) or not isinstance(websocket_url, str):
        raise RuntimeError("Chrome DevTools target is missing its id or WebSocket URL")
    cdp = urllib.parse.urlsplit(cdp_url)
    websocket = urllib.parse.urlsplit(websocket_url)
    if not cdp.netloc or not websocket.path:
        raise RuntimeError("Chrome DevTools returned an invalid WebSocket URL")
    websocket_url = urllib.parse.urlunsplit(
        (
            "wss" if cdp.scheme == "https" else "ws",
            cdp.netloc,
            websocket.path,
            websocket.query,
            "",
        )
    )
    return target_id, websocket_url


def _close_target(cdp_url: str, target_id: str) -> None:
    try:
        _http_json(f"{cdp_url.rstrip('/')}/json/close/{target_id}")
    except OSError, RuntimeError, ValueError:
        pass


async def _cdp_command(
    websocket: Any,
    command_id: int,
    method: str,
    params: dict[str, object] | None = None,
    *,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    async with asyncio.timeout(timeout_s):
        await websocket.send(
            json.dumps(
                {"id": command_id, "method": method, "params": params or {}},
                separators=(",", ":"),
            )
        )
        while True:
            raw = await websocket.recv()
            if not isinstance(raw, str):
                raise RuntimeError("Chrome DevTools returned a binary message")
            value: object = json.loads(raw)
            if not isinstance(value, dict) or value.get("id") != command_id:
                continue
            response = cast(dict[str, Any], value)
            if "error" in response:
                raise RuntimeError(
                    f"Chrome DevTools command failed: {response['error']}"
                )
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Chrome DevTools command returned no result")
            return cast(dict[str, Any], result)


async def _metrics(websocket: Any, command_id: int) -> dict[str, Any] | None:
    result = await _cdp_command(
        websocket,
        command_id,
        "Runtime.evaluate",
        {
            "expression": (
                "window.__scrapCameraMetrics?"
                "JSON.stringify(window.__scrapCameraMetrics.snapshot()):null"
            ),
            "returnByValue": True,
        },
    )
    remote = result.get("result")
    if not isinstance(remote, dict):
        raise RuntimeError("Chrome DevTools evaluation returned no value")
    encoded = remote.get("value")
    if encoded is None:
        return None
    if not isinstance(encoded, str):
        raise RuntimeError("Browser camera metrics must be JSON text")
    metrics: object = json.loads(encoded)
    if not isinstance(metrics, dict):
        raise RuntimeError("Browser camera metrics must be an object")
    return cast(dict[str, Any], metrics)


async def _prepare_page(websocket: Any) -> int:
    await _cdp_command(websocket, 1, "Runtime.enable")
    await _cdp_command(websocket, 2, "Page.bringToFront")
    return 2


async def _page_state(websocket: Any, command_id: int) -> BrowserPageState:
    result = await _cdp_command(
        websocket,
        command_id,
        "Runtime.evaluate",
        {
            "expression": (
                "JSON.stringify({"
                "visibilityState:document.visibilityState,"
                "hidden:document.hidden,"
                "hasFocus:document.hasFocus()"
                "})"
            ),
            "returnByValue": True,
        },
    )
    remote = result.get("result")
    if not isinstance(remote, dict):
        raise RuntimeError("Chrome DevTools page state evaluation returned no value")
    encoded = remote.get("value")
    if not isinstance(encoded, str):
        raise RuntimeError("Browser page state must be JSON text")
    value: object = json.loads(encoded)
    if not isinstance(value, dict):
        raise RuntimeError("Browser page state must be an object")
    state = cast(dict[str, Any], value)
    visibility_state = state.get("visibilityState")
    hidden = state.get("hidden")
    has_focus = state.get("hasFocus")
    if not isinstance(visibility_state, str):
        raise RuntimeError("Browser document.visibilityState must be text")
    if not isinstance(hidden, bool):
        raise RuntimeError("Browser document.hidden must be boolean")
    if not isinstance(has_focus, bool):
        raise RuntimeError("Browser document.hasFocus() must be boolean")
    return BrowserPageState(
        visibility_state=visibility_state,
        hidden=hidden,
        has_focus=has_focus,
    )


def _validate_page_state(state: BrowserPageState) -> None:
    if state.visibility_state == "visible" and not state.hidden and state.has_focus:
        return
    details = {
        "document.hasFocus()": state.has_focus,
        "document.hidden": state.hidden,
        "document.visibilityState": state.visibility_state,
    }
    raise RuntimeError(
        "Browser page must remain visible and focused during acceptance measurement: "
        f"{json.dumps(details, sort_keys=True)}"
    )


def _number(mapping: dict[str, Any], key: str) -> float:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RuntimeError(f"Browser camera metric {key} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError(f"Browser camera metric {key} must be finite")
    return result


def _section(metrics: dict[str, Any], name: str) -> dict[str, Any]:
    value = metrics.get(name)
    if not isinstance(value, dict):
        raise RuntimeError(f"Browser camera metrics are missing {name}")
    return cast(dict[str, Any], value)


def _validate_result(result: BrowserProbeResult) -> None:
    if result.total_samples <= 0:
        raise RuntimeError("Browser camera probe collected no samples")
    if result.stable_samples / result.total_samples < result.minimum_stable_ratio:
        raise RuntimeError(
            "Browser camera FPS was below the acceptance threshold: "
            f"{json.dumps(asdict(result), sort_keys=True)}"
        )
    if (
        result.network_fps < result.minimum_fps
        or result.presented_fps < result.minimum_fps
    ):
        raise RuntimeError(
            "Browser camera average FPS was below the acceptance threshold: "
            f"{json.dumps(asdict(result), sort_keys=True)}"
        )
    if result.decode_errors > 0:
        raise RuntimeError(
            "Browser camera decode failed: "
            f"{json.dumps(asdict(result), sort_keys=True)}"
        )
    if (
        max(
            result.max_pending_decode,
            result.max_decode_inflight,
            result.max_pending_present,
        )
        > 1
    ):
        raise RuntimeError(
            "Browser camera queue exceeded the latest-only bound: "
            f"{json.dumps(asdict(result), sort_keys=True)}"
        )


def _elapsed_s(start: dict[str, Any], end: dict[str, Any]) -> float:
    elapsed_s = (
        _number(end, "sampled_at_ms") - _number(start, "sampled_at_ms")
    ) / 1_000.0
    if elapsed_s <= 0.0:
        raise RuntimeError("Browser camera metric timestamps must increase")
    return elapsed_s


def _counter_delta(
    start: dict[str, Any],
    end: dict[str, Any],
    section: str,
    key: str,
) -> int:
    delta = round(_number(_section(end, section), key)) - round(
        _number(_section(start, section), key)
    )
    if delta < 0:
        raise RuntimeError(f"Browser camera counter {section}.{key} decreased")
    return delta


def _summarize_samples(
    baseline: dict[str, Any],
    samples: list[dict[str, Any]],
    *,
    duration_s: int,
    minimum_fps: float,
    minimum_stable_ratio: float,
) -> BrowserProbeResult:
    if not samples:
        raise RuntimeError("Browser camera probe collected no samples")
    latest = samples[-1]
    measured_duration_s = _elapsed_s(baseline, latest)
    received_frames = _counter_delta(baseline, latest, "network", "received_frames")
    presented_frames = _counter_delta(baseline, latest, "browser", "presented_frames")
    window_s = _number(baseline, "window_s")
    if window_s <= 0.0:
        raise RuntimeError("Browser camera metric window must be positive")

    points = [baseline, *samples]
    stable_samples = 0
    total_samples = 0
    for end_index in range(1, len(points)):
        end = points[end_index]
        cutoff_ms = _number(end, "sampled_at_ms") - window_s * 1_000.0
        candidates = [
            point
            for point in points[:end_index]
            if _number(point, "sampled_at_ms") <= cutoff_ms
        ]
        if not candidates:
            continue
        start = candidates[-1]
        sample_elapsed_s = _elapsed_s(start, end)
        minimum_frames = math.floor(minimum_fps * sample_elapsed_s)
        source_stable = _number(_section(end, "source"), "rendered_fps") >= minimum_fps
        network_stable = (
            _counter_delta(start, end, "network", "received_frames") >= minimum_frames
        )
        browser_stable = (
            _counter_delta(start, end, "browser", "presented_frames") >= minimum_frames
        )
        stable_samples += source_stable and network_stable and browser_stable
        total_samples += 1

    if total_samples == 0:
        raise RuntimeError("Browser camera probe collected no stable-window samples")
    queue_samples = tuple(_section(sample, "queues") for sample in samples)
    result = BrowserProbeResult(
        duration_s=duration_s,
        measured_duration_s=round(measured_duration_s, 3),
        minimum_fps=minimum_fps,
        minimum_stable_ratio=minimum_stable_ratio,
        stable_samples=stable_samples,
        total_samples=total_samples,
        source_fps=_number(_section(latest, "source"), "rendered_fps"),
        network_fps=received_frames / measured_duration_s,
        presented_fps=presented_frames / measured_duration_s,
        received_frames=received_frames,
        presented_frames=presented_frames,
        dropped_before_decode=_counter_delta(
            baseline, latest, "browser", "dropped_before_decode"
        ),
        dropped_before_present=_counter_delta(
            baseline, latest, "browser", "dropped_before_present"
        ),
        decode_errors=_counter_delta(baseline, latest, "browser", "decode_errors"),
        max_pending_decode=max(
            round(_number(queues, "pending_decode")) for queues in queue_samples
        ),
        max_decode_inflight=max(
            round(_number(queues, "decode_inflight")) for queues in queue_samples
        ),
        max_pending_present=max(
            round(_number(queues, "pending_present")) for queues in queue_samples
        ),
    )
    _validate_result(result)
    return result


async def run_probe(
    cdp_url: str,
    page_url: str,
    *,
    duration_s: int,
    minimum_fps: float,
    minimum_stable_ratio: float,
    readiness_timeout_s: float = 30.0,
) -> BrowserProbeResult:
    target_id, websocket_url = _open_target(cdp_url, page_url)
    try:
        async with connect(
            websocket_url,
            open_timeout=5.0,
            close_timeout=2.0,
            max_size=1_048_576,
            compression=None,
        ) as websocket:
            command_id = await _prepare_page(websocket)
            command_id += 1
            _validate_page_state(await _page_state(websocket, command_id))
            deadline = asyncio.get_running_loop().time() + readiness_timeout_s
            while True:
                command_id += 1
                metrics = await _metrics(websocket, command_id)
                if metrics is not None:
                    source = _section(metrics, "source")
                    network = _section(metrics, "network")
                    browser = _section(metrics, "browser")
                    if (
                        source.get("rendered_fps") is not None
                        and _number(network, "received_frames") >= 5
                        and _number(browser, "presented_frames") >= 5
                    ):
                        break
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError("Browser camera metrics did not become ready")
                await asyncio.sleep(0.25)

            await asyncio.sleep(5.0)
            command_id += 1
            _validate_page_state(await _page_state(websocket, command_id))
            command_id += 1
            baseline = await _metrics(websocket, command_id)
            if baseline is None:
                raise RuntimeError("Browser camera metrics disappeared")
            samples: list[dict[str, Any]] = []
            for _ in range(duration_s):
                await asyncio.sleep(1.0)
                command_id += 1
                _validate_page_state(await _page_state(websocket, command_id))
                command_id += 1
                sample = await _metrics(websocket, command_id)
                if sample is None:
                    raise RuntimeError("Browser camera metrics disappeared")
                samples.append(sample)

        return _summarize_samples(
            baseline,
            samples,
            duration_s=duration_s,
            minimum_fps=minimum_fps,
            minimum_stable_ratio=minimum_stable_ratio,
        )
    finally:
        _close_target(cdp_url, target_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--page-url", required=True)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--minimum-fps", type=float, default=27.0)
    parser.add_argument("--minimum-stable-ratio", type=float, default=0.9)
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("--duration must be positive")
    if args.minimum_fps <= 0.0:
        parser.error("--minimum-fps must be positive")
    if not 0.0 < args.minimum_stable_ratio <= 1.0:
        parser.error("--minimum-stable-ratio must be between 0 and 1")
    try:
        result = asyncio.run(
            run_probe(
                args.cdp_url,
                args.page_url,
                duration_s=args.duration,
                minimum_fps=args.minimum_fps,
                minimum_stable_ratio=args.minimum_stable_ratio,
            )
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
