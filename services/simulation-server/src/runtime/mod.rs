//! Long-running simulation composition independent of external delivery.

mod application;
mod generation;

pub use application::{ApplicationError, ApplicationSummary, RuntimeSettings, run};
pub use generation::{
    GenerationBatch, GenerationRuntime, GenerationRuntimeError, GenerationRuntimeStats,
    ScenarioPhaseTransition,
};
