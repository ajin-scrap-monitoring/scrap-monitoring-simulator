import pytest

from scrap_monitoring_visualizer.cli import build_parser, main


def test_live_parser_requires_both_endpoints() -> None:
    parser = build_parser({})
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["live", "--tcp-host", "127.0.0.1"])
    assert caught.value.code == 2


def test_live_parser_reads_environment_configuration() -> None:
    parser = build_parser(
        {
            "SCRAP_MONITORING_VISUALIZER_TCP_HOST": "0.0.0.0",
            "SCRAP_MONITORING_VISUALIZER_TCP_PORT": "17000",
            "SCRAP_MONITORING_VISUALIZER_HTTP_HOST": "0.0.0.0",
            "SCRAP_MONITORING_VISUALIZER_HTTP_PORT": "18000",
            "SCRAP_MONITORING_VISUALIZER_WIDTH": "1920",
            "SCRAP_MONITORING_VISUALIZER_HEIGHT": "1080",
            "SCRAP_MONITORING_VISUALIZER_CAMERA_ENABLED": "true",
            "SCRAP_MONITORING_VISUALIZER_CAMERA_BACKEND": "egl",
        }
    )

    args = parser.parse_args(["live"])

    assert args.tcp_host == "0.0.0.0"
    assert args.tcp_port == 17000
    assert args.http_host == "0.0.0.0"
    assert args.http_port == 18000
    assert args.width == 1920
    assert args.height == 1080
    assert args.camera_enabled is True
    assert args.camera_backend == "egl"


def test_live_parser_uses_rendering_defaults() -> None:
    parser = build_parser(
        {
            "SCRAP_MONITORING_VISUALIZER_TCP_HOST": "0.0.0.0",
            "SCRAP_MONITORING_VISUALIZER_TCP_PORT": "17000",
            "SCRAP_MONITORING_VISUALIZER_HTTP_HOST": "0.0.0.0",
            "SCRAP_MONITORING_VISUALIZER_HTTP_PORT": "18000",
        }
    )

    args = parser.parse_args(["live"])

    assert args.width == 1280
    assert args.height == 720
    assert args.camera_enabled is False
    assert args.camera_profile is None
    assert args.camera_backend is None


def test_cli_arguments_override_environment_configuration() -> None:
    parser = build_parser(
        {
            "SCRAP_MONITORING_VISUALIZER_TCP_HOST": "environment-host",
            "SCRAP_MONITORING_VISUALIZER_TCP_PORT": "invalid",
            "SCRAP_MONITORING_VISUALIZER_HTTP_HOST": "environment-http-host",
            "SCRAP_MONITORING_VISUALIZER_HTTP_PORT": "9000",
        }
    )

    args = parser.parse_args(
        [
            "live",
            "--tcp-host",
            "cli-host",
            "--tcp-port",
            "17000",
            "--http-host",
            "cli-http-host",
            "--http-port",
            "18000",
        ]
    )

    assert args.tcp_host == "cli-host"
    assert args.tcp_port == 17000
    assert args.http_host == "cli-http-host"
    assert args.http_port == 18000


def test_parser_rejects_invalid_environment_number() -> None:
    parser = build_parser(
        {
            "SCRAP_MONITORING_VISUALIZER_TCP_HOST": "0.0.0.0",
            "SCRAP_MONITORING_VISUALIZER_TCP_PORT": "invalid",
            "SCRAP_MONITORING_VISUALIZER_HTTP_HOST": "0.0.0.0",
            "SCRAP_MONITORING_VISUALIZER_HTTP_PORT": "18000",
        }
    )

    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["live"])

    assert caught.value.code == 2


def test_parser_rejects_invalid_camera_boolean() -> None:
    parser = build_parser(
        {
            "SCRAP_MONITORING_VISUALIZER_TCP_HOST": "0.0.0.0",
            "SCRAP_MONITORING_VISUALIZER_TCP_PORT": "17000",
            "SCRAP_MONITORING_VISUALIZER_HTTP_HOST": "0.0.0.0",
            "SCRAP_MONITORING_VISUALIZER_HTTP_PORT": "18000",
            "SCRAP_MONITORING_VISUALIZER_CAMERA_ENABLED": "sometimes",
        }
    )

    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["live"])

    assert caught.value.code == 2


def test_live_config_rejects_shared_endpoint() -> None:
    assert (
        main(
            [
                "live",
                "--tcp-host",
                "127.0.0.1",
                "--tcp-port",
                "18000",
                "--http-host",
                "127.0.0.1",
                "--http-port",
                "18000",
            ],
            environment={},
        )
        == 2
    )


def test_parser_only_exposes_live_mode() -> None:
    parser = build_parser({})

    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["replay"])

    assert caught.value.code == 2
