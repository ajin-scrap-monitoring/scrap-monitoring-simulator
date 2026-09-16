use std::{collections::BTreeSet, path::Path};

use serde_json::Value;

use super::{
    QualityFrequencies, QualityProfileConfig, SensorQualityConfig,
    strict_json::{self as json, Object},
};
use crate::error::{ConfigurationError, ErrorKind, Result};

pub fn load_quality_profile(path: impl AsRef<Path>) -> Result<QualityProfileConfig> {
    parse_quality_profile(&json::read_document(path.as_ref(), "quality profile")?)
}

pub fn parse_quality_profile(document: &str) -> Result<QualityProfileConfig> {
    let value = json::parse_document(document)?;
    let root = Object::new(&value, "$", &["config_version", "sensors"])?;
    json::version(root.get("config_version"), 1, "$.config_version")?;
    let sensors = root
        .array("sensors")?
        .iter()
        .enumerate()
        .map(|(index, value)| {
            let sensor = Object::new(
                value,
                format!("$.sensors[{index}]"),
                &[
                    "sensor_id",
                    "valid_distance_frequencies",
                    "invalid_distance_frequencies",
                ],
            )?;
            Ok(SensorQualityConfig {
                sensor_id: sensor.string("sensor_id")?,
                valid_distance_frequencies: frequencies(
                    sensor.get("valid_distance_frequencies"),
                    &sensor.at("valid_distance_frequencies"),
                )?,
                invalid_distance_frequencies: frequencies(
                    sensor.get("invalid_distance_frequencies"),
                    &sensor.at("invalid_distance_frequencies"),
                )?,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    if sensors.is_empty() {
        return Err(ConfigurationError::new(
            ErrorKind::Range,
            "$.sensors",
            "must contain at least 1 sensor",
        ));
    }
    if sensors
        .iter()
        .map(|sensor| &sensor.sensor_id)
        .collect::<BTreeSet<_>>()
        .len()
        != sensors.len()
    {
        return Err(ConfigurationError::new(
            ErrorKind::Range,
            "$.sensors",
            "must contain unique sensor_id values",
        ));
    }
    Ok(QualityProfileConfig { sensors })
}

fn frequencies(value: &Value, path: &str) -> Result<QualityFrequencies> {
    let object = json::object(value, path)?;
    if object.is_empty() {
        return Err(ConfigurationError::new(
            ErrorKind::Range,
            path,
            "must contain at least 1 quality value",
        ));
    }
    let mut frequencies = [0; 256];
    for (key, value) in object {
        let quality = key
            .parse::<u8>()
            .ok()
            .filter(|quality| quality.to_string() == *key)
            .ok_or_else(|| {
                ConfigurationError::new(
                    ErrorKind::Range,
                    path,
                    "keys must be decimal integers from 0 through 255",
                )
            })?;
        frequencies[usize::from(quality)] =
            json::integer(value, &format!("{path}.{key}"), 1, i64::MAX as u64)?;
    }
    Ok(frequencies)
}
