"""Validate the headless live rendering runtime."""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pyvista as pv
import vtk

from scrap_monitoring_visualizer.limits import (
    DEFAULT_FRAME_HEIGHT,
    DEFAULT_FRAME_WIDTH,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
    MAX_GRID_POINTS,
)

EXPECTED_RENDER_WINDOW = "vtkOSOpenGLRenderWindow"


@dataclass(frozen=True)
class ProbeConfig:
    """Bounded settings for the runtime probe."""

    width: int = DEFAULT_FRAME_WIDTH
    height: int = DEFAULT_FRAME_HEIGHT
    grid_x: int = 33
    grid_y: int = 25

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.width > MAX_FRAME_WIDTH or self.height > MAX_FRAME_HEIGHT:
            raise ValueError("resolution exceeds the configured limit")
        if self.grid_x < 2 or self.grid_y < 2:
            raise ValueError("grid dimensions must be at least two")
        if self.grid_x * self.grid_y > MAX_GRID_POINTS:
            raise ValueError("grid point count exceeds the configured limit")


@dataclass(frozen=True)
class ProbeResult:
    """Machine-readable evidence from one runtime probe."""

    display_present: bool
    euid: int
    gpu_device_present: bool
    render_window: str
    python_version: str
    pyvista_version: str
    vtk_version: str
    width: int
    height: int
    grid_points: int
    render_seconds: float
    max_rss_mib: float
    frame_path: str


def synthetic_surface(x_count: int = 33, y_count: int = 25) -> pv.StructuredGrid:
    """Create a public deterministic surface for the environment probe."""
    x_values = np.linspace(-3.0, 3.0, x_count)
    y_values = np.linspace(-2.0, 2.0, y_count)
    x_grid, y_grid = np.meshgrid(x_values, y_values, indexing="xy")
    z_grid = 0.25 + 0.65 * np.exp(-0.24 * (x_grid**2 + y_grid**2))
    return pv.StructuredGrid(x_grid, y_grid, z_grid)


def _runtime_preconditions() -> None:
    if "DISPLAY" in os.environ:
        raise RuntimeError("DISPLAY must be absent")
    if os.geteuid() == 0:
        raise RuntimeError("runtime probe must not run as root")
    if _gpu_device_present():
        raise RuntimeError("runtime probe must not have a GPU device")


def _gpu_device_present() -> bool:
    return Path("/dev/dri").exists() or any(Path("/dev").glob("nvidia*"))


def run_probe(output_dir: Path, config: ProbeConfig) -> ProbeResult:
    """Render one high-resolution PNG without a display or GPU."""
    config.validate()
    _runtime_preconditions()
    output_dir.mkdir(parents=True, exist_ok=False)
    frame_path = output_dir / "frame.png"

    plotter = pv.Plotter(off_screen=True, window_size=[config.width, config.height])
    plotter.set_background("#E8EEF4")  # type: ignore[arg-type]
    plotter.add_mesh(
        synthetic_surface(config.grid_x, config.grid_y),
        color="#D6A85F",
        smooth_shading=True,
    )
    plotter.add_mesh(pv.Box(bounds=(-3.2, 3.2, -2.2, 2.2, 0.0, 0.06)), color="#BCC8D6")
    plotter.add_axes()  # type: ignore[call-arg]
    plotter.camera_position = [
        (6.1, -4.3, 4.2),
        (0.0, 0.0, 0.2),
        (0.0, 0.0, 1.0),
    ]
    plotter.enable_parallel_projection()  # type: ignore[call-arg]
    render_window = type(plotter.render_window).__name__
    if render_window != EXPECTED_RENDER_WINDOW:
        plotter.close()
        raise RuntimeError(f"unexpected render window: {render_window}")
    started = time.perf_counter()
    try:
        plotter.render()
        image = plotter.screenshot(str(frame_path), return_img=True)
    finally:
        plotter.close()
    render_seconds = time.perf_counter() - started
    if image is None or image.shape[:2] != (config.height, config.width):
        raise RuntimeError("renderer returned an unexpected frame shape")

    result = ProbeResult(
        display_present="DISPLAY" in os.environ,
        euid=os.geteuid(),
        gpu_device_present=_gpu_device_present(),
        render_window=render_window,
        python_version=sys.version.split()[0],
        pyvista_version=pv.__version__,
        vtk_version=vtk.vtkVersion.GetVTKVersion(),
        width=config.width,
        height=config.height,
        grid_points=config.grid_x * config.grid_y,
        render_seconds=round(render_seconds, 6),
        max_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3),
        frame_path=frame_path.name,
    )
    (output_dir / "probe.json").write_text(
        json.dumps(asdict(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/probe"))
    parser.add_argument("--grid-x", type=int, default=33)
    parser.add_argument("--grid-y", type=int, default=25)
    args = parser.parse_args()
    config = ProbeConfig(grid_x=args.grid_x, grid_y=args.grid_y)
    result = run_probe(args.output, config)
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
