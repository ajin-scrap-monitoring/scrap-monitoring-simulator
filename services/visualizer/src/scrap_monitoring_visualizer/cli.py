"""Command-line entry point for live browser visualization."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from scrap_monitoring_visualizer.live import LiveConfig, run_live
from scrap_monitoring_visualizer.synthetic_camera import SyntheticCameraConfig

ENV_PREFIX = "SCRAP_MONITORING_VISUALIZER_"


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _environment_argument(
    environment: Mapping[str, str],
    name: str,
    default: Any = None,
    *,
    required: bool = False,
) -> dict[str, Any]:
    value = environment.get(f"{ENV_PREFIX}{name}", default)
    options: dict[str, Any] = {
        "default": value,
        "help": f"environment: {ENV_PREFIX}{name}",
    }
    if required:
        options["required"] = value is None
    return options


def build_parser(
    environment: Mapping[str, str] | None = None,
) -> argparse.ArgumentParser:
    values = os.environ if environment is None else environment
    parser = argparse.ArgumentParser(prog="scrap-monitoring-visualizer")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    live = subparsers.add_parser("live")
    live.add_argument(
        "--tcp-host",
        **_environment_argument(values, "TCP_HOST", required=True),
    )
    live.add_argument(
        "--tcp-port",
        type=int,
        **_environment_argument(values, "TCP_PORT", required=True),
    )
    live.add_argument(
        "--http-host",
        **_environment_argument(values, "HTTP_HOST", required=True),
    )
    live.add_argument(
        "--http-port",
        type=int,
        **_environment_argument(values, "HTTP_PORT", required=True),
    )
    live.add_argument(
        "--camera-enabled",
        type=_boolean,
        **_environment_argument(values, "CAMERA_ENABLED", "false"),
    )
    live.add_argument(
        "--camera-profile",
        **_environment_argument(values, "CAMERA_PROFILE"),
    )
    live.add_argument(
        "--camera-backend",
        choices=("auto", "osmesa", "egl"),
        **_environment_argument(values, "CAMERA_BACKEND"),
    )
    live.add_argument(
        "--camera-native-raster",
        type=_boolean,
        **_environment_argument(values, "CAMERA_NATIVE_RASTER", "false"),
    )
    return parser


def main(
    arguments: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    parser = build_parser(environment)
    args = parser.parse_args(arguments)
    try:
        camera_config = None
        if args.camera_enabled:
            camera_config = SyntheticCameraConfig.from_file(args.camera_profile or None)
            if args.camera_backend is not None:
                camera_config = replace(
                    camera_config,
                    backend=args.camera_backend,
                )
            if args.camera_native_raster:
                camera_config = replace(
                    camera_config,
                    video=replace(
                        camera_config.video,
                        raster_width=camera_config.video.width,
                        raster_height=camera_config.video.height,
                    ),
                )
        live_config = LiveConfig(
            tcp_host=args.tcp_host,
            tcp_port=args.tcp_port,
            http_host=args.http_host,
            http_port=args.http_port,
            synthetic_camera=camera_config,
        )
        live_config.validate()
        return asyncio.run(run_live(live_config))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
