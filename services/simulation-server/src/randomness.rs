//! Simulation model version 1 streams; not suitable for secrets or security tokens.

use sha2::{Digest, Sha256};

pub const SIMULATION_MODEL_VERSION: u32 = 1;
const SEED_PREFIX: &[u8; 16] = b"scrap-lidar-sim\0";
const UNIT_SCALE: f64 = 1.0 / 9_007_199_254_740_992.0;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StreamScope<'a> {
    Global,
    Sensor(&'a str),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum RandomError {
    #[error("random stream domain must be non-empty")]
    EmptyDomain,
    #[error("sensor random stream identifier must be non-empty")]
    EmptySensor,
    #[error("random stream label exceeds the unsigned 32-bit byte length")]
    LabelTooLong,
    #[error("uniform range must contain finite ordered bounds with a finite difference")]
    InvalidRange,
}

/// Implementations supply words only; sampling consumes one complete word per valid call.
pub trait RandomSource {
    fn next_u64(&mut self) -> u64;

    fn unit_f64(&mut self) -> f64 {
        ((self.next_u64() >> 11) as f64) * UNIT_SCALE
    }

    fn boolean(&mut self) -> bool {
        self.next_u64() >> 63 != 0
    }

    fn uniform(&mut self, lower: f64, upper: f64) -> Result<f64, RandomError> {
        let width = upper - lower;
        if !lower.is_finite() || !upper.is_finite() || upper < lower || !width.is_finite() {
            return Err(RandomError::InvalidRange);
        }
        let fraction = self.unit_f64();
        if lower == upper {
            return Ok(lower);
        }
        let scaled = width * fraction;
        let value = lower + scaled;
        Ok(if value >= upper {
            upper.next_down()
        } else {
            value
        })
    }
}

/// Hash the versioned, length-prefixed seed layout documented in simulation-model.md.
pub fn derive_stream_seed(
    root_seed: u64,
    scope: StreamScope<'_>,
    domain: &str,
) -> Result<[u8; 32], RandomError> {
    if domain.is_empty() {
        return Err(RandomError::EmptyDomain);
    }
    let (scope_tag, sensor) = match scope {
        StreamScope::Global => (0u8, ""),
        StreamScope::Sensor("") => return Err(RandomError::EmptySensor),
        StreamScope::Sensor(sensor) => (1u8, sensor),
    };
    let domain_length = u32::try_from(domain.len()).map_err(|_| RandomError::LabelTooLong)?;
    let sensor_length = u32::try_from(sensor.len()).map_err(|_| RandomError::LabelTooLong)?;
    let mut hash = Sha256::new();
    hash.update(SEED_PREFIX);
    hash.update(SIMULATION_MODEL_VERSION.to_be_bytes());
    hash.update(root_seed.to_be_bytes());
    hash.update([scope_tag]);
    hash.update(domain_length.to_be_bytes());
    hash.update(domain.as_bytes());
    hash.update(sensor_length.to_be_bytes());
    hash.update(sensor.as_bytes());
    Ok(hash.finalize().into())
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ModelRng {
    state: [u64; 4],
}

impl ModelRng {
    pub fn new(root_seed: u64, scope: StreamScope<'_>, domain: &str) -> Result<Self, RandomError> {
        Ok(Self::from_digest(derive_stream_seed(
            root_seed, scope, domain,
        )?))
    }

    fn from_digest(digest: [u8; 32]) -> Self {
        let mut state = [0; 4];
        for (word, bytes) in state.iter_mut().zip(digest.as_chunks::<8>().0) {
            *word = u64::from_be_bytes(*bytes);
        }
        if state == [0; 4] {
            state[0] = 1;
        }
        Self { state }
    }
}

impl RandomSource for ModelRng {
    fn next_u64(&mut self) -> u64 {
        let output = self.state[1].wrapping_mul(5).rotate_left(7).wrapping_mul(9);
        let shifted = self.state[1] << 17;
        self.state[2] ^= self.state[0];
        self.state[3] ^= self.state[1];
        self.state[1] ^= self.state[2];
        self.state[0] ^= self.state[3];
        self.state[2] ^= shifted;
        self.state[3] = self.state[3].rotate_left(45);
        output
    }
}

#[cfg(test)]
mod tests {
    use super::{ModelRng, RandomSource};

    #[test]
    fn xoshiro256_star_star_matches_independent_reference_words() {
        let mut rng = ModelRng {
            state: [1, 2, 3, 4],
        };
        assert_eq!(
            std::array::from_fn::<_, 8, _>(|_| rng.next_u64()),
            [
                11_520,
                0,
                1_509_978_240,
                1_215_971_899_390_074_240,
                1_216_172_134_540_287_360,
                607_988_272_756_665_600,
                16_172_922_978_634_559_625,
                8_476_171_486_693_032_832,
            ]
        );
    }

    #[test]
    fn zero_digest_maps_to_a_nonzero_state() {
        let mut rng = ModelRng::from_digest([0; 32]);
        assert_eq!(rng.state, [1, 0, 0, 0]);
        assert!((0..8).any(|_| rng.next_u64() != 0));
    }
}
