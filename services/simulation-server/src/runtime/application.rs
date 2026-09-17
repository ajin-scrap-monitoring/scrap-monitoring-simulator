//! Process lifecycle for paced canonical scene and independent output adapters.

use std::{
    collections::BTreeMap,
    fmt::Write,
    fs,
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use sha2::{Digest, Sha256};
use tokio::sync::mpsc;
use uuid::Uuid;

use crate::{
    configuration::{SimulatorInputs, load_simulator_inputs},
    error::ConfigurationError,
    s2e::{EndpointConfig, EndpointStats, S2eError, S2eRuntime},
    scene_stream::{
        ScenePublisherConfig, ScenePublisherStats, SceneStreamError, SceneStreamHeader,
        StaticScene, TcpScenePublisher,
    },
};

use super::{GenerationBatch, GenerationRuntime, GenerationRuntimeError};

const GENERATION_OUTPUT_CAPACITY: usize = 1;

#[derive(Clone, Debug)]
pub struct RuntimeSettings {
    pub config_path: PathBuf,
    pub lidar_1_bind: SocketAddr,
    pub lidar_2_bind: SocketAddr,
    pub scene_host: String,
    pub scene_port: u16,
    pub mean_fill_duration_s: Option<f64>,
}

impl RuntimeSettings {
    pub fn validate(&self) -> Result<(), ApplicationError> {
        if self.lidar_1_bind == self.lidar_2_bind {
            return Err(ApplicationError::Invalid(
                "LiDAR UDP bind addresses must be different",
            ));
        }
        if self.scene_host.is_empty() {
            return Err(ApplicationError::Invalid(
                "scene stream host must not be empty",
            ));
        }
        if self.scene_port == 0 {
            return Err(ApplicationError::Invalid(
                "scene stream port must be from 1 through 65535",
            ));
        }
        if self
            .mean_fill_duration_s
            .is_some_and(|duration| !duration.is_finite() || duration <= 0.0)
        {
            return Err(ApplicationError::Invalid(
                "mean fill duration must be finite and positive",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct ApplicationSummary {
    pub run_id: String,
    pub generated_scans: u64,
    pub s2e: BTreeMap<String, EndpointStats>,
    pub scene_endpoint: String,
    pub scene: ScenePublisherStats,
}

#[derive(Debug, thiserror::Error)]
pub enum ApplicationError {
    #[error("invalid runtime configuration: {0}")]
    Invalid(&'static str),
    #[error(transparent)]
    Configuration(#[from] ConfigurationError),
    #[error(transparent)]
    Generation(#[from] GenerationRuntimeError),
    #[error(transparent)]
    Scene(#[from] SceneStreamError),
    #[error(transparent)]
    S2e(#[from] S2eError),
    #[error("cannot read simulation input for fingerprinting: {0}")]
    Fingerprint(#[source] std::io::Error),
    #[error("generation coordinator could not start: {0}")]
    GenerationStart(#[source] std::io::Error),
    #[error("generation coordinator stopped unexpectedly")]
    GenerationStopped,
    #[error("generation coordinator failed: {0}")]
    GenerationWorker(String),
    #[error("generation coordinator panicked")]
    GenerationPanic,
    #[error("generation coordinator join task failed: {0}")]
    GenerationJoin(#[source] tokio::task::JoinError),
    #[error("runtime signal registration failed: {0}")]
    Signal(#[source] std::io::Error),
}

pub async fn run(settings: RuntimeSettings) -> Result<ApplicationSummary, ApplicationError> {
    settings.validate()?;
    let mut inputs = load_simulator_inputs(&settings.config_path)?;
    if let Some(duration) = settings.mean_fill_duration_s {
        inputs.simulator.scenario.mean_fill_duration_s = duration;
    }
    let fingerprint = input_fingerprint(&settings.config_path, &inputs)?;
    let run_id = Uuid::new_v4().to_string();
    let generation = GenerationRuntime::from_inputs(&inputs)?;
    let sensor_ids = generation
        .sensor_ids()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if sensor_ids.len() != 2 {
        return Err(ApplicationError::Invalid(
            "simulation requires exactly two sensors",
        ));
    }

    let measurement = &inputs.simulator.measurement;
    let endpoint_configs = vec![
        EndpointConfig {
            sensor_id: sensor_ids[0].clone(),
            bind: settings.lidar_1_bind,
            sample_rate_hz: measurement.sample_rate_hz,
            rotation_rate_hz: measurement.rotation_rate_hz,
            max_distance_m: measurement.max_distance_m,
        },
        EndpointConfig {
            sensor_id: sensor_ids[1].clone(),
            bind: settings.lidar_2_bind,
            sample_rate_hz: measurement.sample_rate_hz,
            rotation_rate_hz: measurement.rotation_rate_hz,
            max_distance_m: measurement.max_distance_m,
        },
    ];
    let s2e = S2eRuntime::bind(endpoint_configs).await?;

    let initial_keyframe = generation.model_snapshot()?;
    let scene_config =
        ScenePublisherConfig::new(settings.scene_host, settings.scene_port, 3.0, 2.0, 0.5, 5.0)?;
    let scene_header = SceneStreamHeader::new(
        inputs.environment.environment_id.clone(),
        run_id.clone(),
        fingerprint,
        inputs.simulator.seed,
        StaticScene::from_inputs(&inputs, &initial_keyframe.surface)?,
    )?;
    let mut scene = TcpScenePublisher::new(scene_config, scene_header, initial_keyframe)?;
    scene.start()?;
    let scene_endpoint = scene.endpoint();

    let mut coordinator = GenerationCoordinator::start(generation, Instant::now())?;
    let mut generated_scans = 0_u64;
    let execution = loop {
        tokio::select! {
            signal = shutdown_signal() => {
                match signal {
                    Ok(()) => break Ok(()),
                    Err(error) => break Err(error),
                }
            }
            output = coordinator.next() => {
                let Some(output) = output else {
                    break Err(ApplicationError::GenerationStopped);
                };
                generated_scans = generated_scans.saturating_add(output.batch.scans.len() as u64);
                let mut scan_error = None;
                for scan in output.batch.scans {
                    if let Err(error) = s2e.publish(scan) {
                        scan_error = Some(ApplicationError::from(error));
                        break;
                    }
                }
                if let Some(error) = scan_error {
                    break Err(error);
                }
                let mut scene_error = None;
                for keyframe in output.batch.canonical_keyframes {
                    if let Err(error) = scene.publish(keyframe) {
                        scene_error = Some(error);
                        break;
                    }
                }
                if let Some(error) = scene_error {
                    break Err(ApplicationError::from(error));
                }
            }
        }
    };

    let generation_shutdown = coordinator.shutdown().await;
    scene.close().await;
    let scene_stats = scene.stats();
    let s2e_stats = s2e.stats();
    let s2e_shutdown = s2e.close().await;
    execution?;
    generation_shutdown?;
    s2e_shutdown?;

    Ok(ApplicationSummary {
        run_id,
        generated_scans,
        s2e: s2e_stats,
        scene_endpoint,
        scene: scene_stats,
    })
}

struct GeneratedOutput {
    batch: GenerationBatch,
}

struct GenerationCoordinator {
    stop: Arc<AtomicBool>,
    receiver: mpsc::Receiver<GeneratedOutput>,
    thread: Option<JoinHandle<std::result::Result<(), String>>>,
}

impl GenerationCoordinator {
    fn start(mut runtime: GenerationRuntime, epoch: Instant) -> Result<Self, ApplicationError> {
        let stop = Arc::new(AtomicBool::new(false));
        let worker_stop = Arc::clone(&stop);
        let (sender, receiver) = mpsc::channel(GENERATION_OUTPUT_CAPACITY);
        let worker = thread::Builder::new()
            .name("simulation-coordinator".into())
            .spawn(move || {
                let result = run_generation_worker(&mut runtime, epoch, &worker_stop, &sender);
                let shutdown = runtime.shutdown().map_err(|error| error.to_string());
                result.and(shutdown)
            })
            .map_err(ApplicationError::GenerationStart)?;
        Ok(Self {
            stop,
            receiver,
            thread: Some(worker),
        })
    }

    async fn next(&mut self) -> Option<GeneratedOutput> {
        self.receiver.recv().await
    }

    async fn shutdown(&mut self) -> Result<(), ApplicationError> {
        self.stop.store(true, Ordering::Release);
        self.receiver.close();
        let Some(worker) = self.thread.take() else {
            return Ok(());
        };
        worker.thread().unpark();
        match tokio::task::spawn_blocking(move || worker.join())
            .await
            .map_err(ApplicationError::GenerationJoin)?
        {
            Ok(Ok(())) => Ok(()),
            Ok(Err(error)) => Err(ApplicationError::GenerationWorker(error)),
            Err(_) => Err(ApplicationError::GenerationPanic),
        }
    }
}

impl Drop for GenerationCoordinator {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = &self.thread {
            worker.thread().unpark();
        }
    }
}

fn run_generation_worker(
    runtime: &mut GenerationRuntime,
    epoch: Instant,
    stop: &AtomicBool,
    sender: &mpsc::Sender<GeneratedOutput>,
) -> std::result::Result<(), String> {
    while !stop.load(Ordering::Acquire) {
        let batch = runtime
            .next_completed_scans()
            .map_err(|error| error.to_string())?;
        let deadline = epoch
            .checked_add(Duration::from_secs_f64(batch.completed_at_s))
            .ok_or_else(|| "simulation deadline exceeds monotonic clock range".to_owned())?;
        while !stop.load(Ordering::Acquire) {
            let now = Instant::now();
            if now >= deadline {
                break;
            }
            thread::park_timeout((deadline - now).min(Duration::from_millis(20)));
        }
        if stop.load(Ordering::Acquire) {
            break;
        }
        if sender.blocking_send(GeneratedOutput { batch }).is_err() {
            break;
        }
    }
    Ok(())
}

fn input_fingerprint(path: &Path, inputs: &SimulatorInputs) -> Result<String, ApplicationError> {
    let mut hasher = Sha256::new();
    for source in [
        path,
        inputs.simulator.environment_path.as_path(),
        inputs.simulator.quality_profile_path.as_path(),
    ] {
        let bytes = fs::read(source).map_err(ApplicationError::Fingerprint)?;
        hasher.update((bytes.len() as u64).to_le_bytes());
        hasher.update(bytes);
    }
    hasher.update(b"effective-mean-fill-duration-s");
    hasher.update(
        inputs
            .simulator
            .scenario
            .mean_fill_duration_s
            .to_bits()
            .to_le_bytes(),
    );
    let digest = hasher.finalize();
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        write!(&mut encoded, "{byte:02x}").expect("writing to a String cannot fail");
    }
    Ok(encoded)
}

async fn shutdown_signal() -> Result<(), ApplicationError> {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{SignalKind, signal};

        let mut interrupt = signal(SignalKind::interrupt()).map_err(ApplicationError::Signal)?;
        let mut terminate = signal(SignalKind::terminate()).map_err(ApplicationError::Signal)?;
        tokio::select! {
            received = interrupt.recv() => signal_received(received, "SIGINT"),
            received = terminate.recv() => signal_received(received, "SIGTERM"),
        }
    }
    #[cfg(not(unix))]
    {
        tokio::signal::ctrl_c()
            .await
            .map_err(ApplicationError::Signal)
    }
}

#[cfg(unix)]
fn signal_received(received: Option<()>, name: &'static str) -> Result<(), ApplicationError> {
    if received.is_some() {
        Ok(())
    } else {
        Err(ApplicationError::Signal(std::io::Error::other(format!(
            "{name} signal stream stopped"
        ))))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runtime_settings_reject_overlapping_udp_endpoints() {
        let settings = RuntimeSettings {
            config_path: "config/simulation-server.v1.json".into(),
            lidar_1_bind: "127.0.0.1:8089".parse().unwrap(),
            lidar_2_bind: "127.0.0.1:8089".parse().unwrap(),
            scene_host: "127.0.0.1".into(),
            scene_port: 17_000,
            mean_fill_duration_s: None,
        };
        assert!(matches!(
            settings.validate(),
            Err(ApplicationError::Invalid(_))
        ));
    }

    #[test]
    fn runtime_settings_accept_distinct_udp_hosts_on_the_same_port() {
        let settings = RuntimeSettings {
            config_path: "config/simulation-server.v1.json".into(),
            lidar_1_bind: "127.0.0.2:8089".parse().unwrap(),
            lidar_2_bind: "127.0.0.3:8089".parse().unwrap(),
            scene_host: "127.0.0.1".into(),
            scene_port: 17_000,
            mean_fill_duration_s: None,
        };
        assert!(settings.validate().is_ok());
    }

    #[test]
    fn runtime_settings_reject_invalid_mean_fill_duration_override() {
        let settings = RuntimeSettings {
            config_path: "config/simulation-server.v1.json".into(),
            lidar_1_bind: "127.0.0.2:8089".parse().unwrap(),
            lidar_2_bind: "127.0.0.3:8089".parse().unwrap(),
            scene_host: "127.0.0.1".into(),
            scene_port: 17_000,
            mean_fill_duration_s: Some(0.0),
        };
        assert!(matches!(
            settings.validate(),
            Err(ApplicationError::Invalid(_))
        ));
    }

    #[test]
    fn input_fingerprint_is_stable_and_lowercase_hex() {
        let path = Path::new("config/simulation-server.v1.json");
        let mut inputs = load_simulator_inputs(path).unwrap();
        let first = input_fingerprint(path, &inputs).unwrap();
        let second = input_fingerprint(path, &inputs).unwrap();
        assert_eq!(first, second);
        assert_eq!(first.len(), 64);
        assert!(
            first
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        );
        inputs.simulator.scenario.mean_fill_duration_s = 601.0;
        assert_ne!(first, input_fingerprint(path, &inputs).unwrap());
    }
}
