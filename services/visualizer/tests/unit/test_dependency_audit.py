from __future__ import annotations

from importlib.metadata import distribution

from scrap_monitoring_visualizer.dependency_audit import (
    _declared_license,
    _notice_paths,
)


def test_dependency_audit_finds_declared_license_and_notice() -> None:
    installed = distribution("jsonschema")

    assert _declared_license(installed) == "MIT"
    assert any(path.endswith("/COPYING") for path in _notice_paths(installed))
