//! Finite World-coordinate primitives and polygon operations in meters.

mod polygon;
mod primitives;

pub use polygon::{MAX_POLYGON_VERTICES, Polygon2};
pub use primitives::{Ray, Triangle, Vec2, Vec3};

#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum GeometryError {
    #[error("{0}")]
    Invalid(&'static str),
    #[error("{0}")]
    Numerical(&'static str),
    #[error("{0}")]
    Allocation(&'static str),
}

pub type Result<T> = std::result::Result<T, GeometryError>;

pub(crate) const TOLERANCE: f64 = 1e-12;

/// Fixed-order compensated accumulation, independent of worker scheduling.
pub(crate) fn sum(values: impl IntoIterator<Item = f64>) -> f64 {
    let mut total = 0.0_f64;
    let mut correction = 0.0;
    for value in values {
        let next = total + value;
        correction += if total.abs() >= value.abs() {
            (total - next) + value
        } else {
            (value - next) + total
        };
        total = next;
    }
    total + correction
}
