//! RPLIDAR HQ quantization and driver-compatible integer decoding.

pub const HQ_ANGLE_STEPS_PER_ROTATION: u32 = 1 << 16;
pub const HQ_ANGLE_STEP_DEG: f64 = 360.0 / HQ_ANGLE_STEPS_PER_ROTATION as f64;
pub const HQ_DISTANCE_STEPS_PER_METER: u64 = 4_000;
pub const HQ_DISTANCE_STEP_M: f64 = 1.0 / HQ_DISTANCE_STEPS_PER_METER as f64;

const ANGLE_Q14_SCALE: u64 = 16_384;
const ANGLE_MDEG_PER_QUADRANT: u64 = 90_000;
const ANGLE_MDEG_PER_ROTATION: u64 = 360_000;
const DISTANCE_Q2_PER_MM: u64 = 4;
const U64_EXCLUSIVE_UPPER_BOUND_F64: f64 = 18_446_744_073_709_551_616.0;

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum SdkCompatibilityError {
    #[error("HQ angle must be finite and in [0, 360)")]
    InvalidAngle,
    #[error("HQ distance must be finite and non-negative")]
    InvalidDistance,
    #[error("HQ distance exceeds the supported unsigned 64-bit tick range")]
    DistanceTickOverflow,
    #[error("decoded HQ distance exceeds the unsigned 32-bit wire range")]
    DistanceMillimeterOverflow,
}

/// Quantize an angle to the nearest SDK HQ `angle_z_q14` tick.
pub fn quantize_hq_angle_ticks(angle_deg: f64) -> Result<u16, SdkCompatibilityError> {
    if !angle_deg.is_finite() || !(0.0..360.0).contains(&angle_deg) {
        return Err(SdkCompatibilityError::InvalidAngle);
    }

    // Preserve the model-v1 operation order, including its half-up boundary behavior.
    let rounded = (angle_deg / HQ_ANGLE_STEP_DEG + 0.5).floor();
    Ok((rounded as u32 % HQ_ANGLE_STEPS_PER_ROTATION) as u16)
}

/// Quantize an angle and return its representable degree value.
pub fn quantize_hq_angle_deg(angle_deg: f64) -> Result<f64, SdkCompatibilityError> {
    Ok(f64::from(quantize_hq_angle_ticks(angle_deg)?) * HQ_ANGLE_STEP_DEG)
}

/// Quantize multiple angles without changing their order.
pub fn quantize_hq_angles_deg(angles_deg: &[f64]) -> Result<Vec<f64>, SdkCompatibilityError> {
    angles_deg
        .iter()
        .copied()
        .map(quantize_hq_angle_deg)
        .collect()
}

/// Quantize a distance to the nearest SDK HQ `dist_mm_q2` tick.
pub fn quantize_hq_distance_ticks(distance_m: f64) -> Result<u64, SdkCompatibilityError> {
    if !distance_m.is_finite() || distance_m < 0.0 {
        return Err(SdkCompatibilityError::InvalidDistance);
    }

    let rounded = (distance_m * HQ_DISTANCE_STEPS_PER_METER as f64 + 0.5).floor();
    if !rounded.is_finite() || rounded >= U64_EXCLUSIVE_UPPER_BOUND_F64 {
        return Err(SdkCompatibilityError::DistanceTickOverflow);
    }
    Ok(rounded as u64)
}

/// Quantize a distance and return its representable meter value.
pub fn quantize_hq_distance_m(distance_m: f64) -> Result<f64, SdkCompatibilityError> {
    Ok(quantize_hq_distance_ticks(distance_m)? as f64 / HQ_DISTANCE_STEPS_PER_METER as f64)
}

/// Quantize multiple distances without changing their order.
pub fn quantize_hq_distances_m(distances_m: &[f64]) -> Result<Vec<f64>, SdkCompatibilityError> {
    distances_m
        .iter()
        .copied()
        .map(quantize_hq_distance_m)
        .collect()
}

/// Decode `angle_z_q14` exactly as the pinned lidar-processing driver does.
pub fn decode_hq_angle_mdeg(angle_ticks: u16) -> u32 {
    let angle_mdeg =
        (u64::from(angle_ticks) * ANGLE_MDEG_PER_QUADRANT + ANGLE_Q14_SCALE / 2) / ANGLE_Q14_SCALE;
    (angle_mdeg % ANGLE_MDEG_PER_ROTATION) as u32
}

/// Decode `dist_mm_q2` exactly as the pinned lidar-processing driver does.
pub fn decode_hq_distance_mm(distance_ticks: u64) -> Result<u32, SdkCompatibilityError> {
    let distance_mm = distance_ticks / DISTANCE_Q2_PER_MM;
    u32::try_from(distance_mm).map_err(|_| SdkCompatibilityError::DistanceMillimeterOverflow)
}
