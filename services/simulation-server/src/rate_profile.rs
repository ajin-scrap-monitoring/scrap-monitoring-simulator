//! Smooth factor profiles with balanced deviation integrals.

use std::f64::consts::PI;

use crate::randomness::{RandomError, RandomSource};

pub const PROFILE_TOLERANCE: f64 = 1e-12;
pub const MAX_RATE_SEGMENTS: usize = 100_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum RateProfileError {
    #[error("rate segment start must be a finite non-negative number")]
    InvalidSegmentStart,
    #[error("rate segment duration must be a finite positive number")]
    InvalidSegmentDuration,
    #[error("rate segment deviation must be finite and greater than -1")]
    InvalidDeviation,
    #[error("rate profile duration must be a finite positive number")]
    InvalidDuration,
    #[error("rate profile segments must not overlap")]
    OverlappingSegments,
    #[error("rate profile segment starts and ends must be in non-decreasing order")]
    UnorderedSegments,
    #[error("rate profile segment must end within the profile duration")]
    SegmentOutsideProfile,
    #[error("rate profile deviations must have a finite zero integral")]
    NonZeroIntegral,
    #[error("rate profile elapsed time must be within its duration")]
    InvalidElapsed,
    #[error("rate profile interval end must be at least its start")]
    ReversedInterval,
    #[error("rate factor range must contain finite positive ordered values and include 1")]
    InvalidFactorRange,
    #[error("rate change duration range must contain finite positive ordered values")]
    InvalidChangeRange,
    #[error("rate profile segment count exceeds 100000")]
    SegmentLimit,
    #[error("rate profile floating-point arithmetic cannot advance finitely")]
    NoProgress,
    #[error(transparent)]
    Random(#[from] RandomError),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SmoothRateSegment {
    start_s: f64,
    duration_s: f64,
    deviation: f64,
}

impl SmoothRateSegment {
    pub fn new(start_s: f64, duration_s: f64, deviation: f64) -> Result<Self, RateProfileError> {
        if !start_s.is_finite() || start_s < 0.0 {
            return Err(RateProfileError::InvalidSegmentStart);
        }
        if !duration_s.is_finite() || duration_s <= 0.0 {
            return Err(RateProfileError::InvalidSegmentDuration);
        }
        if !deviation.is_finite() || deviation <= -1.0 {
            return Err(RateProfileError::InvalidDeviation);
        }
        if !(start_s + duration_s).is_finite() || start_s + duration_s <= start_s {
            return Err(RateProfileError::NoProgress);
        }
        Ok(Self {
            start_s,
            duration_s,
            deviation,
        })
    }

    pub fn start_s(&self) -> f64 {
        self.start_s
    }
    pub fn duration_s(&self) -> f64 {
        self.duration_s
    }
    pub fn deviation(&self) -> f64 {
        self.deviation
    }
    pub fn end_s(&self) -> f64 {
        self.start_s + self.duration_s
    }

    pub fn factor_at(&self, elapsed_s: f64) -> f64 {
        if elapsed_s <= self.start_s || elapsed_s >= self.end_s() {
            return 1.0;
        }
        let normalized = (elapsed_s - self.start_s) / self.duration_s;
        let sine = (PI * normalized).sin();
        1.0 + self.deviation * (sine * sine)
    }

    pub fn integrated_deviation_to(&self, elapsed_s: f64) -> f64 {
        let normalized = ((elapsed_s - self.start_s) / self.duration_s).clamp(0.0, 1.0);
        let integral = normalized / 2.0 - (2.0 * PI * normalized).sin() / (4.0 * PI);
        self.deviation * self.duration_s * integral
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct SmoothRateProfile {
    duration_s: f64,
    segments: Vec<SmoothRateSegment>,
    prefix_deviation_integrals_s: Vec<f64>,
}

impl SmoothRateProfile {
    pub fn new(
        duration_s: f64,
        segments: Vec<SmoothRateSegment>,
    ) -> Result<Self, RateProfileError> {
        validate_duration(duration_s)?;
        if segments.len() > MAX_RATE_SEGMENTS {
            return Err(RateProfileError::SegmentLimit);
        }
        let tolerance_s = duration_s.max(1.0) * PROFILE_TOLERANCE;
        let mut previous_start = 0.0;
        let mut previous_end = 0.0;
        let mut integral = 0.0;
        let mut prefix = Vec::with_capacity(segments.len() + 1);
        prefix.push(0.0);
        for segment in &segments {
            if segment.start_s < previous_start || segment.end_s() < previous_end {
                return Err(RateProfileError::UnorderedSegments);
            }
            if segment.start_s < previous_end - tolerance_s {
                return Err(RateProfileError::OverlappingSegments);
            }
            if segment.end_s() > duration_s + tolerance_s {
                return Err(RateProfileError::SegmentOutsideProfile);
            }
            previous_start = segment.start_s;
            previous_end = segment.end_s();
            integral += segment.deviation * segment.duration_s / 2.0;
            if !integral.is_finite() {
                return Err(RateProfileError::NonZeroIntegral);
            }
            prefix.push(integral);
        }
        if integral.abs() > tolerance_s {
            return Err(RateProfileError::NonZeroIntegral);
        }
        Ok(Self {
            duration_s,
            segments,
            prefix_deviation_integrals_s: prefix,
        })
    }

    pub fn duration_s(&self) -> f64 {
        self.duration_s
    }
    pub fn segments(&self) -> &[SmoothRateSegment] {
        &self.segments
    }

    pub fn factor_at(&self, elapsed_s: f64) -> Result<f64, RateProfileError> {
        self.validate_elapsed(elapsed_s)?;
        if elapsed_s == self.duration_s || self.segments.is_empty() {
            return Ok(1.0);
        }
        let after = self
            .segments
            .partition_point(|segment| segment.start_s <= elapsed_s);
        Ok(match after.checked_sub(1) {
            Some(index) => self.segments[index].factor_at(elapsed_s),
            None => 1.0,
        })
    }

    pub fn integrated_factor_between(
        &self,
        start_s: f64,
        end_s: f64,
    ) -> Result<f64, RateProfileError> {
        self.validate_elapsed(start_s)?;
        self.validate_elapsed(end_s)?;
        if end_s < start_s {
            return Err(RateProfileError::ReversedInterval);
        }
        if start_s == end_s {
            return Ok(0.0);
        }
        Ok(self.integrated_factor_to(end_s) - self.integrated_factor_to(start_s))
    }

    fn integrated_factor_to(&self, elapsed_s: f64) -> f64 {
        if elapsed_s == self.duration_s {
            return self.duration_s;
        }
        let completed = self
            .segments
            .partition_point(|segment| segment.end_s() <= elapsed_s);
        let mut deviation = self.prefix_deviation_integrals_s[completed];
        if let Some(segment) = self.segments.get(completed)
            && elapsed_s > segment.start_s
        {
            deviation += segment.integrated_deviation_to(elapsed_s);
        }
        elapsed_s + deviation
    }

    fn validate_elapsed(&self, elapsed_s: f64) -> Result<(), RateProfileError> {
        if !elapsed_s.is_finite() || !(0.0..=self.duration_s).contains(&elapsed_s) {
            return Err(RateProfileError::InvalidElapsed);
        }
        Ok(())
    }
}

pub fn create_smooth_rate_profile(
    duration_s: f64,
    factor_range: [f64; 2],
    change_duration_s_range: [f64; 2],
    rng: &mut impl RandomSource,
) -> Result<SmoothRateProfile, RateProfileError> {
    validate_duration(duration_s)?;
    let [lower, upper] = factor_range;
    if !lower.is_finite() || !upper.is_finite() || lower <= 0.0 || lower > 1.0 || upper < 1.0 {
        return Err(RateProfileError::InvalidFactorRange);
    }
    let [minimum_change, maximum_change] = change_duration_s_range;
    if !minimum_change.is_finite()
        || !maximum_change.is_finite()
        || minimum_change <= 0.0
        || maximum_change < minimum_change
    {
        return Err(RateProfileError::InvalidChangeRange);
    }
    let positive_limit = upper - 1.0;
    let negative_limit = 1.0 - lower;
    if positive_limit == 0.0 || negative_limit == 0.0 {
        return SmoothRateProfile::new(duration_s, Vec::new());
    }
    let mut cursor = 0.0;
    let mut segments = Vec::new();
    while duration_s - cursor >= 2.0 * minimum_change {
        if segments.len() + 2 > MAX_RATE_SEGMENTS {
            return Err(RateProfileError::SegmentLimit);
        }
        let remaining = duration_s - cursor;
        let first_duration = rng.uniform(
            minimum_change,
            maximum_change.min(remaining - minimum_change),
        )?;
        let second_duration = rng.uniform(
            minimum_change,
            maximum_change.min(remaining - first_duration),
        )?;
        let positive_first = rng.boolean();
        let (positive_duration, negative_duration) = if positive_first {
            (first_duration, second_duration)
        } else {
            (second_duration, first_duration)
        };
        let maximum_balance =
            (positive_limit * positive_duration).min(negative_limit * negative_duration);
        let balance = rng.uniform(0.0, maximum_balance)?;
        let positive_deviation = balance / positive_duration;
        let negative_deviation = -balance / negative_duration;
        let (first_deviation, second_deviation) = if positive_first {
            (positive_deviation, negative_deviation)
        } else {
            (negative_deviation, positive_deviation)
        };
        segments.push(SmoothRateSegment::new(
            cursor,
            first_duration,
            first_deviation,
        )?);
        segments.push(SmoothRateSegment::new(
            cursor + first_duration,
            second_duration,
            second_deviation,
        )?);
        let next = cursor + (first_duration + second_duration);
        if !next.is_finite() || next <= cursor {
            return Err(RateProfileError::NoProgress);
        }
        cursor = next;
    }
    SmoothRateProfile::new(duration_s, segments)
}

fn validate_duration(duration_s: f64) -> Result<(), RateProfileError> {
    if !duration_s.is_finite() || duration_s <= 0.0 {
        return Err(RateProfileError::InvalidDuration);
    }
    Ok(())
}
