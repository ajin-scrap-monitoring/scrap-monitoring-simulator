//! Strict JSON validation that retains integer precision and rejects duplicate keys.

use std::{collections::BTreeSet, fmt, path::Path};

use serde::de::{Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Value, value::RawValue};

use crate::error::{ConfigurationError, ErrorKind, Result};

pub fn read_document(path: &Path, description: &str) -> Result<String> {
    std::fs::read_to_string(path).map_err(|_| {
        ConfigurationError::new(
            ErrorKind::Read,
            path.display().to_string(),
            format!("cannot read {description}"),
        )
    })
}

pub fn parse_document(document: &str) -> Result<Value> {
    let value = serde_json::from_str(document)
        .map_err(|error| ConfigurationError::new(ErrorKind::Json, "$", error.to_string()))?;
    check_duplicates(document, "$", 0)?;
    Ok(value)
}

fn check_duplicates(document: &str, path: &str, depth: usize) -> Result<()> {
    if depth > 128 {
        return Err(ConfigurationError::new(
            ErrorKind::Json,
            path,
            "JSON nesting exceeds 128 levels",
        ));
    }
    let mut deserializer = serde_json::Deserializer::from_str(document);
    let visitor = UniqueKeys { path, depth };
    let result = match document.trim_start().as_bytes().first() {
        Some(b'{') => deserializer.deserialize_map(visitor),
        Some(b'[') => deserializer.deserialize_seq(visitor),
        _ => return Ok(()),
    };
    result.map_err(|error| ConfigurationError::new(ErrorKind::Json, path, error.to_string()))?
}

struct UniqueKeys<'a> {
    path: &'a str,
    depth: usize,
}

impl<'de> Visitor<'de> for UniqueKeys<'_> {
    type Value = Result<()>;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("a JSON object or array")
    }

    fn visit_map<A: MapAccess<'de>>(
        self,
        mut map: A,
    ) -> std::result::Result<Self::Value, A::Error> {
        let mut keys = BTreeSet::new();
        let mut failure = None;
        while let Some(key) = map.next_key::<String>()? {
            let path = format!("{}.{}", self.path, key);
            if is_serde_private_key(&key) && failure.is_none() {
                failure = Some(ConfigurationError::new(
                    ErrorKind::Json,
                    self.path,
                    "reserved serde JSON object key is not allowed",
                ));
            } else if !keys.insert(key.clone()) && failure.is_none() {
                failure = Some(ConfigurationError::new(
                    ErrorKind::DuplicateField,
                    &path,
                    format!("duplicate JSON field: {key}"),
                ));
            }
            let raw = map.next_value::<&RawValue>()?;
            if failure.is_none() {
                failure = check_duplicates(raw.get(), &path, self.depth + 1).err();
            }
        }
        Ok(failure.map_or(Ok(()), Err))
    }

    fn visit_seq<A: SeqAccess<'de>>(
        self,
        mut sequence: A,
    ) -> std::result::Result<Self::Value, A::Error> {
        let mut index = 0;
        let mut failure = None;
        while let Some(raw) = sequence.next_element::<&RawValue>()? {
            if failure.is_none() {
                failure = check_duplicates(
                    raw.get(),
                    &format!("{}[{index}]", self.path),
                    self.depth + 1,
                )
                .err();
            }
            index += 1;
        }
        Ok(failure.map_or(Ok(()), Err))
    }
}

fn is_serde_private_key(key: &str) -> bool {
    matches!(
        key,
        "$serde_json::private::Number" | "$serde_json::private::RawValue"
    )
}

pub(crate) struct Object<'a> {
    values: &'a Map<String, Value>,
    pub path: String,
}

impl<'a> Object<'a> {
    pub fn new(value: &'a Value, path: impl Into<String>, fields: &[&str]) -> Result<Self> {
        let path = path.into();
        let values = object(value, &path)?;
        let expected: BTreeSet<&str> = fields.iter().copied().collect();
        let missing: Vec<_> = expected
            .iter()
            .filter(|field| !values.contains_key(**field))
            .copied()
            .collect();
        if !missing.is_empty() {
            return Err(ConfigurationError::new(
                ErrorKind::MissingField,
                &path,
                format!("missing fields: {}", missing.join(", ")),
            ));
        }
        let unexpected: Vec<_> = values
            .keys()
            .filter(|field| !expected.contains(field.as_str()))
            .map(String::as_str)
            .collect();
        if !unexpected.is_empty() {
            return Err(ConfigurationError::new(
                ErrorKind::UnexpectedField,
                &path,
                format!("unexpected fields: {}", unexpected.join(", ")),
            ));
        }
        Ok(Self { values, path })
    }

