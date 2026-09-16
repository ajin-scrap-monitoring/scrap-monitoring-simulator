import pytest

from scrap_monitoring_visualizer.limits import (
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
    MAX_GRID_POINTS,
)
from scrap_monitoring_visualizer.runtime_probe import (
    ProbeConfig,
    synthetic_surface,
)


def test_probe_config_accepts_project_limits() -> None:
    ProbeConfig(width=MAX_FRAME_WIDTH, height=MAX_FRAME_HEIGHT).validate()


@pytest.mark.parametrize(
    "config",
    [
        ProbeConfig(width=MAX_FRAME_WIDTH + 1),
        ProbeConfig(height=MAX_FRAME_HEIGHT + 1),
        ProbeConfig(width=0),
        ProbeConfig(grid_x=MAX_GRID_POINTS, grid_y=2),
    ],
)
def test_probe_config_rejects_invalid_limits(config: ProbeConfig) -> None:
    with pytest.raises(ValueError):
        config.validate()


def test_synthetic_surface_has_stable_dimensions() -> None:
    surface = synthetic_surface()
    assert surface.dimensions == (25, 33, 1)
    assert surface.n_points == 825
    assert surface.n_cells == 768
