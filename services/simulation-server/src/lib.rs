//! Canonical scene simulation and RPLIDAR S2E-compatible protocol boundaries.

pub mod cli;
pub mod configuration;
pub mod error;
pub mod geometry;
pub mod measurement;
mod output_format;
pub mod randomness;
pub mod rate_profile;
pub mod runtime;
pub mod s2e;
pub mod scenario;
pub mod scene_stream;

/// Engine resource limit for scenario inlet positions.
pub const MAX_INLET_POSITIONS: usize = 64;
