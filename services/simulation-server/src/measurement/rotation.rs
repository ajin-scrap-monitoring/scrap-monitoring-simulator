//! Independent sensor rotation and point timing schedules.

use sha2::{Digest, Sha256};

use crate::decimal_ratio::PositiveRatio;

use super::sdk::{SdkCompatibilityError, quantize_hq_angle_deg};

const MAX_SCAN_ID: u64 = i64::MAX as u64;
const MAX_SAMPLE_INDEX: u64 = i64::MAX as u64;
const TIME_TOLERANCE: f64 = 1e-12;
pub const MAX_SCAN_POINTS_PER_FRAME: u64 = 32_768;

#[derive(Clone, Debug, PartialEq, thiserror::Error)]
pub enum RotationError {
    #[error("rotation scheduler sensor_id must be non-empty")]
    EmptySensorId,
    #[error("sample rate must be a finite positive number")]
    InvalidSampleRate,
    #[error("rotation rate must be a finite positive number")]
    InvalidRotationRate,
    #[error("sample rate must be at least the rotation rate")]
    SampleRateBelowRotationRate,
    #[error("one rotation must contain at most 32768 measurement points")]
    ScanPointLimitExceeded,
    #[error("initial angle must be finite and in [0, 360)")]
    InvalidInitialAngle,
    #[error("scheduled scan scan_id must be a positive signed 64-bit integer")]
    InvalidScanId,
    #[error("rotation completion must be finite and after its finite non-negative start")]
    InvalidRotationInterval,
    #[error("scheduled scan angle and time arrays must have the same shape")]
    ShapeMismatch,
    #[error("scheduled scan must contain at least one point")]
    EmptyScan,
    #[error("scheduled scan angles must be finite and in [0, 360)")]
    InvalidScheduledAngle,
    #[error("scheduled scan point times must be finite")]
    InvalidPointTime,
    #[error("scheduled scan point times must be strictly increasing")]
    NonMonotonicPointTime,
    #[error("scheduled scan points must not precede the rotation start")]
    PointBeforeRotation,
    #[error("scheduled scan points must precede rotation completion")]
    PointAtOrAfterCompletion,
    #[error("sensor scan_id range is exhausted")]
    ScanIdExhausted,
    #[error("sensor sample index exceeds the supported signed 64-bit range")]
    SampleIndexExhausted,
    #[error("rotation schedule produced no measurement points")]
    NoMeasurementPoints,
    #[error("rotation schedule allocation failed")]
    AllocationFailed,
    #[error("rotation boundary is outside the finite f64 time range")]
    RotationTimeOutOfRange,
    #[error(transparent)]
    Sdk(#[from] SdkCompatibilityError),
}

/// Read-only angle and simulation-time values for one completed rotation.
#[derive(Clone, Debug, PartialEq)]
pub struct ScheduledScan {
    sensor_id: String,
    scan_id: u64,
    rotation_started_at_s: f64,
    completed_at_s: f64,
    angles_deg: Box<[f64]>,
    point_elapsed_times_s: Box<[f64]>,
}

impl ScheduledScan {
    pub fn new(
        sensor_id: impl Into<String>,
        scan_id: u64,
        rotation_started_at_s: f64,
        completed_at_s: f64,
        angles_deg: Vec<f64>,
        point_elapsed_times_s: Vec<f64>,
    ) -> Result<Self, RotationError> {
        let sensor_id = sensor_id.into();
        if sensor_id.is_empty() {
            return Err(RotationError::EmptySensorId);
        }
        if !(1..=MAX_SCAN_ID).contains(&scan_id) {
            return Err(RotationError::InvalidScanId);
        }
        if !rotation_started_at_s.is_finite()
            || rotation_started_at_s < 0.0
            || !completed_at_s.is_finite()
            || completed_at_s <= rotation_started_at_s
        {
            return Err(RotationError::InvalidRotationInterval);
        }
        if angles_deg.len() != point_elapsed_times_s.len() {
            return Err(RotationError::ShapeMismatch);
        }
        if angles_deg.is_empty() {
            return Err(RotationError::EmptyScan);
        }
        if angles_deg
            .iter()
            .any(|angle| !angle.is_finite() || !(0.0..360.0).contains(angle))
        {
            return Err(RotationError::InvalidScheduledAngle);
        }
        if point_elapsed_times_s.iter().any(|time| !time.is_finite()) {
            return Err(RotationError::InvalidPointTime);
        }
        if point_elapsed_times_s
            .windows(2)
            .any(|times| times[1] <= times[0])
        {
            return Err(RotationError::NonMonotonicPointTime);
        }
        let tolerance_s = completed_at_s.max(1.0) * TIME_TOLERANCE;
        if point_elapsed_times_s[0] < rotation_started_at_s - tolerance_s {
            return Err(RotationError::PointBeforeRotation);
        }
        if point_elapsed_times_s[point_elapsed_times_s.len() - 1] >= completed_at_s {
            return Err(RotationError::PointAtOrAfterCompletion);
        }

        Ok(Self {
            sensor_id,
            scan_id,
            rotation_started_at_s,
            completed_at_s,
            angles_deg: angles_deg.into_boxed_slice(),
            point_elapsed_times_s: point_elapsed_times_s.into_boxed_slice(),
        })
    }

