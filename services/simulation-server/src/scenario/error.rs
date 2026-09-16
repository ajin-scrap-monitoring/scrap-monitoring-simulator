use crate::{geometry::GeometryError, randomness::RandomError, rate_profile::RateProfileError};

#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum ScenarioError {
    #[error("{0}")]
    Invalid(&'static str),
    #[error("{0}")]
    State(&'static str),
    #[error("{0}")]
    Resource(&'static str),
    #[error(transparent)]
    Geometry(#[from] GeometryError),
    #[error(transparent)]
    Random(#[from] RandomError),
    #[error(transparent)]
    RateProfile(#[from] RateProfileError),
}

pub type Result<T> = std::result::Result<T, ScenarioError>;
