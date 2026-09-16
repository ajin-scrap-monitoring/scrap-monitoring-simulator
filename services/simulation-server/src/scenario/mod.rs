//! Volume-conserving load surface independent of sensor measurement and transport.

mod error;
mod grid;
mod height_field;
mod simulator;
mod time_scale;

pub use error::{Result, ScenarioError};
pub use grid::{CellCoverage, MAX_GRID_NODES};
pub use height_field::{
    DEFAULT_ANGLE_OF_REPOSE_DEG, DEFAULT_SLOPE_RELAXATION_MAX_ITERATIONS, HeightField,
    MAX_SLOPE_RELAXATION_ITERATIONS, RoughnessChange, SlopeRelaxation, SurfaceSnapshot,
    VolumeChange,
};
pub use simulator::{
    ActiveScenarioPhase, MAX_EVENT_SNAPSHOT_BYTES, MAX_EVENTS_PER_ADVANCE, ScenarioAdvance,
    ScenarioEvent, ScenarioModelSnapshot, ScenarioPhase, ScenarioSettings, ScenarioSimulator,
    ScenarioSnapshot, build_scenario_simulator,
};
pub use time_scale::{REFERENCE_MEAN_FILL_DURATION_S, scale_duration_range, scenario_time_scale};