    pub fn at(&self, key: &str) -> String {
        format!("{}.{}", self.path, key)
    }
    pub fn get(&self, key: &str) -> &'a Value {
        &self.values[key]
    }
    pub fn string(&self, key: &str) -> Result<String> {
        string(self.get(key), &self.at(key))
    }
    pub fn number(&self, key: &str) -> Result<f64> {
        number(self.get(key), &self.at(key))
    }
    pub fn positive(&self, key: &str) -> Result<f64> {
        positive(self.get(key), &self.at(key))
    }
    pub fn non_negative(&self, key: &str) -> Result<f64> {
        non_negative(self.get(key), &self.at(key))
    }
    pub fn ratio(&self, key: &str) -> Result<f64> {
        ratio(self.get(key), &self.at(key))
    }
    pub fn boolean(&self, key: &str) -> Result<bool> {
        self.get(key).as_bool().ok_or_else(|| {
            ConfigurationError::new(ErrorKind::Type, self.at(key), "must be a boolean")
        })
    }
    pub fn array(&self, key: &str) -> Result<&'a [Value]> {
        array(self.get(key), &self.at(key))
    }
    pub fn range(&self, key: &str, parser: fn(&Value, &str) -> Result<f64>) -> Result<[f64; 2]> {
        let path = self.at(key);
        let items = array(self.get(key), &path)?;
        if items.len() != 2 {
            return Err(ConfigurationError::new(
                ErrorKind::Range,
                path,
                "must contain exactly 2 numbers",
            ));
        }
        let lower = parser(&items[0], &format!("{path}[0]"))?;
        let upper = parser(&items[1], &format!("{path}[1]"))?;
        if upper < lower {
            return Err(ConfigurationError::new(
                ErrorKind::Range,
                format!("{path}[1]"),
                "must be at least the lower bound",
            ));
        }
        Ok([lower, upper])
    }
}

pub(crate) fn object<'a>(value: &'a Value, path: &str) -> Result<&'a Map<String, Value>> {
    value
        .as_object()
        .ok_or_else(|| ConfigurationError::new(ErrorKind::Type, path, "must be an object"))
}

pub(crate) fn array<'a>(value: &'a Value, path: &str) -> Result<&'a [Value]> {
    value
        .as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| ConfigurationError::new(ErrorKind::Type, path, "must be an array"))
}

pub(crate) fn string(value: &Value, path: &str) -> Result<String> {
    value
        .as_str()
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| ConfigurationError::new(ErrorKind::Type, path, "must be a non-empty string"))
}

pub(crate) fn number(value: &Value, path: &str) -> Result<f64> {
    let value = value
        .as_number()
        .ok_or_else(|| ConfigurationError::new(ErrorKind::Type, path, "must be a number"))?;
    value
        .as_f64()
        .filter(|value| value.is_finite())
        .ok_or_else(|| ConfigurationError::new(ErrorKind::Range, path, "must be finite"))
}

pub(crate) fn integer_text(value: &Value, path: &str) -> Result<String> {
    let raw = value
        .as_number()
        .map(ToString::to_string)
        .ok_or_else(|| ConfigurationError::new(ErrorKind::Type, path, "must be an integer"))?;
    if raw.contains(['.', 'e', 'E']) {
        return Err(ConfigurationError::new(
            ErrorKind::Type,
            path,
            "must be an integer",
        ));
    }
    if raw.starts_with('-') && raw != "-0" {
        return Err(ConfigurationError::new(
            ErrorKind::Range,
            path,
            "must be non-negative",
        ));
    }
    Ok(if raw == "-0" { "0".into() } else { raw })
}

