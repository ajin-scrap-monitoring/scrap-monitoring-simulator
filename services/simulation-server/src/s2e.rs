//! RPLIDAR S2E-compatible UDP command and HQ scan adapters.

use std::{
    collections::{BTreeMap, BTreeSet},
    io,
    net::SocketAddr,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
};

use sha2::{Digest, Sha256};
use tokio::{net::UdpSocket, sync::watch, task::JoinHandle};

use crate::measurement::{HqSample, MeasurementResult};

const COMMAND_SYNC: u8 = 0xa5;
const ANSWER_SYNC_1: u8 = 0xa5;
const ANSWER_SYNC_2: u8 = 0x5a;
const LOOP_RESPONSE_FLAG: u32 = 1 << 30;

const COMMAND_STOP: u8 = 0x25;
const COMMAND_RESET: u8 = 0x40;
const COMMAND_GET_DEVICE_INFO: u8 = 0x50;
const COMMAND_GET_DEVICE_HEALTH: u8 = 0x52;
const COMMAND_EXPRESS_SCAN: u8 = 0x82;
const COMMAND_GET_LIDAR_CONF: u8 = 0x84;
const COMMAND_HQ_MOTOR_SPEED: u8 = 0xa8;

const ANSWER_DEVICE_INFO: u8 = 0x04;
const ANSWER_DEVICE_HEALTH: u8 = 0x06;
const ANSWER_GET_LIDAR_CONF: u8 = 0x20;
const ANSWER_MEASUREMENT_HQ: u8 = 0x83;

const CONF_DESIRED_ROTATION_FREQUENCY: u32 = 0x0000_0001;
const CONF_SCAN_MODE_COUNT: u32 = 0x0000_0070;
const CONF_SCAN_MODE_US_PER_SAMPLE: u32 = 0x0000_0071;
const CONF_SCAN_MODE_MAX_DISTANCE: u32 = 0x0000_0074;
const CONF_SCAN_MODE_ANSWER_TYPE: u32 = 0x0000_0075;
const CONF_SCAN_MODE_TYPICAL: u32 = 0x0000_007c;
const CONF_SCAN_MODE_NAME: u32 = 0x0000_007f;
const HQ_SCAN_MODE: u16 = 2;

const HQ_CAPSULE_SYNC: u8 = 0xa5;
const HQ_NODES_PER_CAPSULE: usize = 96;
const HQ_NODE_BYTES: usize = 8;
const HQ_CAPSULE_BYTES: usize = 1 + 8 + HQ_NODES_PER_CAPSULE * HQ_NODE_BYTES + 4;
const MAX_COMMAND_BYTES: usize = 1_024;

#[derive(Clone, Debug)]
pub struct EndpointConfig {
    pub sensor_id: String,
    pub bind: SocketAddr,
    pub sample_rate_hz: f64,
    pub rotation_rate_hz: f64,
    pub max_distance_m: f64,
}

