use super::{Result, ScenarioError};

pub const REFERENCE_MEAN_FILL_DURATION_S: f64 = 24.0 * 60.0 * 60.0;

pub fn scenario_time_scale(mean_fill_duration_s: f64) -> Result<f64> {
    if !mean_fill_duration_s.is_finite() || mean_fill_duration_s <= 0.0 {
        return Err(ScenarioError::Invalid(
            "mean fill duration must be a finite positive number",
        ));
    }
    let scale = mean_fill_duration_s / REFERENCE_MEAN_FILL_DURATION_S;
    if !scale.is_finite() || scale <= 0.0 {
        return Err(ScenarioError::Invalid(
            "scenario time scale must be finite and positive",
        ));
    }
    Ok(scale)
}

pub fn scale_duration_range(value: [f64; 2], time_scale: f64) -> Result<[f64; 2]> {
    if !time_scale.is_finite() || time_scale <= 0.0 {
        return Err(ScenarioError::Invalid(
            "scenario time scale must be finite and positive",
        ));
    }
    let scaled = [value[0] * time_scale, value[1] * time_scale];
    if !scaled[0].is_finite() || !scaled[1].is_finite() || scaled[0] <= 0.0 || scaled[1] < scaled[0]
    {
        return Err(ScenarioError::Invalid(
            "scaled duration range must contain finite positive ordered values",
        ));
    }
    Ok(scaled)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn twenty_four_hours_is_the_unscaled_reference() {
        assert_eq!(scenario_time_scale(86_400.0).unwrap(), 1.0);
        assert_eq!(scenario_time_scale(3_600.0).unwrap(), 1.0 / 24.0);
        assert_eq!(
            scale_duration_range([60.0, 180.0], 1.0 / 24.0).unwrap(),
            [2.5, 7.5]
        );
    }

    #[test]
    fn scaling_rejects_non_finite_underflow_and_reversed_ranges() {
        for value in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert!(scenario_time_scale(value).is_err());
            assert!(scale_duration_range([1.0, 2.0], value).is_err());
        }
        assert!(scale_duration_range([2.0, 1.0], 1.0).is_err());
        assert!(scale_duration_range([f64::from_bits(1), 1.0], 0.5).is_err());
    }
}
