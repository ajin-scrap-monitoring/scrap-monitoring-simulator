//! Sensor-local final distance and quality generation.

use crate::randomness::{ModelRng, RandomSource, StreamScope};

use super::{
    HitKind, HqSample, MeasuredScan, MeasurementError, MeasurementResult, Result,
    SpatialDistortionResolver, TimedReferenceScan, require_distance_bounds,
};

const MAX_REJECTION_ATTEMPTS_PER_SAMPLE: usize = 1_000_000;
const MAX_DROPOUT_EVENTS_PER_BATCH: usize = 100_000;

#[derive(Clone, Debug, PartialEq)]
pub struct QualityDistribution {
    thresholds: [u128; 255],
    total: u128,
}

impl QualityDistribution {
    pub fn new(frequencies: [u64; 256]) -> Result<Self> {
        let mut cumulative_values = [0_u128; 255];
        let mut total = 0_u128;
        for (index, frequency) in frequencies.into_iter().enumerate() {
            total = total
                .checked_add(u128::from(frequency))
                .ok_or(MeasurementError::Exhausted(
                    "quality frequency total exceeds u128",
                ))?;
            if index < cumulative_values.len() {
                cumulative_values[index] = total;
            }
        }
        if total == 0 {
            return Err(MeasurementError::Invalid(
                "quality frequencies require a positive total",
            ));
        }
        let thresholds = cumulative_values.map(|value| scaled_word_threshold(value, total));
        Ok(Self { thresholds, total })
    }

    fn sample(&self, rng: &mut impl RandomSource) -> u8 {
        let draw = u128::from(rng.next_u64());
        self.thresholds
            .iter()
            .position(|&threshold| draw < threshold)
            .unwrap_or(255) as u8
    }

    pub fn total(&self) -> u128 {
        self.total
    }
}

