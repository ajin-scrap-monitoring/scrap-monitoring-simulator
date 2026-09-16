use std::{collections::BTreeSet, path::Path};

use super::{
    EnvironmentConfig, SensorConfig, compensated_sum, polygon,
    strict_json::{self as json, Object},
};
use crate::error::{ConfigurationError, ErrorKind, Result};
use crate::geometry::MAX_POLYGON_VERTICES;

pub fn load_environment(path: impl AsRef<Path>) -> Result<EnvironmentConfig> {
    parse_environment(&json::read_document(
        path.as_ref(),
        "environment configuration",
    )?)
}

pub fn parse_environment(document: &str) -> Result<EnvironmentConfig> {
    let value = json::parse_document(document)?;
    let root = Object::new(
        &value,
        "$",
        &[
            "environment_id",
            "length_unit",
            "angle_unit",
            "boundary_xy_m",
            "floor_z_m",
            "top_z_m",
            "sensors",
        ],
    )?;
    for (key, expected) in [("length_unit", "m"), ("angle_unit", "deg")] {
        if root.get(key).as_str() != Some(expected) {
            return Err(ConfigurationError::new(
                ErrorKind::Range,
                root.at(key),
                format!("must be {expected:?}"),
            ));
        }
    }
    let boundary_values = root.array("boundary_xy_m")?;
    if boundary_values.len() > MAX_POLYGON_VERTICES {
        return Err(ConfigurationError::new(
            ErrorKind::Range,
            "$.boundary_xy_m",
            format!("must contain at most {MAX_POLYGON_VERTICES} vertices"),
        ));
    }
    let boundary = boundary_values
        .iter()
        .enumerate()
        .map(|(index, value)| json::coordinate(value, &format!("$.boundary_xy_m[{index}]")))
        .collect::<Result<Vec<_>>>()?;
    polygon::validate(&boundary)?;
    let floor_z_m = root.number("floor_z_m")?;
    let top_z_m = root.number("top_z_m")?;
    if top_z_m <= floor_z_m {
        return Err(ConfigurationError::new(
            ErrorKind::Range,
            "$.top_z_m",
            "must be greater than $.floor_z_m",
        ));
    }
    let sensors = root
        .array("sensors")?
        .iter()
        .enumerate()
        .map(|(index, value)| {
            let sensor = Object::new(
                value,
                format!("$.sensors[{index}]"),
                &["sensor_id", "p0_m", "u0", "u90"],
            )?;
            let u0 = json::coordinate::<3>(sensor.get("u0"), &sensor.at("u0"))?;
            let u90 = json::coordinate::<3>(sensor.get("u90"), &sensor.at("u90"))?;
            for (key, vector) in [("u0", u0), ("u90", u90)] {
                let length =
                    compensated_sum(vector.iter().map(|component| component * component)).sqrt();
                if (length - 1.0).abs() > 1e-6 {
                    return Err(ConfigurationError::new(
                        ErrorKind::Geometry,
                        sensor.at(key),
                        "must be a unit vector",
                    ));
                }
            }
            let dot = compensated_sum(u0.iter().zip(u90).map(|(left, right)| left * right));
            if dot.abs() > 1e-6 {
                return Err(ConfigurationError::new(
                    ErrorKind::Geometry,
                    sensor.at("u0"),
                    "u0 and u90 must be orthogonal",
                ));
            }
            Ok(SensorConfig {
                sensor_id: sensor.string("sensor_id")?,
                p0_m: json::coordinate(sensor.get("p0_m"), &sensor.at("p0_m"))?,
                u0,
                u90,
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
    Ok(EnvironmentConfig {
        environment_id: root.string("environment_id")?,
        boundary_xy_m: boundary,
        floor_z_m,
        top_z_m,
        sensors,
    })
}