pub(crate) fn integer(value: &Value, path: &str, minimum: u64, maximum: u64) -> Result<u64> {
    let raw = integer_text(value, path)?;
    let result = raw.parse::<u64>().map_err(|_| {
        ConfigurationError::new(ErrorKind::Range, path, format!("must be at most {maximum}"))
    })?;
    if !(minimum..=maximum).contains(&result) {
        return Err(ConfigurationError::new(
            ErrorKind::Range,
            path,
            format!("must be in [{minimum}, {maximum}]"),
        ));
    }
    Ok(result)
}

pub(crate) fn positive(value: &Value, path: &str) -> Result<f64> {
    bound(
        number(value, path)?,
        path,
        |value| value > 0.0,
        "must be greater than zero",
    )
}

pub(crate) fn non_negative(value: &Value, path: &str) -> Result<f64> {
    bound(
        number(value, path)?,
        path,
        |value| value >= 0.0,
        "must be non-negative",
    )
}

pub(crate) fn ratio(value: &Value, path: &str) -> Result<f64> {
    bound(
        number(value, path)?,
        path,
        |value| (0.0..=1.0).contains(&value),
        "must be between 0 and 1",
    )
}

pub(crate) fn positive_ratio(value: &Value, path: &str) -> Result<f64> {
    bound(
        ratio(value, path)?,
        path,
        |value| value > 0.0,
        "must be greater than zero",
    )
}

pub(crate) fn bound(
    value: f64,
    path: &str,
    predicate: impl FnOnce(f64) -> bool,
    message: &str,
) -> Result<f64> {
    if predicate(value) {
        Ok(value)
    } else {
        Err(ConfigurationError::new(ErrorKind::Range, path, message))
    }
}

pub(crate) fn coordinate<const N: usize>(value: &Value, path: &str) -> Result<[f64; N]> {
    let items = array(value, path)?;
    if items.len() != N {
        return Err(ConfigurationError::new(
            ErrorKind::Range,
            path,
            format!("must contain exactly {N} numbers"),
        ));
    }
    let mut values = [0.0; N];
    for (index, item) in items.iter().enumerate() {
        values[index] = number(item, &format!("{path}[{index}]"))?;
    }
    Ok(values)
}

pub(crate) fn version(value: &Value, expected: u32, path: &str) -> Result<()> {
    if value.as_f64() == Some(f64::from(expected)) {
        Ok(())
    } else {
        Err(ConfigurationError::new(
            ErrorKind::Range,
            path,
            format!("must be {expected}"),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::{integer, number, parse_document};
    use crate::error::ErrorKind;

    #[test]
    fn integer_precision_survives_the_document_parser() {
        let value = parse_document("18446744073709551615").unwrap();
        assert_eq!(integer(&value, "$", 0, u64::MAX).unwrap(), u64::MAX);
        let decimal = parse_document("1.0").unwrap();
        assert_eq!(
            integer(&decimal, "$", 0, u64::MAX).unwrap_err().kind,
            ErrorKind::Type
        );
        assert_eq!(number(&decimal, "$").unwrap(), 1.0);
    }

    #[test]
    fn escaped_keys_and_duplicates_inside_arrays_are_checked() {
        let error = parse_document(r#"{"outer":[{"same":0,"\u0073ame":1}],"tail":2}"#).unwrap_err();
        assert_eq!(error.kind, ErrorKind::DuplicateField);
        assert_eq!(error.path, "$.outer[0].same");
    }

    #[test]
    fn enormous_exponents_are_rejected_as_non_finite_numbers() {
        let value = parse_document("1e999").unwrap();
        assert_eq!(number(&value, "$").unwrap_err().kind, ErrorKind::Range);
    }

    #[test]
    fn serde_private_object_keys_cannot_change_json_value_types() {
        for document in [
            r#"{"seed":{"$serde_json::private::Number":"123"}}"#,
            r#"{"seed":{"$serde_json::private::RawValue":"123"}}"#,
            r#"{"seed":{"\u0024serde_json::private::Number":"123"}}"#,
        ] {
            let error = parse_document(document).unwrap_err();
            assert_eq!(error.kind, ErrorKind::Json);
            assert_eq!(error.path, "$.seed");
        }
    }
}