fn scaled_word_threshold(cumulative: u128, total: u128) -> u128 {
    let mut remainder = cumulative;
    let mut threshold = 0_u128;
    for _ in 0..u64::BITS {
        threshold <<= 1;
        let complement = total - remainder;
        if remainder >= complement {
            remainder -= complement;
            threshold |= 1;
        } else {
            remainder *= 2;
        }
    }
    threshold + u128::from(remainder != 0)
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct DropoutSettings {
    pub event_interval_s_range: [f64; 2],
    pub duration_s_range: [f64; 2],
}

#[derive(Clone, Debug)]
pub struct SensorDropoutScheduler {
    sensor_id: String,
    event_interval_s_range: [f64; 2],
    duration_s_range: [f64; 2],
    rng: ModelRng,
    current_start_s: f64,
    current_end_s: Option<f64>,
    last_point_elapsed_s: Option<f64>,
}

impl SensorDropoutScheduler {
    pub fn new(sensor_id: &str, settings: DropoutSettings, seed: u64) -> Result<Self> {
        if sensor_id.is_empty() {
            return Err(MeasurementError::Invalid(
                "dropout sensor identifier must be non-empty",
            ));
        }
        require_positive_range(settings.event_interval_s_range)?;
        require_positive_range(settings.duration_s_range)?;
        let mut rng = ModelRng::new(seed, StreamScope::Sensor(sensor_id), "dropout")?;
        let current_start_s = rng.uniform(
            settings.event_interval_s_range[0],
            settings.event_interval_s_range[1],
        )?;
        Ok(Self {
            sensor_id: sensor_id.to_owned(),
            event_interval_s_range: settings.event_interval_s_range,
            duration_s_range: settings.duration_s_range,
            rng,
            current_start_s,
            current_end_s: None,
            last_point_elapsed_s: None,
        })
    }

    pub fn sensor_id(&self) -> &str {
        &self.sensor_id
    }

    pub fn active_mask(&mut self, point_times_s: &[f64]) -> Result<Vec<bool>> {
        let mut candidate = self.clone();
        let active = candidate.active_mask_inner(point_times_s)?;
        *self = candidate;
        Ok(active)
    }

    fn active_mask_inner(&mut self, point_times_s: &[f64]) -> Result<Vec<bool>> {
        if point_times_s.is_empty()
            || point_times_s
                .iter()
                .any(|value| !value.is_finite() || *value < 0.0)
            || point_times_s.windows(2).any(|pair| pair[0] >= pair[1])
            || self
                .last_point_elapsed_s
                .is_some_and(|last| point_times_s[0] <= last)
        {
            return Err(MeasurementError::Invalid(
                "dropout point times must be finite and strictly increasing",
            ));
        }
        let mut active = vec![false; point_times_s.len()];
        let through = *point_times_s.last().expect("non-empty checked above");
        let mut generated = 0;
        while self.current_start_s <= through {
            generated += 1;
            if generated > MAX_DROPOUT_EVENTS_PER_BATCH {
                return Err(MeasurementError::Exhausted(
                    "dropout event count exceeds the per-batch limit",
                ));
            }
            let end = match self.current_end_s {
                Some(end) => end,
                None => {
                    let duration = self
                        .rng
                        .uniform(self.duration_s_range[0], self.duration_s_range[1])?;
                    let end = self.current_start_s + duration;
                    if !end.is_finite() || end <= self.current_start_s {
                        return Err(MeasurementError::Numerical(
                            "dropout event time did not advance",
                        ));
                    }
                    self.current_end_s = Some(end);
                    end
                }
            };
            for (selected, &elapsed_s) in active.iter_mut().zip(point_times_s) {
                *selected |= elapsed_s >= self.current_start_s && elapsed_s < end;
            }
            if end > through {
                break;
            }
            let gap = self.rng.uniform(
                self.event_interval_s_range[0],
                self.event_interval_s_range[1],
            )?;
            let next = end + gap;
            if !next.is_finite() || next <= end {
                return Err(MeasurementError::Numerical(
                    "dropout schedule time did not advance",
                ));
            }
            self.current_start_s = next;
            self.current_end_s = None;
        }
        self.last_point_elapsed_s = Some(through);
        Ok(active)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct MeasurementSettings {
    pub sensor_id: String,
    pub min_distance_m: f64,
    pub max_distance_m: f64,
    pub noise_enabled: bool,
    pub noise_standard_deviation_m: f64,
    pub noise_limit_m: f64,
    pub reflection_error_enabled: bool,
    pub reflection_error_probability: f64,
    pub reflection_error_reduction_range_m: [f64; 2],
    pub valid_quality: QualityDistribution,
    pub invalid_quality: QualityDistribution,
    pub dropout: Option<DropoutSettings>,
}

#[derive(Clone, Debug)]
pub struct MeasurementGenerator {
    settings: MeasurementSettings,
    noise_rng: ModelRng,
    quality_rng: ModelRng,
    reflection_rng: ModelRng,
    dropout: Option<SensorDropoutScheduler>,
}

impl MeasurementGenerator {
    pub fn new(settings: MeasurementSettings, seed: u64) -> Result<Self> {
        validate_settings(&settings)?;
        let scope = StreamScope::Sensor(&settings.sensor_id);
        let noise_rng = ModelRng::new(seed, scope, "distance-noise")?;
        let quality_rng = ModelRng::new(seed, scope, "quality")?;
        let reflection_rng = ModelRng::new(seed, scope, "reflection-error")?;
        let dropout = settings
            .dropout
            .map(|dropout| SensorDropoutScheduler::new(&settings.sensor_id, dropout, seed))
            .transpose()?;
        Ok(Self {
            settings,
            noise_rng,
            quality_rng,
            reflection_rng,
            dropout,
        })
    }

    pub fn sensor_id(&self) -> &str {
        &self.settings.sensor_id
    }

    pub fn generate(
        &mut self,
        reference: TimedReferenceScan,
        spatial: Option<&SpatialDistortionResolver>,
    ) -> Result<MeasurementResult> {
        let noise_checkpoint = self.noise_rng.clone();
        let quality_checkpoint = self.quality_rng.clone();
        let reflection_checkpoint = self.reflection_rng.clone();
        let dropout_checkpoint = self.dropout.clone();
        let result = self.generate_inner(reference, spatial);
        if result.is_err() {
            self.noise_rng = noise_checkpoint;
            self.quality_rng = quality_checkpoint;
            self.reflection_rng = reflection_checkpoint;
            self.dropout = dropout_checkpoint;
        }
        result
    }

    fn generate_inner(
        &mut self,
        reference: TimedReferenceScan,
        spatial: Option<&SpatialDistortionResolver>,
    ) -> Result<MeasurementResult> {
        if reference.sensor_id() != self.sensor_id() {
            return Err(MeasurementError::Invalid(
                "measurement generator and reference sensors must match",
            ));
        }
        let reference_distances: Vec<_> = reference
            .scan()
            .points()
            .iter()
            .map(|point| point.distance_m)
            .collect();
        let mut distances = match spatial {
            Some(spatial) => spatial.resolve_distances(
                &reference,
                self.settings.min_distance_m,
                self.settings.max_distance_m,
            )?,
            None => reference_distances.clone(),
        };
        if distances.len() != reference_distances.len()
            || distances
                .iter()
                .any(|value| !value.is_finite() || *value < 0.0)
        {
            return Err(MeasurementError::Numerical(
                "spatial resolver returned invalid distances",
            ));
        }
        self.apply_reflection(&reference, &reference_distances, &mut distances);
        if let Some(dropout) = &mut self.dropout {
            for (distance, selected) in distances
                .iter_mut()
                .zip(dropout.active_mask(reference.schedule().point_elapsed_times_s())?)
            {
                if selected {
                    *distance = 0.0;
                }
            }
        }
        if self.settings.noise_enabled {
            let noise = sample_truncated_normal(
                &mut self.noise_rng,
                distances.len(),
                self.settings.noise_standard_deviation_m,
                self.settings.noise_limit_m,
            )?;
            for (distance, noise) in distances.iter_mut().zip(noise) {
                if *distance > 0.0 {
                    *distance += noise;
                }
            }
        }
        let mut hq_samples = Vec::new();
        hq_samples
            .try_reserve_exact(distances.len())
            .map_err(|_| MeasurementError::Exhausted("HQ sample allocation failed"))?;
        for (&angle_deg, distance) in reference.schedule().angles_deg().iter().zip(distances) {
            let mut distance_tick = super::sdk::quantize_hq_distance_ticks(distance.max(0.0))?;
            let distance_m = distance_tick as f64 / super::sdk::HQ_DISTANCE_STEPS_PER_METER as f64;
            let valid = distance_tick != 0
                && distance_m >= self.settings.min_distance_m
                && distance_m <= self.settings.max_distance_m;
            if !valid {
                distance_tick = 0;
            }
            let quality = if valid {
                self.settings.valid_quality.sample(&mut self.quality_rng)
            } else {
                self.settings.invalid_quality.sample(&mut self.quality_rng)
            };
            hq_samples.push(HqSample {
                angle_z_q14: super::sdk::quantize_hq_angle_ticks(angle_deg)?,
                dist_mm_q2: distance_tick,
                quality,
            });
        }
        let measured = MeasuredScan::from_hq_samples(self.settings.sensor_id.clone(), hq_samples)?;
        MeasurementResult::new(reference, measured)
    }

    fn apply_reflection(
        &mut self,
        reference: &TimedReferenceScan,
        reference_distances: &[f64],
        distances: &mut [f64],
    ) {
        if !self.settings.reflection_error_enabled
            || self.settings.reflection_error_probability == 0.0
        {
            return;
        }
        let selected_draws: Vec<_> = (0..distances.len())
            .map(|_| self.reflection_rng.unit_f64())
            .collect();
        let reduction_draws: Vec<_> = (0..distances.len())
            .map(|_| self.reflection_rng.unit_f64())
            .collect();
        let [lower, upper] = self.settings.reflection_error_reduction_range_m;
        for (index, point) in reference.scan().points().iter().enumerate() {
            let maximum = upper.min(reference_distances[index] - self.settings.min_distance_m);
            if point.hit_kind == Some(HitKind::Surface)
                && maximum >= lower
                && selected_draws[index] < self.settings.reflection_error_probability
            {
                let reduction = lower + reduction_draws[index] * (maximum - lower);
                distances[index] = distances[index].min(reference_distances[index] - reduction);
            }
        }
    }
}

fn validate_settings(settings: &MeasurementSettings) -> Result<()> {
    require_distance_bounds(settings.min_distance_m, settings.max_distance_m)?;
    if settings.sensor_id.is_empty()
        || settings.min_distance_m == 0.0
        || !settings.max_distance_m.is_finite()
        || !settings.noise_standard_deviation_m.is_finite()
        || settings.noise_standard_deviation_m < 0.0
        || !settings.noise_limit_m.is_finite()
        || settings.noise_limit_m < 0.0
        || !settings.reflection_error_probability.is_finite()
        || !(0.0..=1.0).contains(&settings.reflection_error_probability)
    {
        return Err(MeasurementError::Invalid(
            "measurement settings contain an invalid scalar",
        ));
    }
    require_positive_range(settings.reflection_error_reduction_range_m)
}

fn require_positive_range(range: [f64; 2]) -> Result<()> {
    if !range[0].is_finite() || !range[1].is_finite() || range[0] <= 0.0 || range[1] < range[0] {
        return Err(MeasurementError::Invalid(
            "measurement range must be finite, positive, and ordered",
        ));
    }
    Ok(())
}

fn sample_truncated_normal(
    rng: &mut impl RandomSource,
    count: usize,
    standard_deviation: f64,
    limit: f64,
) -> Result<Vec<f64>> {
    if count == 0 || standard_deviation == 0.0 || limit == 0.0 {
        return Ok(vec![0.0; count]);
    }
    let mut result = Vec::with_capacity(count);
    while result.len() < count {
        let mut accepted = None;
        for _ in 0..MAX_REJECTION_ATTEMPTS_PER_SAMPLE {
            if limit >= standard_deviation * 0.5 {
                let radial = (-2.0 * (1.0 - rng.unit_f64()).ln()).sqrt();
                let candidate =
                    standard_deviation * radial * (std::f64::consts::TAU * rng.unit_f64()).cos();
                if candidate.abs() <= limit {
                    accepted = Some(candidate);
                    break;
                }
            } else {
                let candidate = -limit + (2.0 * limit) * rng.unit_f64();
                let probability = (-0.5 * (candidate / standard_deviation).powi(2)).exp();
                if rng.unit_f64() < probability {
                    accepted = Some(candidate);
                    break;
                }
            }
        }
        result.push(accepted.ok_or(MeasurementError::Exhausted(
            "truncated normal rejection limit exceeded",
        ))?);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use crate::randomness::RandomSource;

    use super::QualityDistribution;

    struct FixedWord(u64);

    impl RandomSource for FixedWord {
        fn next_u64(&mut self) -> u64 {
            self.0
        }
    }

    #[test]
    fn quality_uses_the_full_word_at_an_extreme_frequency_boundary() {
        let mut frequencies = [0_u64; 256];
        frequencies[3] = 1;
        frequencies[9] = i64::MAX as u64;
        let distribution = QualityDistribution::new(frequencies).unwrap();

        assert_eq!(distribution.sample(&mut FixedWord(0)), 3);
        assert_eq!(distribution.sample(&mut FixedWord(1)), 3);
        assert_eq!(distribution.sample(&mut FixedWord(2)), 9);
        assert_eq!(distribution.sample(&mut FixedWord(u64::MAX)), 9);
    }
}
