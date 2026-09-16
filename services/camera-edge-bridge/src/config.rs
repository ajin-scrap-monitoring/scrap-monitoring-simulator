use std::env;
use std::path::PathBuf;
use std::time::Duration;

use std::fmt;

use tungstenite::http::Uri;

const SERVER_URL: &str = "SCRAP_SYNTHETIC_CAMERA_SERVER_URL";
const DEVICE: &str = "SCRAP_SYNTHETIC_CAMERA_DEVICE";
const CONNECT_TIMEOUT_MS: &str = "SCRAP_SYNTHETIC_CAMERA_CONNECT_TIMEOUT_MS";
const IO_TIMEOUT_MS: &str = "SCRAP_SYNTHETIC_CAMERA_IO_TIMEOUT_MS";
const RECONNECT_INITIAL_MS: &str = "SCRAP_SYNTHETIC_CAMERA_RECONNECT_INITIAL_MS";
const RECONNECT_MAX_MS: &str = "SCRAP_SYNTHETIC_CAMERA_RECONNECT_MAX_MS";

#[derive(Clone, Debug)]
pub struct Config {
    pub server_url: ServerUrl,
    pub device: PathBuf,
    pub connect_timeout: Duration,
    pub io_timeout: Duration,
    pub reconnect_initial: Duration,
    pub reconnect_max: Duration,
}

#[derive(Clone, Debug)]
pub struct ServerUrl {
    raw: String,
    host: String,
    port: u16,
}

impl ServerUrl {
    pub fn as_str(&self) -> &str {
        &self.raw
    }

    pub fn host(&self) -> &str {
        &self.host
    }

    pub fn port(&self) -> u16 {
        self.port
    }
}

impl fmt::Display for ServerUrl {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.raw.fmt(formatter)
    }
}

impl Config {
    pub fn from_env() -> Result<Self, String> {
        Self::from_lookup(|name| env::var(name).ok())
    }

    fn from_lookup<F>(lookup: F) -> Result<Self, String>
    where
        F: Fn(&str) -> Option<String>,
    {
        let raw_url = required(&lookup, SERVER_URL)?;
        let server_url = parse_server_url(&raw_url)?;
        let device = PathBuf::from(
            lookup(DEVICE).unwrap_or_else(|| "/dev/scrap-synthetic-camera".to_owned()),
        );
        if !device.is_absolute() {
            return Err(format!("{DEVICE} must be an absolute path"));
        }

        let connect_timeout = parse_milliseconds(&lookup, CONNECT_TIMEOUT_MS, 5_000, 5_000)?;
        let io_timeout = parse_milliseconds(&lookup, IO_TIMEOUT_MS, 1_000, 5_000)?;
        let reconnect_initial = parse_milliseconds(&lookup, RECONNECT_INITIAL_MS, 500, 60_000)?;
        let reconnect_max = parse_milliseconds(&lookup, RECONNECT_MAX_MS, 30_000, 300_000)?;
        if reconnect_initial > reconnect_max {
            return Err(format!(
                "{RECONNECT_INITIAL_MS} must not exceed {RECONNECT_MAX_MS}"
            ));
        }

        Ok(Self {
            server_url,
            device,
            connect_timeout,
            io_timeout,
            reconnect_initial,
            reconnect_max,
        })
    }
}

fn required<F>(lookup: &F, name: &str) -> Result<String, String>
where
    F: Fn(&str) -> Option<String>,
{
    lookup(name)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("{name} is required"))
}

fn parse_server_url(raw: &str) -> Result<ServerUrl, String> {
    let uri = raw
        .parse::<Uri>()
        .map_err(|error| format!("invalid {SERVER_URL}: {error}"))?;
    if uri.scheme_str() != Some("ws") {
        return Err(format!("{SERVER_URL} must use ws"));
    }
    let authority = uri
        .authority()
        .ok_or_else(|| format!("{SERVER_URL} must contain a host and port"))?;
    let host = uri
        .host()
        .filter(|host| !host.is_empty())
        .ok_or_else(|| format!("{SERVER_URL} must contain a host and port"))?;
    let Some(port) = uri.port_u16() else {
        return Err(format!("{SERVER_URL} must contain a host and port"));
    };
    if uri.path() != "/camera/v1/stream" {
        return Err(format!("{SERVER_URL} path must be /camera/v1/stream"));
    }
    if authority.as_str().contains('@') || uri.query().is_some() {
        return Err(format!(
            "{SERVER_URL} must not contain credentials or query"
        ));
    }
    Ok(ServerUrl {
        raw: raw.to_owned(),
        host: host.to_owned(),
        port,
    })
}

fn parse_milliseconds<F>(
    lookup: &F,
    name: &str,
    default: u64,
    maximum: u64,
) -> Result<Duration, String>
where
    F: Fn(&str) -> Option<String>,
{
    let raw = lookup(name).unwrap_or_else(|| default.to_string());
    let value = raw
        .parse::<u64>()
        .map_err(|_| format!("{name} must be an integer"))?;
    if value == 0 || value > maximum {
        return Err(format!("{name} must be between 1 and {maximum}"));
    }
    Ok(Duration::from_millis(value))
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use super::*;

    fn config(values: &[(&str, &str)]) -> Result<Config, String> {
        let values = values
            .iter()
            .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
            .collect::<HashMap<_, _>>();
        Config::from_lookup(|name| values.get(name).cloned())
    }

    #[test]
    fn requires_server_url() {
        assert_eq!(
            config(&[]).unwrap_err(),
            format!("{SERVER_URL} is required")
        );
    }

    #[test]
    fn applies_bounded_defaults() {
        let config = config(&[(SERVER_URL, "ws://visualizer:18000/camera/v1/stream")])
            .expect("valid config");

        assert_eq!(config.device, PathBuf::from("/dev/scrap-synthetic-camera"));
        assert_eq!(config.connect_timeout, Duration::from_secs(5));
        assert_eq!(config.io_timeout, Duration::from_secs(1));
        assert_eq!(config.reconnect_initial, Duration::from_millis(500));
        assert_eq!(config.reconnect_max, Duration::from_secs(30));
    }

    #[test]
    fn rejects_url_outside_stream_contract() {
        let error = config(&[(SERVER_URL, "ws://visualizer:18000/status")]).unwrap_err();
        assert!(error.contains("path must be /camera/v1/stream"));

        let error = config(&[(
            SERVER_URL,
            "wss://visualizer:18000/camera/v1/stream?token=secret",
        )])
        .unwrap_err();
        assert!(error.contains("must use ws"));
    }

    #[test]
    fn validates_backoff_and_connect_timeout() {
        let error = config(&[
            (SERVER_URL, "ws://visualizer:18000/camera/v1/stream"),
            (RECONNECT_INITIAL_MS, "2000"),
            (RECONNECT_MAX_MS, "1000"),
        ])
        .unwrap_err();
        assert!(error.contains("must not exceed"));

        let error = config(&[
            (SERVER_URL, "ws://visualizer:18000/camera/v1/stream"),
            (CONNECT_TIMEOUT_MS, "5001"),
        ])
        .unwrap_err();
        assert!(error.contains("must be between 1 and 5000"));
    }
}
