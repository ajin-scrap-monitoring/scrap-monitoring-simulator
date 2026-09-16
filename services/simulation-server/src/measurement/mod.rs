//! Deterministic scan scheduling, ray casting, distortions, and HQ samples.

mod generation;
mod reference;
mod scene;
mod snapshots;
mod spatial;

pub mod rotation;
pub mod sdk;

pub use generation::{
    DropoutSettings, MeasurementGenerator, MeasurementSettings, QualityDistribution,
    SensorDropoutScheduler,
};
pub use reference::{
    HqSample, MeasuredScan, MeasurementResult, ReferencePoint, ReferenceScan, ReferenceScanner,
    TimedReferenceScan,
};
pub use rotation::{
    MAX_SCAN_POINTS_PER_FRAME, ScheduledScan, SensorRotationScheduler,
    create_seeded_rotation_scheduler, validate_scan_point_limit,
};
pub use scene::{EnvironmentScene, HitKind, RayHit, SensorFrame};
pub use snapshots::SnapshotEventCoordinator;
pub use spatial::{
    CollectionOcclusionEvent, CollectionOcclusionSettings, DistortionInterval, DistortionPhase,
    FallingMaterialEvent, FallingMaterialSettings, SpatialDistortionResolver,
    SpatialDistortionTimeline, VoidEvent, VoidSettings, resolve_collection_occlusion_distances,
    resolve_falling_material_distances, resolve_void_distances,
};

#[derive(Clone, Debug, PartialEq, thiserror::Error)]
pub enum MeasurementError {
    #[error("{0}")]
    Invalid(&'static str),
    #[error("{0}")]
    Numerical(&'static str),
    #[error("{0}")]
    Exhausted(&'static str),
    #[error("{0}")]
    Clock(&'static str),
    #[error(transparent)]
    Geometry(#[from] crate::geometry::GeometryError),
    #[error(transparent)]
    Random(#[from] crate::randomness::RandomError),
    #[error(transparent)]
    Rotation(#[from] rotation::RotationError),
    #[error(transparent)]
    Sdk(#[from] sdk::SdkCompatibilityError),
    #[error(transparent)]
    RateProfile(#[from] crate::rate_profile::RateProfileError),
}

pub type Result<T> = std::result::Result<T, MeasurementError>;

pub(crate) fn require_distance_bounds(minimum: f64, maximum: f64) -> Result<()> {
    if !minimum.is_finite() || minimum < 0.0 {
        return Err(MeasurementError::Invalid(
            "minimum distance must be finite and non-negative",
        ));
    }
    if maximum.is_nan() || maximum < minimum {
        return Err(MeasurementError::Invalid(
            "maximum distance must be at least the minimum distance",
        ));
    }
    Ok(())
}
