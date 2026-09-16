//! Command-line configuration for validation and runtime execution.

use std::{net::SocketAddr, path::PathBuf};

use clap::{Parser, Subcommand};

use crate::{configuration::load_simulator_inputs, runtime::RuntimeSettings};

#[derive(Debug, Parser)]
#[command(name = "scrap-monitoring-simulation-server", version)]
pub struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Check {
        #[arg(
            long,
            env = "SCRAP_SIMULATOR_CONFIG",
            default_value = "config/simulation-server.v1.json"
        )]
        config: PathBuf,
    },
    Run {
        #[arg(
            long,
            env = "SCRAP_SIMULATOR_CONFIG",
            default_value = "config/simulation-server.v1.json"
        )]
        config: PathBuf,
        #[arg(
            long,
            env = "SCRAP_SIMULATOR_LIDAR_1_BIND",
            default_value = "0.0.0.0:8089"
        )]
        lidar_1_bind: SocketAddr,
        #[arg(
            long,
            env = "SCRAP_SIMULATOR_LIDAR_2_BIND",
            default_value = "0.0.0.0:8090"
        )]
        lidar_2_bind: SocketAddr,
        #[arg(long, env = "SCRAP_SIMULATOR_SCENE_HOST", default_value = "visualizer")]
        scene_host: String,
        #[arg(long, env = "SCRAP_SIMULATOR_SCENE_PORT", default_value_t = 17_000)]
        scene_port: u16,
        #[arg(long, env = "SCRAP_SIMULATOR_SCENE_INTERVAL_S", default_value_t = 1.0)]
        scene_interval_s: f64,
        #[arg(long, env = "SCRAP_SIMULATOR_MEAN_FILL_DURATION_S")]
        mean_fill_duration_s: Option<f64>,
    },
}

pub async fn execute() -> Result<(), String> {
    match Cli::parse().command {
        Command::Check { config } => {
            let inputs = load_simulator_inputs(config).map_err(|error| error.to_string())?;
            let sensors = inputs
                .environment
                .sensors
                .iter()
                .map(|sensor| sensor.sensor_id.as_str())
                .collect::<Vec<_>>();
            println!(
                "configuration valid: environment={} sensors={}",
                inputs.environment.environment_id,
                sensors.join(",")
            );
            Ok(())
        }
        Command::Run {
            config,
            lidar_1_bind,
            lidar_2_bind,
            scene_host,
            scene_port,
            scene_interval_s,
            mean_fill_duration_s,
        } => {
            let summary = crate::runtime::run(RuntimeSettings {
                config_path: config,
                lidar_1_bind,
                lidar_2_bind,
                scene_host,
                scene_port,
                scene_interval_s,
                mean_fill_duration_s,
            })
            .await
            .map_err(|error| error.to_string())?;
            println!(
                "{}",
                serde_json::to_string(&summary).map_err(|error| error.to_string())?
            );
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::CommandFactory;

    #[test]
    fn command_definition_is_consistent() {
        Cli::command().debug_assert();
    }

    #[test]
    fn run_accepts_mean_fill_duration_override() {
        let cli = Cli::try_parse_from([
            "scrap-monitoring-simulation-server",
            "run",
            "--mean-fill-duration-s",
            "600",
        ])
        .unwrap();
        let Command::Run {
            mean_fill_duration_s,
            ..
        } = cli.command
        else {
            panic!("expected run command");
        };
        assert_eq!(mean_fill_duration_s, Some(600.0));
    }
}