impl EndpointConfig {
    pub fn validate(&self) -> Result<(), S2eError> {
        if self.sensor_id.is_empty() {
            return Err(S2eError::Invalid("sensor_id must not be empty"));
        }
        if !self.sample_rate_hz.is_finite() || self.sample_rate_hz <= 0.0 {
            return Err(S2eError::Invalid("sample rate must be finite and positive"));
        }
        if !self.rotation_rate_hz.is_finite() || self.rotation_rate_hz <= 0.0 {
            return Err(S2eError::Invalid(
                "rotation rate must be finite and positive",
            ));
        }
        if !self.max_distance_m.is_finite() || self.max_distance_m <= 0.0 {
            return Err(S2eError::Invalid(
                "maximum distance must be finite and positive",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum S2eError {
    #[error("invalid S2E configuration: {0}")]
    Invalid(&'static str),
    #[error("failed to bind S2E endpoint {bind}: {source}")]
    Bind {
        bind: SocketAddr,
        #[source]
        source: io::Error,
    },
    #[error("unknown S2E sensor: {0}")]
    UnknownSensor(String),
    #[error("S2E adapter task failed: {0}")]
    Task(#[from] tokio::task::JoinError),
    #[error("S2E adapter I/O failed: {0}")]
    Io(#[from] io::Error),
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize)]
pub struct EndpointStats {
    pub received_commands: u64,
    pub rejected_commands: u64,
    pub published_scans: u64,
    pub transmitted_capsules: u64,
    pub skipped_scans: u64,
}

#[derive(Default)]
struct EndpointCounters {
    received_commands: AtomicU64,
    rejected_commands: AtomicU64,
    published_scans: AtomicU64,
    transmitted_capsules: AtomicU64,
    skipped_scans: AtomicU64,
}

impl EndpointCounters {
    fn snapshot(&self) -> EndpointStats {
        EndpointStats {
            received_commands: self.received_commands.load(Ordering::Relaxed),
            rejected_commands: self.rejected_commands.load(Ordering::Relaxed),
            published_scans: self.published_scans.load(Ordering::Relaxed),
            transmitted_capsules: self.transmitted_capsules.load(Ordering::Relaxed),
            skipped_scans: self.skipped_scans.load(Ordering::Relaxed),
        }
    }
}

#[derive(Debug)]
struct PublishedScan {
    scan_id: u64,
    samples: Vec<HqSample>,
}

struct EndpointHandle {
    scans: watch::Sender<Option<Arc<PublishedScan>>>,
    counters: Arc<EndpointCounters>,
    task: JoinHandle<io::Result<()>>,
}

pub struct S2eRuntime {
    endpoints: BTreeMap<String, EndpointHandle>,
    shutdown: watch::Sender<bool>,
}

impl S2eRuntime {
    pub async fn bind(configs: Vec<EndpointConfig>) -> Result<Self, S2eError> {
        if configs.len() != 2 {
            return Err(S2eError::Invalid("exactly two endpoints are required"));
        }
        let sensor_ids = configs
            .iter()
            .map(|config| config.sensor_id.as_str())
            .collect::<BTreeSet<_>>();
        let bind_addresses = configs
            .iter()
            .map(|config| config.bind)
            .collect::<BTreeSet<_>>();
        if sensor_ids.len() != configs.len() || bind_addresses.len() != configs.len() {
            return Err(S2eError::Invalid(
                "sensor identifiers and bind addresses must be unique",
            ));
        }
        for config in &configs {
            config.validate()?;
        }

        let mut sockets = Vec::with_capacity(configs.len());
        for config in &configs {
            sockets.push(
                UdpSocket::bind(config.bind)
                    .await
                    .map_err(|source| S2eError::Bind {
                        bind: config.bind,
                        source,
                    })?,
            );
        }

        let (shutdown, _) = watch::channel(false);
        let mut endpoints = BTreeMap::new();
        for (config, socket) in configs.into_iter().zip(sockets) {
            let (scans, receiver) = watch::channel(None);
            let counters = Arc::new(EndpointCounters::default());
            let task = tokio::spawn(run_endpoint(
                socket,
                config.clone(),
                receiver,
                shutdown.subscribe(),
                Arc::clone(&counters),
            ));
            endpoints.insert(
                config.sensor_id,
                EndpointHandle {
                    scans,
                    counters,
                    task,
                },
            );
        }
        Ok(Self {
            endpoints,
            shutdown,
        })
    }

    pub fn publish(&self, result: MeasurementResult) -> Result<(), S2eError> {
        let sensor_id = result.sensor_id().to_owned();
        let scan_id = result.scan_id();
        let samples = result.into_measured().into_hq_samples();
        let endpoint = self
            .endpoints
            .get(&sensor_id)
            .ok_or_else(|| S2eError::UnknownSensor(sensor_id.clone()))?;
        endpoint
            .scans
            .send_replace(Some(Arc::new(PublishedScan { scan_id, samples })));
        endpoint
            .counters
            .published_scans
            .fetch_add(1, Ordering::Relaxed);
        Ok(())
    }

    pub fn stats(&self) -> BTreeMap<String, EndpointStats> {
        self.endpoints
            .iter()
            .map(|(sensor_id, endpoint)| (sensor_id.clone(), endpoint.counters.snapshot()))
            .collect()
    }

    pub async fn close(mut self) -> Result<(), S2eError> {
        self.shutdown.send_replace(true);
        let mut first_error = None;
        for (_, endpoint) in std::mem::take(&mut self.endpoints) {
            match endpoint.task.await {
                Ok(Ok(())) => {}
                Ok(Err(error)) if first_error.is_none() => {
                    first_error = Some(S2eError::Io(error));
                }
                Err(error) if first_error.is_none() => {
                    first_error = Some(S2eError::Task(error));
                }
                _ => {}
            }
        }
        first_error.map_or(Ok(()), Err)
    }
}

async fn run_endpoint(
    socket: UdpSocket,
    config: EndpointConfig,
    mut scans: watch::Receiver<Option<Arc<PublishedScan>>>,
    mut shutdown: watch::Receiver<bool>,
    counters: Arc<EndpointCounters>,
) -> io::Result<()> {
    let mut command_buffer = [0_u8; MAX_COMMAND_BYTES];
    let mut active_peer = None;
    let mut streaming = false;
    let mut last_observed_scan_id = None;
    let mut last_transmitted_scan_id = None;
    let mut encoder = HqEncoder::default();

    loop {
        tokio::select! {
            biased;
            changed = shutdown.changed() => {
                if changed.is_err() || *shutdown.borrow() {
                    return Ok(());
                }
            }
            received = socket.recv_from(&mut command_buffer) => {
                let (size, peer) = received?;
                counters.received_commands.fetch_add(1, Ordering::Relaxed);
                let Some(command) = parse_command(&command_buffer[..size]) else {
                    counters.rejected_commands.fetch_add(1, Ordering::Relaxed);
                    continue;
                };
                if active_peer != Some(peer) {
                    streaming = false;
                    last_transmitted_scan_id = None;
                    encoder.reset();
                }
                active_peer = Some(peer);
                match command.code {
                    COMMAND_STOP | COMMAND_RESET => {
                        streaming = false;
                        last_transmitted_scan_id = None;
                        encoder.reset();
                    }
                    COMMAND_GET_DEVICE_INFO if command.payload.is_empty() => {
                        streaming = false;
                        last_transmitted_scan_id = None;
                        encoder.reset();
                        socket.send_to(&device_info_response(&config.sensor_id), peer).await?;
                    }
                    COMMAND_GET_DEVICE_HEALTH if command.payload.is_empty() => {
                        streaming = false;
                        last_transmitted_scan_id = None;
                        encoder.reset();
                        socket.send_to(&response(ANSWER_DEVICE_HEALTH, &[0, 0, 0], false), peer).await?;
                    }
                    COMMAND_GET_LIDAR_CONF => {
                        streaming = false;
                        last_transmitted_scan_id = None;
                        encoder.reset();
                        if let Some(payload) = lidar_configuration_response(&config, command.payload) {
                            socket.send_to(&response(ANSWER_GET_LIDAR_CONF, &payload, false), peer).await?;
                        } else {
                            counters.rejected_commands.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                    COMMAND_HQ_MOTOR_SPEED if command.payload.len() == 2 => {}
                    COMMAND_EXPRESS_SCAN if valid_scan_request(command.payload) => {
                        if !streaming {
                            encoder.reset();
                            last_transmitted_scan_id = None;
                        }
                        streaming = true;
                        socket.send_to(&response(ANSWER_MEASUREMENT_HQ, &[], true), peer).await?;
                        let latest = scans.borrow_and_update().clone();
                        if let Some(scan) = latest
                            && last_transmitted_scan_id != Some(scan.scan_id)
                        {
                            transmit_scan(&socket, peer, &scan, &counters, &mut encoder).await?;
                            last_transmitted_scan_id = Some(scan.scan_id);
                        }
                    }
                    _ => {
                        counters.rejected_commands.fetch_add(1, Ordering::Relaxed);
                    }
                }
            }
            changed = scans.changed() => {
                if changed.is_err() {
                    return Ok(());
                }
                let latest = scans.borrow_and_update().clone();
                let Some(scan) = latest else {
                    continue;
                };
                if let Some(previous) = last_observed_scan_id
                    && scan.scan_id > previous + 1
                {
                    counters.skipped_scans.fetch_add(scan.scan_id - previous - 1, Ordering::Relaxed);
                }
                last_observed_scan_id = Some(scan.scan_id);
                if streaming
                    && let Some(peer) = active_peer
                    && last_transmitted_scan_id != Some(scan.scan_id)
                {
                    transmit_scan(&socket, peer, &scan, &counters, &mut encoder).await?;
                    last_transmitted_scan_id = Some(scan.scan_id);
                }
            }
        }
    }
}

struct Command<'a> {
    code: u8,
    payload: &'a [u8],
}

fn parse_command(frame: &[u8]) -> Option<Command<'_>> {
    if frame.len() < 2 || frame[0] != COMMAND_SYNC {
        return None;
    }
    let code = frame[1];
    if code & 0x80 == 0 {
        return (frame.len() == 2).then_some(Command { code, payload: &[] });
    }
    let size = usize::from(*frame.get(2)?);
    if frame.len() != size.checked_add(4)? {
        return None;
    }
    let checksum = frame[..frame.len() - 1]
        .iter()
        .copied()
        .fold(0_u8, |left, right| left ^ right);
    (checksum == frame[frame.len() - 1]).then_some(Command {
        code,
        payload: &frame[3..3 + size],
    })
}

fn response(answer_type: u8, payload: &[u8], loop_mode: bool) -> Vec<u8> {
    let payload_size = if loop_mode {
        HQ_CAPSULE_BYTES as u32
    } else {
        payload.len() as u32
    };
    let size_and_flags = payload_size | if loop_mode { LOOP_RESPONSE_FLAG } else { 0 };
    let mut frame = Vec::with_capacity(7 + payload.len());
    frame.extend_from_slice(&[ANSWER_SYNC_1, ANSWER_SYNC_2]);
    frame.extend_from_slice(&size_and_flags.to_le_bytes());
    frame.push(answer_type);
    frame.extend_from_slice(payload);
    frame
}

fn device_info_response(sensor_id: &str) -> Vec<u8> {
    let digest = Sha256::digest(sensor_id.as_bytes());
    let mut payload = Vec::with_capacity(20);
    payload.push(0x00);
    payload.extend_from_slice(&0x0118_u16.to_le_bytes());
    payload.push(1);
    payload.extend_from_slice(&digest[..16]);
    response(ANSWER_DEVICE_INFO, &payload, false)
}

fn lidar_configuration_response(config: &EndpointConfig, payload: &[u8]) -> Option<Vec<u8>> {
    let query_type = u32::from_le_bytes(payload.get(..4)?.try_into().ok()?);
    let mut answer = Vec::new();
    answer.extend_from_slice(&query_type.to_le_bytes());
    match query_type {
        CONF_SCAN_MODE_TYPICAL => answer.extend_from_slice(&HQ_SCAN_MODE.to_le_bytes()),
        CONF_SCAN_MODE_COUNT => answer.extend_from_slice(&1_u16.to_le_bytes()),
        CONF_SCAN_MODE_US_PER_SAMPLE => {
            require_hq_mode(payload)?;
            let q8 = ((1_000_000.0 / config.sample_rate_hz) * 256.0).round();
            answer.extend_from_slice(&(q8 as u32).to_le_bytes());
        }
        CONF_SCAN_MODE_MAX_DISTANCE => {
            require_hq_mode(payload)?;
            let q8 = (config.max_distance_m * 256.0).round();
            answer.extend_from_slice(&(q8 as u32).to_le_bytes());
        }
        CONF_SCAN_MODE_ANSWER_TYPE => {
            require_hq_mode(payload)?;
            answer.push(ANSWER_MEASUREMENT_HQ);
        }
        CONF_SCAN_MODE_NAME => {
            require_hq_mode(payload)?;
            answer.extend_from_slice(b"HQ");
        }
        CONF_DESIRED_ROTATION_FREQUENCY => {
            let rpm = (config.rotation_rate_hz * 60.0).round() as u16;
            answer.extend_from_slice(&rpm.to_le_bytes());
            answer.extend_from_slice(&0_u16.to_le_bytes());
        }
        _ => return None,
    }
    Some(answer)
}

fn require_hq_mode(payload: &[u8]) -> Option<()> {
    let mode = u16::from_le_bytes(payload.get(4..6)?.try_into().ok()?);
    (mode == HQ_SCAN_MODE).then_some(())
}

fn valid_scan_request(payload: &[u8]) -> bool {
    payload.len() == 5 && payload[0] == HQ_SCAN_MODE as u8
}

async fn transmit_scan(
    socket: &UdpSocket,
    peer: SocketAddr,
    scan: &PublishedScan,
    counters: &EndpointCounters,
    encoder: &mut HqEncoder,
) -> io::Result<()> {
    for capsule in encoder.encode(scan) {
        socket.send_to(&capsule, peer).await?;
        counters
            .transmitted_capsules
            .fetch_add(1, Ordering::Relaxed);
    }
    Ok(())
}

/// HQ capsules contain exactly 96 real nodes; a rotation boundary may occur inside one.
/// An incomplete tail (at most 95 nodes) waits for the next rotation, never zero padding.
struct HqEncoder {
    capsule: [u8; HQ_CAPSULE_BYTES],
    node_count: usize,
    last_scan_id: Option<u64>,
}

impl Default for HqEncoder {
    fn default() -> Self {
        Self {
            capsule: [0; HQ_CAPSULE_BYTES],
            node_count: 0,
            last_scan_id: None,
        }
    }
}

impl HqEncoder {
    fn reset(&mut self) {
        self.node_count = 0;
        self.last_scan_id = None;
    }

    fn encode(&mut self, scan: &PublishedScan) -> Vec<[u8; HQ_CAPSULE_BYTES]> {
        if self
            .last_scan_id
            .is_some_and(|previous| previous.checked_add(1) != Some(scan.scan_id))
        {
            // A latest-only producer may skip a rotation; do not join its old partial tail.
            self.node_count = 0;
        }
        self.last_scan_id = Some(scan.scan_id);
        let mut capsules =
            Vec::with_capacity((self.node_count + scan.samples.len()) / HQ_NODES_PER_CAPSULE);
        for (sample_index, sample) in scan.samples.iter().enumerate() {
            if self.node_count == 0 {
                self.capsule[0] = HQ_CAPSULE_SYNC;
                self.capsule[1..9].copy_from_slice(&scan.scan_id.to_le_bytes());
            }
            let offset = 9 + self.node_count * HQ_NODE_BYTES;
            self.capsule[offset..offset + 2].copy_from_slice(&sample.angle_z_q14.to_le_bytes());
            let distance = u32::try_from(sample.dist_mm_q2)
                .expect("validated measurement distance must fit the S2E HQ field");
            self.capsule[offset + 2..offset + 6].copy_from_slice(&distance.to_le_bytes());
            self.capsule[offset + 6] = sample.quality;
            self.capsule[offset + 7] = u8::from(sample_index == 0);
            self.node_count += 1;
            if self.node_count == HQ_NODES_PER_CAPSULE {
                let crc_offset = HQ_CAPSULE_BYTES - 4;
                let crc = padded_crc32(&self.capsule[..crc_offset]);
                self.capsule[crc_offset..].copy_from_slice(&crc.to_le_bytes());
                capsules.push(self.capsule);
                self.node_count = 0;
            }
        }
        capsules
    }
}

fn padded_crc32(bytes: &[u8]) -> u32 {
    let padding = 4 - (bytes.len() & 3);
    let mut crc = 0xffff_ffff_u32;
    for byte in bytes.iter().copied().chain(std::iter::repeat_n(0, padding)) {
        crc ^= u32::from(byte);
        for _ in 0..8 {
            crc = (crc >> 1) ^ (0xedb8_8320_u32 & 0_u32.wrapping_sub(crc & 1));
        }
    }
    !crc
}

#[cfg(test)]
mod tests {
    use super::*;

    fn payload_command(code: u8, payload: &[u8]) -> Vec<u8> {
        let mut command = vec![COMMAND_SYNC, code, payload.len() as u8];
        command.extend_from_slice(payload);
        let checksum = command
            .iter()
            .copied()
            .fold(0_u8, |left, right| left ^ right);
        command.push(checksum);
        command
    }

    #[test]
    fn command_parser_checks_shape_and_checksum() {
        let command = payload_command(COMMAND_EXPRESS_SCAN, &[2, 0, 0, 0, 0]);
        let parsed = parse_command(&command).expect("valid command");
        assert_eq!(parsed.code, COMMAND_EXPRESS_SCAN);
        assert_eq!(parsed.payload, [2, 0, 0, 0, 0]);

        let mut invalid = command;
        *invalid.last_mut().expect("checksum") ^= 1;
        assert!(parse_command(&invalid).is_none());
        assert!(parse_command(&[COMMAND_SYNC, COMMAND_STOP]).is_some());
    }

    #[test]
    fn hq_capsules_have_fixed_shape_sync_flags_and_crc() {
        let scan = PublishedScan {
            scan_id: 7,
            samples: (0..97)
                .map(|index| HqSample {
                    angle_z_q14: index,
                    dist_mm_q2: u64::from(index) * 4,
                    quality: 40,
                })
                .collect(),
        };
        let mut encoder = HqEncoder::default();
        let mut capsules = encoder.encode(&scan);
        assert_eq!(capsules.len(), 1);
        let following = PublishedScan {
            scan_id: 8,
            samples: scan.samples[..95].to_vec(),
        };
        capsules.extend(encoder.encode(&following));
        assert_eq!(capsules.len(), 2);
        assert_eq!(capsules[0][0], HQ_CAPSULE_SYNC);
        assert_eq!(capsules[0][16], 1);
        assert_eq!(capsules[1][16], 0);
        assert_eq!(capsules[1][24], 1);
        for capsule in capsules {
            let crc_offset = HQ_CAPSULE_BYTES - 4;
            let expected = u32::from_le_bytes(capsule[crc_offset..].try_into().unwrap());
            assert_eq!(padded_crc32(&capsule[..crc_offset]), expected);
        }
    }

    #[test]
    fn loop_header_declares_one_hq_capsule() {
        let header = response(ANSWER_MEASUREMENT_HQ, &[], true);
        assert_eq!(header.len(), 7);
        assert_eq!(&header[..2], &[ANSWER_SYNC_1, ANSWER_SYNC_2]);
        assert_eq!(
            u32::from_le_bytes(header[2..6].try_into().unwrap()),
            LOOP_RESPONSE_FLAG | HQ_CAPSULE_BYTES as u32
        );
        assert_eq!(header[6], ANSWER_MEASUREMENT_HQ);
    }

    #[test]
    fn three_full_rotations_preserve_exact_samples_without_padding() {
        let mut encoder = HqEncoder::default();
        let mut encoded = Vec::new();
        let mut expected = Vec::new();
        for scan_id in 1..=3 {
            let samples: Vec<_> = (0..3200)
                .map(|index| HqSample {
                    angle_z_q14: index as u16,
                    // An actual zero-distance/zero-quality sample must survive unchanged.
                    dist_mm_q2: if index == 0 {
                        0
                    } else {
                        scan_id * 10000 + index
                    },
                    quality: if index == 0 { 0 } else { 40 },
                })
                .collect();
            expected.extend(samples.iter().copied());
            encoded.extend(encoder.encode(&PublishedScan { scan_id, samples }));
        }
        let nodes: Vec<_> = encoded
            .iter()
            .flat_map(|capsule| {
                (0..HQ_NODES_PER_CAPSULE).map(move |i| {
                    let offset = 9 + i * HQ_NODE_BYTES;
                    (
                        u16::from_le_bytes(capsule[offset..offset + 2].try_into().unwrap()),
                        u32::from_le_bytes(capsule[offset + 2..offset + 6].try_into().unwrap()),
                        capsule[offset + 6],
                        capsule[offset + 7],
                    )
                })
            })
            .collect();
        assert_eq!(
            nodes.len(),
            expected.len(),
            "packet padding must not become scan data"
        );
        for (i, (actual, sample)) in nodes.iter().zip(&expected).enumerate() {
            assert_eq!(
                *actual,
                (
                    sample.angle_z_q14,
                    sample.dist_mm_q2 as u32,
                    sample.quality,
                    u8::from(i % 3200 == 0)
                )
            );
        }
    }

    #[test]
    fn partial_capsule_is_bounded_and_discarded_on_reset_or_scan_gap() {
        let scan = |scan_id, length| PublishedScan {
            scan_id,
            samples: (0..length)
                .map(|i| HqSample {
                    angle_z_q14: i,
                    dist_mm_q2: u64::from(i) + scan_id * 1000,
                    quality: 0,
                })
                .collect(),
        };
        let mut encoder = HqEncoder::default();
        assert!(encoder.encode(&scan(1, 95)).is_empty());
        assert_eq!(encoder.node_count, 95);
        encoder.reset();
        let after_reset = encoder.encode(&scan(2, 96));
        assert_eq!(after_reset.len(), 1);
        assert_eq!(
            u32::from_le_bytes(after_reset[0][11..15].try_into().unwrap()),
            2000
        );
        assert_eq!(after_reset[0][16], 1);
        assert!(encoder.encode(&scan(3, 95)).is_empty());
        let after_gap = encoder.encode(&scan(5, 96));
        assert_eq!(after_gap.len(), 1);
        assert_eq!(
            u32::from_le_bytes(after_gap[0][11..15].try_into().unwrap()),
            5000
        );
        assert_eq!(after_gap[0][16], 1);
        assert_eq!(encoder.node_count, 0);
    }

    #[tokio::test]
    async fn udp_stop_restart_and_new_peer_start_with_a_fresh_capsule() {
        use std::time::Duration;

        let server = UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let address = server.local_addr().unwrap();
        let scan = Arc::new(PublishedScan {
            scan_id: 7,
            samples: (0..97)
                .map(|i| HqSample {
                    angle_z_q14: i,
                    dist_mm_q2: u64::from(i) * 4,
                    quality: 40,
                })
                .collect(),
        });
        let (_publisher, scans) = watch::channel(Some(scan));
        let (shutdown, stop) = watch::channel(false);
        let task = tokio::spawn(run_endpoint(
            server,
            EndpointConfig {
                sensor_id: "test-sensor".into(),
                bind: address,
                sample_rate_hz: 32000.0,
                rotation_rate_hz: 10.0,
                max_distance_m: 30.0,
            },
            scans,
            stop,
            Arc::new(EndpointCounters::default()),
        ));
        let first = UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let second = UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let start = payload_command(COMMAND_EXPRESS_SCAN, &[2, 0, 0, 0, 0]);
        for (index, peer) in [&first, &first, &second].iter().enumerate() {
            if index == 1 {
                peer.send_to(&[COMMAND_SYNC, COMMAND_STOP], address)
                    .await
                    .unwrap();
            }
            peer.send_to(&start, address).await.unwrap();
            let mut bytes = [0u8; HQ_CAPSULE_BYTES];
            let (size, _) =
                tokio::time::timeout(Duration::from_secs(3), peer.recv_from(&mut bytes))
                    .await
                    .unwrap()
                    .unwrap();
            assert_eq!(size, 7);
            let (size, _) =
                tokio::time::timeout(Duration::from_secs(3), peer.recv_from(&mut bytes))
                    .await
                    .unwrap()
                    .unwrap();
            assert_eq!(size, HQ_CAPSULE_BYTES);
            assert_eq!(bytes[16], 1);
            assert_eq!(u16::from_le_bytes(bytes[9..11].try_into().unwrap()), 0);
            assert_eq!(u16::from_le_bytes(bytes[17..19].try_into().unwrap()), 1);
        }
        shutdown.send_replace(true);
        task.await.unwrap().unwrap();
    }
}
