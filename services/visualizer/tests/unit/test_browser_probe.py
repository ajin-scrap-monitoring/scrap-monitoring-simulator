from __future__ import annotations

import asyncio
import json

import pytest

from scrap_monitoring_visualizer.browser_probe import (
    BrowserProbeResult,
    _cdp_command,
    _metrics,
    _number,
    _section,
    _summarize_samples,
    _validate_result,
)


class FakeCdpSocket:
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        return self.messages.pop(0)


def test_browser_probe_reads_hidden_metrics_through_cdp() -> None:
    async def exercise() -> None:
        snapshot = {
            "source": {"rendered_fps": 29.5},
            "network": {"received_fps": 29.4},
            "browser": {"presented_fps": 29.3},
        }
        socket = FakeCdpSocket(
            [
                json.dumps({"method": "Runtime.consoleAPICalled"}),
                json.dumps(
                    {
                        "id": 7,
                        "result": {"result": {"value": json.dumps(snapshot)}},
                    }
                ),
            ]
        )

        assert await _metrics(socket, 7) == snapshot
        command = json.loads(socket.sent[0])
        assert command["method"] == "Runtime.evaluate"
        assert "__scrapCameraMetrics" in command["params"]["expression"]

    asyncio.run(exercise())


def test_browser_probe_validates_metric_shape_and_numbers() -> None:
    metrics = {"network": {"received_fps": 29}}

    assert _number(_section(metrics, "network"), "received_fps") == 29.0
    with pytest.raises(RuntimeError, match="missing source"):
        _section(metrics, "source")
    with pytest.raises(RuntimeError, match="must be numeric"):
        _number({"received_fps": "29"}, "received_fps")


def test_browser_probe_bounds_the_complete_cdp_exchange() -> None:
    class HangingCdpSocket:
        async def send(self, message: str) -> None:
            del message

        async def recv(self) -> str:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def exercise() -> None:
        with pytest.raises(TimeoutError):
            await _cdp_command(
                HangingCdpSocket(),
                1,
                "Runtime.enable",
                timeout_s=0.001,
            )

    asyncio.run(exercise())


def _result(**overrides: int | float) -> BrowserProbeResult:
    values: dict[str, int | float] = {
        "duration_s": 10,
        "measured_duration_s": 10.0,
        "minimum_fps": 27.0,
        "minimum_stable_ratio": 0.9,
        "stable_samples": 9,
        "total_samples": 10,
        "source_fps": 30.0,
        "network_fps": 30.0,
        "presented_fps": 30.0,
        "received_frames": 300,
        "presented_frames": 300,
        "dropped_before_decode": 0,
        "dropped_before_present": 0,
        "decode_errors": 0,
        "max_pending_decode": 1,
        "max_decode_inflight": 1,
        "max_pending_present": 1,
    }
    values.update(overrides)
    return BrowserProbeResult(**values)  # type: ignore[arg-type]


def test_browser_probe_acceptance_rejects_decode_and_queue_failures() -> None:
    _validate_result(_result())
    with pytest.raises(RuntimeError, match="acceptance threshold"):
        _validate_result(_result(stable_samples=8))
    with pytest.raises(RuntimeError, match="decode failed"):
        _validate_result(_result(decode_errors=1))
    with pytest.raises(RuntimeError, match="latest-only bound"):
        _validate_result(_result(max_pending_decode=2))
    with pytest.raises(RuntimeError, match="average FPS"):
        _validate_result(_result(presented_fps=26.9))


def _sample(
    sampled_at_s: float,
    *,
    fps: float = 30.0,
    source_fps: float = 30.0,
) -> dict[str, object]:
    frames = int(fps * sampled_at_s)
    return {
        "sampled_at_ms": sampled_at_s * 1_000.0,
        "window_s": 5.0,
        "source": {"rendered_fps": source_fps},
        "network": {"received_frames": frames},
        "browser": {
            "presented_frames": frames,
            "dropped_before_decode": 0,
            "dropped_before_present": 0,
            "decode_errors": 0,
        },
        "queues": {
            "pending_decode": 0,
            "decode_inflight": 1,
            "pending_present": 0,
        },
    }


def test_browser_probe_uses_measured_counter_deltas_and_stable_windows() -> None:
    sample_times = [
        1.01,
        2.04,
        3.02,
        4.08,
        5.03,
        6.07,
        7.01,
        8.06,
        9.02,
        10.0,
    ]

    result = _summarize_samples(
        _sample(0.0, fps=27.0, source_fps=27.0),
        [_sample(value, fps=27.0, source_fps=27.0) for value in sample_times],
        duration_s=10,
        minimum_fps=27.0,
        minimum_stable_ratio=0.9,
    )

    assert result.measured_duration_s == 10.0
    assert result.network_fps == 27.0
    assert result.presented_fps == 27.0
    assert result.stable_samples == result.total_samples


def test_browser_probe_rejects_slow_whole_measurement() -> None:
    samples = [_sample(float(second), fps=26.5) for second in range(1, 61)]
    for sample in samples[-5:]:
        sampled_at_s = float(sample["sampled_at_ms"]) / 1_000.0
        sample["network"] = {
            "received_frames": int(26.5 * 55 + 30 * (sampled_at_s - 55))
        }
        browser = sample["browser"]
        assert isinstance(browser, dict)
        browser["presented_frames"] = int(26.5 * 55 + 30 * (sampled_at_s - 55))

    with pytest.raises(RuntimeError, match="average FPS"):
        _summarize_samples(
            _sample(0.0, fps=26.5),
            samples,
            duration_s=60,
            minimum_fps=27.0,
            minimum_stable_ratio=0.0,
        )
