use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;

use scrap_synthetic_camera_bridge::bridge;
use scrap_synthetic_camera_bridge::config::Config;
use signal_hook::consts::signal::{SIGINT, SIGTERM};

fn main() -> ExitCode {
    match start() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("scrap-synthetic-camera-bridge: {error}");
            ExitCode::FAILURE
        }
    }
}

fn start() -> Result<(), String> {
    let config = Config::from_env()?;
    let shutdown = Arc::new(AtomicBool::new(false));
    signal_hook::flag::register(SIGTERM, Arc::clone(&shutdown))
        .map_err(|error| format!("cannot register SIGTERM handler: {error}"))?;
    signal_hook::flag::register(SIGINT, Arc::clone(&shutdown))
        .map_err(|error| format!("cannot register SIGINT handler: {error}"))?;
    bridge::run(config, shutdown)
}
