//! Best-effort version 2 canonical scene segment streaming over TCP.

use std::{
    collections::BTreeSet,
    sync::{
        Arc, Mutex, MutexGuard,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};

use serde::Serialize;
use tokio::{
    io::AsyncWriteExt,
    net::TcpStream,
    sync::Notify,
    task::JoinHandle,
    time::{sleep, timeout},
};

use crate::{
    configuration::SimulatorInputs,
    output_format::{HeightRows, ScenarioDocument, validate_model_snapshot},
    randomness::{ModelRng, RandomError, RandomSource, StreamScope},
    scenario::ScenarioModelSnapshot,
};

pub const SCENE_VERSION: u32 = 2;
pub const MAX_SCENE_LINE_BYTES: usize = 1_048_576;
pub const MAX_SCENE_SEQUENCE: u64 = 9_007_199_254_740_991;
pub const DEFAULT_SCENE_HOST: &str = "127.0.0.1";
pub const DEFAULT_SCENE_PORT: u16 = 17_000;

#[derive(Debug, thiserror::Error)]
pub enum SceneStreamError {
    #[error("{0}")]
    Invalid(&'static str),
    #[error("scene JSON encoding failed: {0}")]
    Json(#[from] serde_json::Error),
    #[error("scene line exceeds {maximum} bytes: {actual}")]
    LineTooLarge { actual: usize, maximum: usize },
    #[error("scene sequence range is exhausted")]
    SequenceExhausted,
    #[error("scene publisher is closed")]
    Closed,
    #[error("scene publisher requires a Tokio runtime")]
    RuntimeUnavailable,
    #[error(transparent)]
    Random(#[from] RandomError),
}

pub type Result<T> = std::result::Result<T, SceneStreamError>;

#[derive(Clone, Debug, PartialEq)]
pub struct SceneSensor {
    sensor_id: String,
    p0_m: [f64; 3],
    u0: [f64; 3],
    u90: [f64; 3],
}

impl SceneSensor {
    pub fn new(
        sensor_id: impl Into<String>,
        p0_m: [f64; 3],
        u0: [f64; 3],
        u90: [f64; 3],
    ) -> Result<Self> {
        let sensor_id = sensor_id.into();
        if sensor_id.is_empty() {
            return Err(SceneStreamError::Invalid(
                "scene sensor_id must be non-empty",
            ));
        }
        if [p0_m, u0, u90]
            .iter()
            .flatten()
            .any(|component| !component.is_finite())
        {
            return Err(SceneStreamError::Invalid(
                "scene sensor vectors must contain finite numbers",
            ));
        }
        Ok(Self {
            sensor_id,
            p0_m,
            u0,
            u90,
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct StaticScene {
    boundary_xy_m: Vec<[f64; 2]>,
    floor_z_m: f64,
    top_z_m: f64,
    inlet_positions_xy_m: Vec<[f64; 2]>,
    sensors: Vec<SceneSensor>,
    surface_grid: SceneGrid,
}

#[derive(Clone, Debug, PartialEq)]
pub struct SceneGrid {
    cell_size_m: f64,
    x_coordinates_m: Vec<f64>,
    y_coordinates_m: Vec<f64>,
}

impl SceneGrid {
    pub fn from_surface(surface: &crate::scenario::SurfaceSnapshot) -> Result<Self> {
        let grid = Self {
            cell_size_m: surface.cell_size_m(),
            x_coordinates_m: surface.x_coordinates_m().to_vec(),
            y_coordinates_m: surface.y_coordinates_m().to_vec(),
        };
        validate_grid(&grid)?;
        Ok(grid)
    }
}

impl StaticScene {
    pub fn new(
        boundary_xy_m: Vec<[f64; 2]>,
        floor_z_m: f64,
        top_z_m: f64,
        inlet_positions_xy_m: Vec<[f64; 2]>,
        sensors: Vec<SceneSensor>,
        surface_grid: SceneGrid,
    ) -> Result<Self> {
        if boundary_xy_m.len() < 3
            || boundary_xy_m
                .iter()
                .flatten()
                .any(|component| !component.is_finite())
        {
            return Err(SceneStreamError::Invalid(
                "scene boundary must contain at least 3 finite coordinates",
            ));
        }
        if !floor_z_m.is_finite() || !top_z_m.is_finite() || top_z_m <= floor_z_m {
            return Err(SceneStreamError::Invalid(
                "scene top must be finite and above its finite floor",
            ));
        }
        if inlet_positions_xy_m.is_empty()
            || inlet_positions_xy_m
                .iter()
                .flatten()
                .any(|component| !component.is_finite())
        {
            return Err(SceneStreamError::Invalid(
                "static scene must contain at least 1 finite inlet",
            ));
        }
        if sensors.is_empty() {
            return Err(SceneStreamError::Invalid(
                "static scene must contain at least 1 sensor",
            ));
        }
        let sensor_ids = sensors
            .iter()
            .map(|sensor| sensor.sensor_id.as_str())
            .collect::<BTreeSet<_>>();
        if sensor_ids.len() != sensors.len() {
            return Err(SceneStreamError::Invalid(
                "scene sensor identifiers must be unique",
            ));
        }
        validate_grid(&surface_grid)?;
        Ok(Self {
            boundary_xy_m,
            floor_z_m,
            top_z_m,
            inlet_positions_xy_m,
            sensors,
            surface_grid,
        })
    }

    pub fn from_inputs(
        inputs: &SimulatorInputs,
        surface: &crate::scenario::SurfaceSnapshot,
    ) -> Result<Self> {
        let sensors = inputs
            .environment
            .sensors
            .iter()
            .map(|sensor| {
                SceneSensor::new(sensor.sensor_id.clone(), sensor.p0_m, sensor.u0, sensor.u90)
            })
            .collect::<Result<Vec<_>>>()?;
        Self::new(
            inputs.environment.boundary_xy_m.clone(),
            inputs.environment.floor_z_m,
            inputs.environment.top_z_m,
            inputs.simulator.scenario.inlet_positions_xy_m.clone(),
            sensors,
            SceneGrid::from_surface(surface)?,
        )
    }
}

#[derive(Clone, Debug)]
pub struct SceneStreamHeader {
    environment_id: String,
    run_id: String,
    input_fingerprint_sha256: String,
    seed: u64,
    scene: StaticScene,
}

impl SceneStreamHeader {
    pub fn new(
        environment_id: impl Into<String>,
        run_id: impl Into<String>,
        input_fingerprint_sha256: impl Into<String>,
        seed: u64,
        scene: StaticScene,
    ) -> Result<Self> {
        let environment_id = environment_id.into();
        let run_id = run_id.into();
        let input_fingerprint_sha256 = input_fingerprint_sha256.into();
        if environment_id.is_empty() {
            return Err(SceneStreamError::Invalid(
                "scene environment_id must be non-empty",
            ));
        }
        if run_id.is_empty() {
            return Err(SceneStreamError::Invalid("scene run_id must be non-empty"));
        }
        require_fingerprint(&input_fingerprint_sha256)?;
        Ok(Self {
            environment_id,
            run_id,
            input_fingerprint_sha256,
            seed,
            scene,
        })
    }
}

#[derive(Clone, Debug)]
pub struct SceneSegment {
    sequence: u64,
    run_id: String,
    left: ScenarioModelSnapshot,
    right: ScenarioModelSnapshot,
}

impl SceneSegment {
    pub fn new(
        sequence: u64,
        run_id: impl Into<String>,
        left: ScenarioModelSnapshot,
        right: ScenarioModelSnapshot,
    ) -> Result<Self> {
        if !(1..=MAX_SCENE_SEQUENCE).contains(&sequence) {
            return Err(SceneStreamError::Invalid(
                "scene sequence must be a positive JSON-safe integer",
            ));
        }
        let run_id = run_id.into();
        if run_id.is_empty() {
            return Err(SceneStreamError::Invalid("scene run_id must be non-empty"));
        }
        validate_scene_segment(&left, &right)?;
        Ok(Self {
            sequence,
            run_id,
            left,
            right,
        })
    }
}

#[derive(Serialize)]
struct HeaderDocument<'a> {
    scene_version: u32,
    #[serde(rename = "type")]
    record_type: &'static str,
    environment_id: &'a str,
    run_id: &'a str,
    input_fingerprint_sha256: &'a str,
    seed: u64,
    scene: SceneDocument<'a>,
}

#[derive(Serialize)]
struct SceneDocument<'a> {
    coordinate_system: &'static str,
    length_unit: &'static str,
    angle_unit: &'static str,
    boundary_xy_m: &'a [[f64; 2]],
    floor_z_m: f64,
    top_z_m: f64,
    inlet_positions_xy_m: &'a [[f64; 2]],
    sensors: Vec<SensorDocument<'a>>,
    surface: GridDocument<'a>,
}

#[derive(Serialize)]
struct GridDocument<'a> {
    cell_size_m: f64,
    x_coordinates_m: &'a [f64],
    y_coordinates_m: &'a [f64],
}

#[derive(Serialize)]
struct SensorDocument<'a> {
    sensor_id: &'a str,
    p0_m: [f64; 3],
    u0: [f64; 3],
    u90: [f64; 3],
}

#[derive(Serialize)]
struct RecordDocument<'a> {
    scene_version: u32,
    #[serde(rename = "type")]
    record_type: &'static str,
    sequence: u64,
    left_sequence: u64,
    right_sequence: u64,
    run_id: &'a str,
    left: KeyframeDocument<'a>,
    right: KeyframeDocument<'a>,
}

#[derive(Serialize)]
struct KeyframeDocument<'a> {
    scenario: ScenarioDocument,
    heights_m: HeightRows<'a>,
}

pub fn encode_scene_header_frame(header: &SceneStreamHeader) -> Result<Vec<u8>> {
    let scene = &header.scene;
    let document = HeaderDocument {
        scene_version: SCENE_VERSION,
        record_type: "scene_definition",
        environment_id: &header.environment_id,
        run_id: &header.run_id,
        input_fingerprint_sha256: &header.input_fingerprint_sha256,
        seed: header.seed,
        scene: SceneDocument {
            coordinate_system: "right-handed-z-up",
            length_unit: "m",
            angle_unit: "deg",
            boundary_xy_m: &scene.boundary_xy_m,
            floor_z_m: scene.floor_z_m,
            top_z_m: scene.top_z_m,
            inlet_positions_xy_m: &scene.inlet_positions_xy_m,
            sensors: scene
                .sensors
                .iter()
                .map(|sensor| SensorDocument {
                    sensor_id: &sensor.sensor_id,
                    p0_m: sensor.p0_m,
                    u0: sensor.u0,
                    u90: sensor.u90,
                })
                .collect(),
            surface: GridDocument {
                cell_size_m: scene.surface_grid.cell_size_m,
                x_coordinates_m: &scene.surface_grid.x_coordinates_m,
                y_coordinates_m: &scene.surface_grid.y_coordinates_m,
            },
        },
    };
    encode_frame(&document)
}

pub fn encode_scene_segment(record: &SceneSegment) -> Result<Vec<u8>> {
    validate_scene_segment(&record.left, &record.right)?;
    let document = RecordDocument {
        scene_version: SCENE_VERSION,
        record_type: "scene_segment",
        sequence: record.sequence,
        left_sequence: record.sequence.saturating_sub(1),
        right_sequence: record.sequence,
        run_id: &record.run_id,
        left: KeyframeDocument {
            scenario: ScenarioDocument::from(&record.left.state),
            heights_m: HeightRows::new(&record.left.surface),
        },
        right: KeyframeDocument {
            scenario: ScenarioDocument::from(&record.right.state),
            heights_m: HeightRows::new(&record.right.surface),
        },
    };
    encode_frame(&document)
}

fn validate_grid(grid: &SceneGrid) -> Result<()> {
    if !grid.cell_size_m.is_finite() || grid.cell_size_m <= 0.0 {
        return Err(SceneStreamError::Invalid(
            "scene surface grid cell size must be finite and positive",
        ));
    }
    if !strictly_increasing_finite(&grid.x_coordinates_m)
        || !strictly_increasing_finite(&grid.y_coordinates_m)
    {
        return Err(SceneStreamError::Invalid(
            "scene surface grid coordinates must be finite and strictly increasing",
        ));
    }
    Ok(())
}

fn validate_scene_segment(
    left: &ScenarioModelSnapshot,
    right: &ScenarioModelSnapshot,
) -> Result<()> {
    validate_model_snapshot(left).map_err(SceneStreamError::Invalid)?;
    validate_model_snapshot(right).map_err(SceneStreamError::Invalid)?;
    if left.state.elapsed_s >= right.state.elapsed_s {
        return Err(SceneStreamError::Invalid(
            "scene segment keyframes must be strictly ordered by elapsed time",
        ));
    }
    let left_grid = SceneGrid::from_surface(&left.surface)?;
    let right_grid = SceneGrid::from_surface(&right.surface)?;
    if left_grid != right_grid {
        return Err(SceneStreamError::Invalid(
            "scene segment keyframes must share one static surface grid",
        ));
    }
    Ok(())
}

fn strictly_increasing_finite(values: &[f64]) -> bool {
    values.len() >= 2
        && values.iter().all(|value| value.is_finite())
        && values.windows(2).all(|pair| pair[0] < pair[1])
}

fn encode_frame(document: &impl Serialize) -> Result<Vec<u8>> {
    let mut encoded = serde_json::to_vec(document)?;
    let actual = encoded.len().saturating_add(1);
    if actual > MAX_SCENE_LINE_BYTES {
        return Err(SceneStreamError::LineTooLarge {
            actual,
            maximum: MAX_SCENE_LINE_BYTES,
        });
    }
    encoded.push(b'\n');
    Ok(encoded)
}

#[derive(Clone, Debug)]
pub struct ScenePublisherConfig {
    host: String,
    port: u16,
    connect_timeout: Duration,
    send_timeout: Duration,
    reconnect_initial_delay_s: f64,
    reconnect_max_delay_s: f64,
}

impl ScenePublisherConfig {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        host: impl Into<String>,
        port: u16,
        connect_timeout_s: f64,
        send_timeout_s: f64,
        reconnect_initial_delay_s: f64,
        reconnect_max_delay_s: f64,
    ) -> Result<Self> {
        let host = host.into();
        if host.is_empty() {
            return Err(SceneStreamError::Invalid("scene host must be non-empty"));
        }
        if port == 0 {
            return Err(SceneStreamError::Invalid(
                "scene port must be from 1 through 65535",
            ));
        }
        let connect_timeout = positive_duration(connect_timeout_s, "connect timeout")?;
        let send_timeout = positive_duration(send_timeout_s, "send timeout")?;
        if !reconnect_initial_delay_s.is_finite() || reconnect_initial_delay_s <= 0.0 {
            return Err(SceneStreamError::Invalid(
                "scene reconnect initial delay must be finite and positive",
            ));
        }
        if !reconnect_max_delay_s.is_finite()
            || reconnect_max_delay_s <= 0.0
            || reconnect_initial_delay_s > reconnect_max_delay_s
        {
            return Err(SceneStreamError::Invalid(
                "scene reconnect maximum delay must be finite, positive, and not below the initial delay",
            ));
        }
        positive_duration(reconnect_initial_delay_s, "reconnect initial delay")?;
        positive_duration(reconnect_max_delay_s, "reconnect maximum delay")?;
        Ok(Self {
            host,
            port,
            connect_timeout,
            send_timeout,
            reconnect_initial_delay_s,
            reconnect_max_delay_s,
        })
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
pub struct ScenePublisherStats {
    pub accepted_records: u64,
    pub sent_records: u64,
    pub dropped_records: u64,
    pub connection_failures: u64,
}

#[derive(Default)]
struct PublisherCounters {
    accepted_records: AtomicU64,
    sent_records: AtomicU64,
    dropped_records: AtomicU64,
    connection_failures: AtomicU64,
}

impl PublisherCounters {
    fn snapshot(&self) -> ScenePublisherStats {
        ScenePublisherStats {
            accepted_records: self.accepted_records.load(Ordering::Relaxed),
            sent_records: self.sent_records.load(Ordering::Relaxed),
            dropped_records: self.dropped_records.load(Ordering::Relaxed),
            connection_failures: self.connection_failures.load(Ordering::Relaxed),
        }
    }
}

struct PublisherState {
    closed: bool,
    latest: Option<SceneSegment>,
    next_sequence: u64,
    previous_keyframe: ScenarioModelSnapshot,
}

struct SharedPublisher {
    state: Mutex<PublisherState>,
    counters: PublisherCounters,
    wakeup: Notify,
}

pub struct TcpScenePublisher {
    config: ScenePublisherConfig,
    run_id: String,
    seed: u64,
    header_frame: Arc<[u8]>,
    shared: Arc<SharedPublisher>,
    task: Option<JoinHandle<()>>,
}

impl TcpScenePublisher {
    pub fn new(
        config: ScenePublisherConfig,
        header: SceneStreamHeader,
        initial_keyframe: ScenarioModelSnapshot,
    ) -> Result<Self> {
        let header_frame = encode_scene_header_frame(&header)?;
        validate_model_snapshot(&initial_keyframe).map_err(SceneStreamError::Invalid)?;
        if initial_keyframe.state.elapsed_s.to_bits() != 0.0_f64.to_bits() {
            return Err(SceneStreamError::Invalid(
                "initial canonical keyframe must be at elapsed time zero",
            ));
        }
        Ok(Self {
            config,
            run_id: header.run_id,
            seed: header.seed,
            header_frame: header_frame.into(),
            shared: Arc::new(SharedPublisher {
                state: Mutex::new(PublisherState {
                    closed: false,
                    latest: None,
                    next_sequence: 1,
                    previous_keyframe: initial_keyframe,
                }),
                counters: PublisherCounters::default(),
                wakeup: Notify::new(),
            }),
            task: None,
        })
    }

    pub fn endpoint(&self) -> String {
        format!("{}:{}", self.config.host, self.config.port)
    }

    pub fn stats(&self) -> ScenePublisherStats {
        self.shared.counters.snapshot()
    }

    pub fn publish(&self, keyframe: ScenarioModelSnapshot) -> Result<bool> {
        let mut state = lock(&self.shared.state);
        if state.closed {
            return Ok(false);
        }
        if state.next_sequence > MAX_SCENE_SEQUENCE {
            return Err(SceneStreamError::SequenceExhausted);
        }
        let sequence = state.next_sequence;
        state.next_sequence += 1;
        let record = SceneSegment::new(
            sequence,
            self.run_id.clone(),
            state.previous_keyframe.clone(),
            keyframe.clone(),
        )?;
        state.previous_keyframe = keyframe;
        if state.latest.replace(record).is_some() {
            increment(&self.shared.counters.dropped_records);
        }
        increment(&self.shared.counters.accepted_records);
        drop(state);
        self.shared.wakeup.notify_one();
        Ok(true)
    }

    pub fn start(&mut self) -> Result<()> {
        if lock(&self.shared.state).closed {
            return Err(SceneStreamError::Closed);
        }
        if self.task.is_some() {
            return Ok(());
        }
        let runtime = tokio::runtime::Handle::try_current()
            .map_err(|_| SceneStreamError::RuntimeUnavailable)?;
        let config = self.config.clone();
        let header_frame = Arc::clone(&self.header_frame);
        let shared = Arc::clone(&self.shared);
        let mut backoff = ReconnectBackoff::new(
            config.reconnect_initial_delay_s,
            config.reconnect_max_delay_s,
            self.seed,
        )?;
        self.task = Some(runtime.spawn(async move {
            run_publisher(config, header_frame, shared, &mut backoff).await;
        }));
        Ok(())
    }

    pub async fn close(&mut self) {
        close_shared(&self.shared);
        if let Some(task) = self.task.take() {
            task.abort();
            let _ = task.await;
        }
        reconcile_accepted_records(&self.shared.counters);
    }
}

impl Drop for TcpScenePublisher {
    fn drop(&mut self) {
        close_shared(&self.shared);
        if let Some(task) = self.task.take() {
            task.abort();
        }
    }
}

async fn run_publisher(
    config: ScenePublisherConfig,
    header_frame: Arc<[u8]>,
    shared: Arc<SharedPublisher>,
    backoff: &mut ReconnectBackoff,
) {
    loop {
        if !wait_for_pending(&shared).await {
            return;
        }
        let result = use_next_connection(&config, &header_frame, &shared, backoff).await;
        if result.is_err() {
            increment(&shared.counters.connection_failures);
        }
        if lock(&shared.state).closed {
            return;
        }
        sleep(backoff.next_delay_after_failure()).await;
    }
}

async fn use_next_connection(
    config: &ScenePublisherConfig,
    header_frame: &[u8],
    shared: &SharedPublisher,
    backoff: &mut ReconnectBackoff,
) -> std::io::Result<()> {
    let mut stream = timeout(
        config.connect_timeout,
        TcpStream::connect((config.host.as_str(), config.port)),
    )
    .await
    .map_err(|_| timed_out("scene connect timed out"))??;
    write_frame(&mut stream, header_frame, config.send_timeout).await?;

    loop {
        if !wait_for_pending(shared).await {
            return Ok(());
        }
        let Some(record) = take_latest(shared) else {
            continue;
        };
        let frame = match encode_scene_segment(&record) {
            Ok(frame) => frame,
            Err(_) => {
                increment(&shared.counters.dropped_records);
                continue;
            }
        };
        if let Err(error) = write_frame(&mut stream, &frame, config.send_timeout).await {
            increment(&shared.counters.dropped_records);
            return Err(error);
        }
        increment(&shared.counters.sent_records);
        backoff.reset_after_success();
    }
}

async fn write_frame(
    stream: &mut TcpStream,
    frame: &[u8],
    deadline: Duration,
) -> std::io::Result<()> {
    timeout(deadline, stream.write_all(frame))
        .await
        .map_err(|_| timed_out("scene send timed out"))?
}

async fn wait_for_pending(shared: &SharedPublisher) -> bool {
    loop {
        let notified = shared.wakeup.notified();
        {
            let state = lock(&shared.state);
            if state.closed {
                return false;
            }
            if state.latest.is_some() {
                return true;
            }
        }
        notified.await;
    }
}

fn take_latest(shared: &SharedPublisher) -> Option<SceneSegment> {
    lock(&shared.state).latest.take()
}

fn close_shared(shared: &SharedPublisher) {
    let mut state = lock(&shared.state);
    if state.closed {
        return;
    }
    state.closed = true;
    if state.latest.take().is_some() {
        increment(&shared.counters.dropped_records);
    }
    drop(state);
    shared.wakeup.notify_one();
}

fn positive_duration(value: f64, _name: &'static str) -> Result<Duration> {
    if !value.is_finite() || value <= 0.0 {
        return Err(SceneStreamError::Invalid(
            "scene timeout must be finite and positive",
        ));
    }
    Duration::try_from_secs_f64(value).map_err(|_| {
        SceneStreamError::Invalid("scene timeout must fit the platform duration range")
    })
}

fn require_fingerprint(value: &str) -> Result<()> {
    if value.len() != 64
        || value
            .bytes()
            .any(|byte| !byte.is_ascii_digit() && !(b'a'..=b'f').contains(&byte))
    {
        return Err(SceneStreamError::Invalid(
            "scene input fingerprint must be lowercase SHA-256 hex",
        ));
    }
    Ok(())
}

fn increment(counter: &AtomicU64) {
    add(counter, 1);
}

fn add(counter: &AtomicU64, amount: u64) {
    let _ = counter.fetch_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
        Some(value.saturating_add(amount))
    });
}

fn reconcile_accepted_records(counters: &PublisherCounters) {
    let accepted = counters.accepted_records.load(Ordering::Relaxed);
    let accounted = counters
        .sent_records
        .load(Ordering::Relaxed)
        .saturating_add(counters.dropped_records.load(Ordering::Relaxed));
    add(
        &counters.dropped_records,
        accepted.saturating_sub(accounted),
    );
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn timed_out(message: &'static str) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::TimedOut, message)
}

struct ReconnectBackoff {
    initial_delay_s: f64,
    maximum_delay_s: f64,
    current_delay_cap_s: f64,
    consecutive_failures: u64,
    rng: ModelRng,
}

impl ReconnectBackoff {
    fn new(initial_delay_s: f64, maximum_delay_s: f64, seed: u64) -> Result<Self> {
        Ok(Self {
            initial_delay_s,
            maximum_delay_s,
            current_delay_cap_s: initial_delay_s,
            consecutive_failures: 0,
            rng: ModelRng::new(seed, StreamScope::Global, "scene-reconnect-jitter")?,
        })
    }

    fn next_delay_after_failure(&mut self) -> Duration {
        let delay_s = self.rng.unit_f64() * self.current_delay_cap_s;
        self.consecutive_failures = self.consecutive_failures.saturating_add(1);
        self.current_delay_cap_s = (self.current_delay_cap_s * 2.0).min(self.maximum_delay_s);
        Duration::from_secs_f64(delay_s)
    }

    fn reset_after_success(&mut self) {
        self.current_delay_cap_s = self.initial_delay_s;
        self.consecutive_failures = 0;
    }
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use crate::{configuration::load_simulator_inputs, scenario::build_scenario_simulator};

    use super::*;

    fn publisher() -> (TcpScenePublisher, crate::scenario::ScenarioSimulator) {
        let inputs = load_simulator_inputs(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("config/simulation-server.v1.json"),
        )
        .unwrap();
        let simulator = build_scenario_simulator(&inputs).unwrap();
        let initial = simulator.scene_snapshot().unwrap();
        let scene = StaticScene::from_inputs(&inputs, &initial.surface).unwrap();
        let header = SceneStreamHeader::new(
            inputs.environment.environment_id.clone(),
            "run-a",
            "0".repeat(64),
            inputs.simulator.seed,
            scene,
        )
        .unwrap();
        let config = ScenePublisherConfig::new("127.0.0.1", 17_000, 1.0, 1.0, 0.5, 5.0).unwrap();
        (
            TcpScenePublisher::new(config, header, initial).unwrap(),
            simulator,
        )
    }

    #[test]
    fn sequence_stops_at_the_json_safe_integer_limit() {
        let (publisher, mut simulator) = publisher();
        lock(&publisher.shared.state).next_sequence = MAX_SCENE_SEQUENCE;
        simulator.advance_to(0.1).unwrap();
        assert!(
            publisher
                .publish(simulator.scene_snapshot().unwrap())
                .unwrap()
        );
        assert!(matches!(
            publisher.publish(simulator.scene_snapshot().unwrap()),
            Err(SceneStreamError::SequenceExhausted)
        ));
    }

    #[test]
    fn full_jitter_is_deterministic_and_success_resets_only_the_cap() {
        let mut first = ReconnectBackoff::new(0.5, 2.0, 42).unwrap();
        let mut second = ReconnectBackoff::new(0.5, 2.0, 42).unwrap();
        for cap in [0.5, 1.0, 2.0, 2.0] {
            let first_delay = first.next_delay_after_failure().as_secs_f64();
            let second_delay = second.next_delay_after_failure().as_secs_f64();
            assert_eq!(first_delay, second_delay);
            assert!((0.0..cap).contains(&first_delay));
        }
        first.reset_after_success();
        assert_eq!(first.current_delay_cap_s, 0.5);
        assert_eq!(first.consecutive_failures, 0);
        assert!(first.next_delay_after_failure().as_secs_f64() < 0.5);
    }

    #[test]
    fn close_reconciliation_accounts_for_an_in_flight_record_once() {
        let counters = PublisherCounters::default();
        counters.accepted_records.store(3, Ordering::Relaxed);
        counters.sent_records.store(1, Ordering::Relaxed);

        reconcile_accepted_records(&counters);
        reconcile_accepted_records(&counters);

        assert_eq!(counters.snapshot().sent_records, 1);
        assert_eq!(counters.snapshot().dropped_records, 2);
    }

    #[test]
    fn segment_is_self_contained_and_preserves_the_static_grid_in_the_definition() {
        let (publisher, mut simulator) = publisher();
        let header: serde_json::Value = serde_json::from_slice(&publisher.header_frame).unwrap();
        assert_eq!(header["scene_version"], 2);
        assert_eq!(header["type"], "scene_definition");
        assert!(
            header["scene"]["surface"]["x_coordinates_m"]
                .as_array()
                .is_some_and(|coordinates| coordinates.len() >= 2)
        );

        simulator.advance_to(0.1).unwrap();
        publisher
            .publish(simulator.scene_snapshot().unwrap())
            .unwrap();
        let segment = lock(&publisher.shared.state).latest.clone().unwrap();
        let record: serde_json::Value =
            serde_json::from_slice(&encode_scene_segment(&segment).unwrap()).unwrap();
        assert_eq!(record["scene_version"], 2);
        assert_eq!(record["type"], "scene_segment");
        assert_eq!(record["left_sequence"], 0);
        assert_eq!(record["right_sequence"], 1);
        assert_eq!(record["left"]["scenario"]["elapsed_s"], 0.0);
        assert_eq!(record["right"]["scenario"]["elapsed_s"], 0.1);
        assert!(record["left"].get("surface").is_none());
        assert!(record["right"]["heights_m"].as_array().is_some());
    }

    #[test]
    fn publisher_rejects_non_monotonic_canonical_keyframes() {
        let (publisher, simulator) = publisher();
        assert!(matches!(
            publisher.publish(simulator.scene_snapshot().unwrap()),
            Err(SceneStreamError::Invalid(
                "scene segment keyframes must be strictly ordered by elapsed time"
            ))
        ));
    }
}
