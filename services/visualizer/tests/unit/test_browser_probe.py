from __future__ import annotations

import asyncio
import json

import pytest

from scrap_monitoring_visualizer import browser_probe as browser_probe_module
from scrap_monitoring_visualizer.browser_probe import (
    BrowserPageState,
    BrowserProbeResult,
    _cdp_command,
    _metrics,
    _number,
    _page_state,
    _prepare_page,
    _section,
    _summarize_samples,
    _validate_page_state,
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
        assert "__scrapVisualMetrics" in command["params"]["expression"]

    asyncio.run(exercise())


def test_browser_probe_activates_target_and_reads_page_state() -> None:
    async def exercise() -> None:
        socket = FakeCdpSocket(
            [
                json.dumps({"id": 1, "result": {}}),
                json.dumps({"id": 2, "result": {}}),
                json.dumps({"id": 3, "result": {}}),
                json.dumps(
                    {
                        "id": 4,
                        "result": {
                            "result": {
                                "value": json.dumps(
                                    {
                                        "visibilityState": "visible",
                                        "hidden": False,
                                        "hasFocus": True,
                                    }
                                )
                            }
                        },
                    }
                ),
            ]
        )

        assert await _prepare_page(socket) == 3
        state = await _page_state(socket, 4)
        _validate_page_state(state)
        commands = [json.loads(message) for message in socket.sent]
        assert [command["method"] for command in commands] == [
            "Runtime.enable",
            "Page.bringToFront",
            "Emulation.setFocusEmulationEnabled",
            "Runtime.evaluate",
        ]
        assert commands[2]["params"] == {"enabled": True}
        assert "document.visibilityState" in commands[3]["params"]["expression"]
        assert "document.hidden" in commands[3]["params"]["expression"]
        assert "document.hasFocus()" in commands[3]["params"]["expression"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "state",
    [
        BrowserPageState("hidden", True, False),
        BrowserPageState("visible", False, False),
        BrowserPageState("visible", True, True),
    ],
)
def test_browser_probe_rejects_inactive_page(state: BrowserPageState) -> None:
    with pytest.raises(RuntimeError, match="visible and focused"):
        _validate_page_state(state)


class FakeCdpConnection:
    def __init__(self, websocket: object) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> object:
        return self.websocket

    async def __aexit__(self, *args: object) -> None:
        del args


def test_browser_probe_checks_page_state_throughout_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = object()
    page_command_ids: list[int] = []
    metric_command_ids: list[int] = []
    sleeps: list[float] = []
    closed_targets: list[str] = []
    metrics = iter([_sample(float(second)) for second in range(1, 9)])

    async def prepare_page(observed_websocket: object) -> int:
        assert observed_websocket is websocket
        return 2

    async def page_state(
        observed_websocket: object,
        command_id: int,
    ) -> BrowserPageState:
        assert observed_websocket is websocket
        page_command_ids.append(command_id)
        return BrowserPageState("visible", False, True)

    async def read_metrics(
        observed_websocket: object,
        command_id: int,
    ) -> dict[str, object]:
        assert observed_websocket is websocket
        metric_command_ids.append(command_id)
        return next(metrics)

    async def sleep(delay_s: float) -> None:
        sleeps.append(delay_s)

    monkeypatch.setattr(
        browser_probe_module,
        "_open_target",
        lambda cdp_url, page_url: ("target-1", "ws://chrome/target-1"),
    )
    monkeypatch.setattr(
        browser_probe_module,
        "connect",
        lambda *args, **kwargs: FakeCdpConnection(websocket),
    )
    monkeypatch.setattr(browser_probe_module, "_prepare_page", prepare_page)
    monkeypatch.setattr(browser_probe_module, "_page_state", page_state)
    monkeypatch.setattr(browser_probe_module, "_metrics", read_metrics)
    monkeypatch.setattr(browser_probe_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(
        browser_probe_module,
        "_close_target",
        lambda cdp_url, target_id: closed_targets.append(target_id),
    )

    result = asyncio.run(
        browser_probe_module.run_probe(
            "http://chrome",
            "http://visualizer",
            duration_s=6,
            minimum_fps=27.0,
            minimum_stable_ratio=1.0,
        )
    )

    assert result.presented_fps == 30.0
    assert page_command_ids == [3, 5, 7, 9, 11, 13, 15, 17]
    assert metric_command_ids == [4, 6, 8, 10, 12, 14, 16, 18]
    assert sleeps == [5.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert closed_targets == ["target-1"]


def test_browser_probe_closes_target_when_page_loses_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = object()
    states = iter(
        [
            BrowserPageState("visible", False, True),
            BrowserPageState("visible", False, False),
        ]
    )
    closed_targets: list[str] = []

    async def prepare_page(observed_websocket: object) -> int:
        assert observed_websocket is websocket
        return 2

    async def page_state(
        observed_websocket: object,
        command_id: int,
    ) -> BrowserPageState:
        assert observed_websocket is websocket
        assert command_id in {3, 5}
        return next(states)

    async def read_metrics(
        observed_websocket: object,
        command_id: int,
    ) -> dict[str, object]:
        assert observed_websocket is websocket
        assert command_id == 4
        return _sample(1.0)

    async def sleep(delay_s: float) -> None:
        assert delay_s == 5.0

    monkeypatch.setattr(
        browser_probe_module,
        "_open_target",
        lambda cdp_url, page_url: ("target-2", "ws://chrome/target-2"),
    )
    monkeypatch.setattr(
        browser_probe_module,
        "connect",
        lambda *args, **kwargs: FakeCdpConnection(websocket),
    )
    monkeypatch.setattr(browser_probe_module, "_prepare_page", prepare_page)
    monkeypatch.setattr(browser_probe_module, "_page_state", page_state)
    monkeypatch.setattr(browser_probe_module, "_metrics", read_metrics)
    monkeypatch.setattr(browser_probe_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(
        browser_probe_module,
        "_close_target",
        lambda cdp_url, target_id: closed_targets.append(target_id),
    )

    with pytest.raises(RuntimeError, match="visible and focused"):
        asyncio.run(
            browser_probe_module.run_probe(
                "http://chrome",
                "http://visualizer",
                duration_s=1,
                minimum_fps=27.0,
                minimum_stable_ratio=0.9,
            )
        )

    assert closed_targets == ["target-2"]


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
        "source_publish_fps": 30.0,
        "received_fps": 30.0,
        "decoded_fps": 30.0,
        "presented_fps": 30.0,
        "received_frames": 300,
        "decoded_frames": 300,
        "presented_frames": 300,
        "decode_duration_ms": 1.0,
        "decode_duration_max_ms": 2.0,
        "dropped_before_decode": 0,
        "dropped_before_present": 0,
        "decode_errors": 0,
        "target_mismatches": 0,
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
    with pytest.raises(RuntimeError, match="target identity diverged"):
        _validate_result(_result(target_mismatches=1))
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
        "source": {"rendered_fps": source_fps, "published_fps": source_fps},
        "network": {"received_frames": frames},
        "browser": {
            "decoded_frames": frames,
            "decode_duration_ms": 1.0,
            "decode_duration_max_ms": 2.0,
            "presented_frames": frames,
            "dropped_before_decode": 0,
            "dropped_before_present": 0,
            "decode_errors": 0,
            "target_mismatches": 0,
        },
        "queues": {
            "pending_decode": 0,
            "decode_inflight": 1,
            "pending_present": 0,
            "max_pending_decode": 0,
            "max_decode_inflight": 1,
            "max_pending_present": 0,
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
    assert result.received_fps == 27.0
    assert result.decoded_fps == 27.0
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