    pub fn sensor_id(&self) -> &str {
        &self.sensor_id
    }

    pub fn scan_id(&self) -> u64 {
        self.scan_id
    }

    pub fn rotation_started_at_s(&self) -> f64 {
        self.rotation_started_at_s
    }

    pub fn completed_at_s(&self) -> f64 {
        self.completed_at_s
    }

    pub fn captured_elapsed_s(&self) -> f64 {
        self.point_elapsed_times_s[0]
    }

    pub fn angles_deg(&self) -> &[f64] {
        &self.angles_deg
    }

    pub fn point_elapsed_times_s(&self) -> &[f64] {
        &self.point_elapsed_times_s
    }

    pub fn point_count(&self) -> usize {
        self.angles_deg.len()
    }
}

/// Generates completed rotation schedules for one sensor independently.
#[derive(Clone, Debug)]
pub struct SensorRotationScheduler {
    sensor_id: String,
    sample_rate_hz: f64,
    rotation_rate_hz: f64,
    samples_per_rotation: PositiveRatio,
    rotation_rate_fraction: PositiveRatio,
    initial_angle_deg: f64,
    next_rotation_index: u64,
    next_sample_index: u64,
}

impl SensorRotationScheduler {
    pub fn new(
        sensor_id: impl Into<String>,
        sample_rate_hz: f64,
        rotation_rate_hz: f64,
        initial_angle_deg: f64,
    ) -> Result<Self, RotationError> {
        let sensor_id = sensor_id.into();
        if sensor_id.is_empty() {
            return Err(RotationError::EmptySensorId);
        }
        let (samples_per_rotation, rotation_rate_fraction) =
            validated_rate_ratios(sample_rate_hz, rotation_rate_hz)?;
        if !initial_angle_deg.is_finite() || !(0.0..360.0).contains(&initial_angle_deg) {
            return Err(RotationError::InvalidInitialAngle);
        }

        Ok(Self {
            sensor_id,
            sample_rate_hz,
            rotation_rate_hz,
            samples_per_rotation,
            rotation_rate_fraction,
            initial_angle_deg: quantize_hq_angle_deg(initial_angle_deg)?,
            next_rotation_index: 0,
            next_sample_index: 0,
        })
    }

    pub fn seeded(
        sensor_id: impl Into<String>,
        sample_rate_hz: f64,
        rotation_rate_hz: f64,
        seed: u64,
    ) -> Result<Self, RotationError> {
        let sensor_id = sensor_id.into();
        if sensor_id.is_empty() {
            return Err(RotationError::EmptySensorId);
        }

        let mut payload = Vec::with_capacity(b"sensor-rotation\0".len() + 8 + sensor_id.len());
        payload.extend_from_slice(b"sensor-rotation\0");
        payload.extend_from_slice(&seed.to_be_bytes());
        payload.extend_from_slice(sensor_id.as_bytes());
        let digest: [u8; 32] = Sha256::digest(payload).into();
        let digest_value = unsigned_256_to_f64(&digest);
        let initial_angle_deg = digest_value * 360.0 / 2.0_f64.powi(256);

        Self::new(
            sensor_id,
            sample_rate_hz,
            rotation_rate_hz,
            initial_angle_deg,
        )
    }

    pub fn sensor_id(&self) -> &str {
        &self.sensor_id
    }

    pub fn initial_angle_deg(&self) -> f64 {
        self.initial_angle_deg
    }

    pub fn next_scan_id(&self) -> u64 {
        self.next_rotation_index.saturating_add(1)
    }

    pub fn next_completion_elapsed_s(&self) -> Result<f64, RotationError> {
        self.rotation_boundary_elapsed_s(self.next_scan_id())
    }

