//! Exact decimal ratios with one correctly-rounded conversion to simulation time.

use num_bigint::BigUint;

#[derive(Clone, Debug)]
pub(crate) struct PositiveRatio {
    numerator: BigUint,
    denominator: BigUint,
}

impl PositiveRatio {
    pub(crate) fn from_decimal_f64s(numerator: f64, denominator: f64) -> Option<Self> {
        let (mut numerator, numerator_exponent) = decimal_parts(numerator)?;
        let (mut denominator, denominator_exponent) = decimal_parts(denominator)?;
        let exponent = numerator_exponent - denominator_exponent;

        let common = gcd(numerator, denominator);
        numerator /= common;
        denominator /= common;
        let mut numerator = BigUint::from(numerator);
        let mut denominator = BigUint::from(denominator);
        if exponent > 0 {
            numerator *= BigUint::from(10_u8).pow(exponent as u32);
        } else if exponent < 0 {
            denominator *= BigUint::from(10_u8).pow((-exponent) as u32);
        }
        Some(Self {
            numerator,
            denominator,
        })
    }

    pub(crate) fn ceil_multiple(&self, multiplier: u64) -> Option<u64> {
        let product = &self.numerator * multiplier;
        let mut quotient = &product / &self.denominator;
        if product % &self.denominator != BigUint::from(0_u8) {
            quotient += 1_u8;
        }
        u64::try_from(quotient).ok()
    }

    pub(crate) fn multiple_to_f64(&self, multiplier: u64) -> Option<f64> {
        if multiplier == 0 {
            return Some(0.0);
        }
        let numerator = &self.numerator * multiplier;
        rational_to_f64(&numerator, &self.denominator)
    }

    pub(crate) fn reciprocal_multiple_to_f64(&self, multiplier: u64) -> Option<f64> {
        if multiplier == 0 {
            return Some(0.0);
        }
        let numerator = &self.denominator * multiplier;
        rational_to_f64(&numerator, &self.numerator)
    }
}

fn rational_to_f64(numerator: &BigUint, denominator: &BigUint) -> Option<f64> {
    let zero = BigUint::from(0_u8);
    if numerator == &zero || denominator == &zero {
        return (denominator != &zero).then_some(0.0);
    }
    let numerator_bits = i32::try_from(numerator.bits()).ok()?;
    let denominator_bits = i32::try_from(denominator.bits()).ok()?;
    let mut exponent = numerator_bits - denominator_bits;
    if exponent >= 0 {
        if numerator < &(denominator << exponent as usize) {
            exponent -= 1;
        }
    } else if (numerator << (-exponent) as usize) < *denominator {
        exponent -= 1;
    }
    if exponent > 1023 {
        return None;
    }

    if exponent < -1022 {
        let significand = rounded_scaled_quotient(numerator, denominator, 1074)?;
        let bits = u64::try_from(significand).ok()?;
        return (bits <= 1_u64 << 52).then(|| f64::from_bits(bits));
    }

    let mut significand = rounded_scaled_quotient(numerator, denominator, 52 - exponent)?;
    if significand.bits() == 54 {
        significand >>= 1_usize;
        exponent += 1;
        if exponent > 1023 {
            return None;
        }
    }
    let significand = u64::try_from(significand).ok()?;
    debug_assert!(((1_u64 << 52)..(1_u64 << 53)).contains(&significand));
    let exponent_bits = u64::try_from(exponent + 1023).ok()? << 52;
    let fraction_bits = significand - (1_u64 << 52);
    Some(f64::from_bits(exponent_bits | fraction_bits))
}

fn rounded_scaled_quotient(
    numerator: &BigUint,
    denominator: &BigUint,
    binary_shift: i32,
) -> Option<BigUint> {
    let (scaled_numerator, scaled_denominator) = if binary_shift >= 0 {
        (numerator << binary_shift as usize, denominator.clone())
    } else {
        (numerator.clone(), denominator << (-binary_shift) as usize)
    };
    let mut quotient = &scaled_numerator / &scaled_denominator;
    let remainder = scaled_numerator % &scaled_denominator;
    let twice_remainder = &remainder << 1_usize;
    if twice_remainder > scaled_denominator
        || (twice_remainder == scaled_denominator && quotient.bit(0))
    {
        quotient += 1_u8;
    }
    Some(quotient)
}

fn decimal_parts(value: f64) -> Option<(u128, i32)> {
    let rendered = value.to_string();
    let (mantissa, explicit_exponent) =
        if let Some((mantissa, exponent)) = rendered.split_once(['e', 'E']) {
            (mantissa, exponent.parse::<i32>().ok()?)
        } else {
            (rendered.as_str(), 0)
        };
    let fractional_digits = mantissa
        .split_once('.')
        .map_or(0, |(_, fractional)| fractional.len()) as i32;
    let mut digits: String = mantissa
        .chars()
        .filter(|character| *character != '.')
        .collect();
    let first_nonzero = digits.find(|character| character != '0')?;
    digits.drain(..first_nonzero);
    let mut exponent = explicit_exponent - fractional_digits;
    while digits.ends_with('0') {
        digits.pop();
        exponent += 1;
    }
    Some((digits.parse().ok()?, exponent))
}

fn gcd(mut left: u128, mut right: u128) -> u128 {
    while right != 0 {
        let remainder = left % right;
        left = right;
        right = remainder;
    }
    left
}

#[cfg(test)]
mod tests {
    use super::PositiveRatio;

    #[test]
    fn decimal_interval_multiples_are_rounded_once_without_accumulated_drift() {
        let interval = PositiveRatio::from_decimal_f64s(0.1, 1.0).unwrap();
        for (index, expected_bits) in [
            (3, 0x3fd3_3333_3333_3333),
            (6, 0x3fe3_3333_3333_3333),
            (7, 0x3fe6_6666_6666_6666),
            (12, 0x3ff3_3333_3333_3333),
            (3_212_757, 0x4113_9bee_cccc_cccd),
        ] {
            assert_eq!(
                interval.multiple_to_f64(index).unwrap().to_bits(),
                expected_bits
            );
        }
    }

    #[test]
    fn decimal_interval_and_reciprocal_rate_share_identical_boundaries() {
        let interval = PositiveRatio::from_decimal_f64s(0.1, 1.0).unwrap();
        let rate = PositiveRatio::from_decimal_f64s(10.0, 1.0).unwrap();
        for index in [1, 3, 6, 7, 12, 3_212_757] {
            assert_eq!(
                interval.multiple_to_f64(index).unwrap().to_bits(),
                rate.reciprocal_multiple_to_f64(index).unwrap().to_bits()
            );
        }
    }
}
