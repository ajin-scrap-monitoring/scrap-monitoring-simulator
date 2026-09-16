//! Stable error categories and field paths for configuration validation.

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ErrorKind {
    Read,
    Json,
    DuplicateField,
    MissingField,
    UnexpectedField,
    Type,
    Range,
    Geometry,
    CrossInput,
    Runtime,
}

#[derive(Debug, thiserror::Error)]
#[error("{path}: {message}")]
pub struct ConfigurationError {
    pub kind: ErrorKind,
    pub path: String,
    pub message: String,
}

impl ConfigurationError {
    pub fn new(kind: ErrorKind, path: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            kind,
            path: path.into(),
            message: message.into(),
        }
    }
}

pub type Result<T> = std::result::Result<T, ConfigurationError>;