    pub fn next_scan(&mut self) -> Result<ScheduledScan, RotationError> {
        let scan_id = self.next_scan_id();
        if scan_id > MAX_SCAN_ID {
            return Err(RotationError::ScanIdExhausted);
        }

        let end_sample_index = self
            .samples_per_rotation
            .ceil_multiple(scan_id)
            .ok_or(RotationError::SampleIndexExhausted)?;
        if end_sample_index > MAX_SAMPLE_INDEX {
            return Err(RotationError::SampleIndexExhausted);
        }
        if end_sample_index <= self.next_sample_index {
            return Err(RotationError::NoMeasurementPoints);
        }

        let point_count = usize::try_from(end_sample_index - self.next_sample_index)
            .map_err(|_| RotationError::AllocationFailed)?;
        let mut angles_deg = Vec::new();
        let mut point_elapsed_times_s = Vec::new();
        angles_deg
            .try_reserve_exact(point_count)
            .map_err(|_| RotationError::AllocationFailed)?;
        point_elapsed_times_s
            .try_reserve_exact(point_count)
            .map_err(|_| RotationError::AllocationFailed)?;

        let rotation_index = self.next_rotation_index;
        for sample_index in self.next_sample_index..end_sample_index {
            let point_elapsed_s = sample_index as f64 / self.sample_rate_hz;
            let rotation_progress = point_elapsed_s * self.rotation_rate_hz - rotation_index as f64;
            let unquantized_angle =
                (self.initial_angle_deg + 360.0 * rotation_progress).rem_euclid(360.0);
            point_elapsed_times_s.push(point_elapsed_s);
            angles_deg.push(quantize_hq_angle_deg(unquantized_angle)?);
        }

        let rotation_started_at_s = self.rotation_boundary_elapsed_s(rotation_index)?;
        let completed_at_s = self.rotation_boundary_elapsed_s(scan_id)?;
        let result = ScheduledScan::new(
            self.sensor_id.clone(),
            scan_id,
            rotation_started_at_s,
            completed_at_s,
            angles_deg,
            point_elapsed_times_s,
        )?;
        self.next_rotation_index += 1;
        self.next_sample_index = end_sample_index;
        Ok(result)
    }

    fn rotation_boundary_elapsed_s(&self, rotation_index: u64) -> Result<f64, RotationError> {
        self.rotation_rate_fraction
            .reciprocal_multiple_to_f64(rotation_index)
            .ok_or(RotationError::RotationTimeOutOfRange)
    }

    #[cfg(test)]
    #[allow(dead_code)]
    pub(crate) fn set_state_for_test(&mut self, next_rotation_index: u64, next_sample_index: u64) {
        self.next_rotation_index = next_rotation_index;
        self.next_sample_index = next_sample_index;
    }
}

pub fn create_seeded_rotation_scheduler(
    sensor_id: impl Into<String>,
    sample_rate_hz: f64,
    rotation_rate_hz: f64,
    seed: u64,
) -> Result<SensorRotationScheduler, RotationError> {
    SensorRotationScheduler::seeded(sensor_id, sample_rate_hz, rotation_rate_hz, seed)
}

pub fn validate_scan_point_limit(
    sample_rate_hz: f64,
    rotation_rate_hz: f64,
) -> Result<(), RotationError> {
    validated_rate_ratios(sample_rate_hz, rotation_rate_hz).map(|_| ())
}

fn validated_rate_ratios(
    sample_rate_hz: f64,
    rotation_rate_hz: f64,
) -> Result<(PositiveRatio, PositiveRatio), RotationError> {
    if !sample_rate_hz.is_finite() || sample_rate_hz <= 0.0 {
        return Err(RotationError::InvalidSampleRate);
    }
    if !rotation_rate_hz.is_finite() || rotation_rate_hz <= 0.0 {
        return Err(RotationError::InvalidRotationRate);
    }
    if sample_rate_hz < rotation_rate_hz {
        return Err(RotationError::SampleRateBelowRotationRate);
    }
    let samples_per_rotation = PositiveRatio::from_decimal_f64s(sample_rate_hz, rotation_rate_hz)
        .expect("finite positive f64 decimal ratios are representable");
    let rotation_rate_fraction = PositiveRatio::from_decimal_f64s(rotation_rate_hz, 1.0)
        .expect("finite positive f64 decimals are representable");
    let points = samples_per_rotation
        .ceil_multiple(1)
        .ok_or(RotationError::ScanPointLimitExceeded)?;
    if points > MAX_SCAN_POINTS_PER_FRAME {
        return Err(RotationError::ScanPointLimitExceeded);
    }
    Ok((samples_per_rotation, rotation_rate_fraction))
}

fn unsigned_256_to_f64(bytes: &[u8; 32]) -> f64 {
    let Some(first_nonzero) = bytes.iter().position(|byte| *byte != 0) else {
        return 0.0;
    };
    let bit_length = (bytes.len() - first_nonzero - 1) * 8
        + (u8::BITS - bytes[first_nonzero].leading_zeros()) as usize;
    let kept_bits = bit_length.min(f64::MANTISSA_DIGITS as usize);
    let discarded_bits = bit_length - kept_bits;

    let mut significand = 0_u64;
    for position in (discarded_bits..bit_length).rev() {
        significand = (significand << 1) | u64::from(bit_at(bytes, position));
    }
    if discarded_bits > 0 {
        let halfway = bit_at(bytes, discarded_bits - 1) == 1;
        let sticky = (0..discarded_bits - 1).any(|position| bit_at(bytes, position) == 1);
        if halfway && (sticky || significand & 1 == 1) {
            significand += 1;
        }
    }

    significand as f64 * 2.0_f64.powi(discarded_bits as i32)
}

fn bit_at(bytes: &[u8; 32], position_from_least_significant: usize) -> u8 {
    let byte = bytes[bytes.len() - 1 - position_from_least_significant / 8];
    (byte >> (position_from_least_significant % 8)) & 1
}
