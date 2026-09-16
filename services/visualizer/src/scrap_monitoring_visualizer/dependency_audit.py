"""Inventory runtime dependencies and their bundled license notices."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from importlib.metadata import Distribution, distributions
from pathlib import Path
from typing import Any

DIRECT_PYTHON_DEPENDENCIES = {
    "fastapi",
    "jsonschema",
    "numpy",
    "pillow",
    "pyvista",
    "uvicorn",
    "vtk",
    "websockets",
}
DIRECT_DEBIAN_DEPENDENCIES = {"libegl1", "libgl1", "libosmesa6"}
PROJECT_DISTRIBUTION = "scrap-monitoring-visualizer"
LICENSE_MARKERS = ("LICENSE", "COPYING", "COPYRIGHT")
NOTICE_MARKERS = (*LICENSE_MARKERS, "NOTICE", "AUTHORS")


def _notice_paths(distribution: Distribution) -> list[str]:
    result = []
    for entry in distribution.files or ():
        name = str(entry)
        upper_name = name.upper()
        if ".DIST-INFO/" in upper_name and any(
            marker in upper_name for marker in NOTICE_MARKERS
        ):
            result.append(str(distribution.locate_file(entry)))
    return sorted(result)


def _declared_license(distribution: Distribution) -> str:
    expression = distribution.metadata.get("License-Expression", "").strip()
    if expression:
        return expression
    value = distribution.metadata.get("License", "").strip()
    if value and "\n" not in value and len(value) <= 120:
        return value
    classifiers = [
        classifier.removeprefix("License :: ")
        for classifier in distribution.metadata.get_all("Classifier", [])
        if classifier.startswith("License :: ")
    ]
    return " OR ".join(classifiers) if classifiers else "See bundled license files"


def python_inventory() -> list[dict[str, Any]]:
    result = []
    observed: set[str] = set()
    for distribution in sorted(
        distributions(), key=lambda item: item.metadata["Name"].lower()
    ):
        name = distribution.metadata["Name"]
        normalized_name = name.lower().replace("_", "-")
        if normalized_name == PROJECT_DISTRIBUTION:
            continue
        notices = _notice_paths(distribution)
        if not any(
            any(marker in path.upper() for marker in LICENSE_MARKERS)
            for path in notices
        ):
            raise RuntimeError(f"Python distribution has no license file: {name}")
        observed.add(normalized_name)
        result.append(
            {
                "name": name,
                "version": distribution.version,
                "declared_license": _declared_license(distribution),
                "notice_files": notices,
            }
        )
    missing = DIRECT_PYTHON_DEPENDENCIES - observed
    if missing:
        raise RuntimeError(f"Missing direct Python dependencies: {sorted(missing)}")
    return result


def debian_inventory() -> list[dict[str, str]]:
    try:
        completed = subprocess.run(
            ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("cannot inventory Debian packages") from error
    result = []
    observed: set[str] = set()
    for line in completed.stdout.splitlines():
        package, version = line.split("\t", 1)
        name = package.split(":", 1)[0]
        notice = Path("/usr/share/doc") / name / "copyright"
        if not notice.is_file():
            raise RuntimeError(f"Debian package has no copyright file: {package}")
        observed.add(name)
        result.append(
            {
                "name": package,
                "version": version,
                "copyright_file": str(notice),
            }
        )
    missing = DIRECT_DEBIAN_DEPENDENCIES - observed
    if missing:
        raise RuntimeError(f"Missing direct Debian dependencies: {sorted(missing)}")
    return sorted(result, key=lambda item: item["name"])


def build_inventory() -> dict[str, Any]:
    python_notice = next(
        iter(
            sorted(
                Path(sys.base_prefix).glob("lib/python*/LICENSE.txt"),
                key=str,
            )
        ),
        None,
    )
    if python_notice is None:
        raise RuntimeError("CPython license file is missing")
    return {
        "schema_version": 1,
        "python_version": platform.python_version(),
        "machine": platform.machine(),
        "runtime_notices": [
            {
                "name": "CPython",
                "version": platform.python_version(),
                "license_file": str(python_notice),
            }
        ],
        "python_distributions": python_inventory(),
        "debian_packages": debian_inventory(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    content = json.dumps(build_inventory(), indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(content)
        return
    if not args.output.parent.is_dir():
        raise ValueError("inventory output parent directory does not exist")
    with args.output.open("x", encoding="utf-8") as output:
        output.write(content)


if __name__ == "__main__":
    main()
