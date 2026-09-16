#[path = "../src/decimal_ratio.rs"]
mod decimal_ratio;
#[path = "../src/measurement/rotation.rs"]
mod rotation;
#[path = "../src/measurement/sdk.rs"]
mod sdk;

use rotation::{
    RotationError, ScheduledScan, SensorRotationScheduler, create_seeded_rotation_scheduler,
    validate_scan_point_limit,
};
use sdk::{
    HQ_ANGLE_STEP_DEG, HQ_DISTANCE_STEP_M, SdkCompatibilityError, decode_hq_angle_mdeg,
    decode_hq_distance_mm, quantize_hq_angle_ticks, quantize_hq_angles_deg,
    quantize_hq_distance_ticks, quantize_hq_distances_m,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("fixtures/model-v1/rotation-and-hq.json");

fn floats(value: &Value) -> Vec<f64> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item.as_f64().unwrap())
        .collect()
}

#[test]
fn rotation_schedules_match_the_rng_independent_fixture() {
    let fixture: Value = serde_json::from_str(FIXTURE).unwrap();
    for case in fixture["rotations"].as_array().unwrap() {
        let sample_rate_hz = case["sample_rate_hz"].as_f64().unwrap();
        let rotation_rate_hz = case["rotation_rate_hz"].as_f64().unwrap();
        let mut scheduler = SensorRotationScheduler::new(
            "lidar_1",
            sample_rate_hz,
            rotation_rate_hz,
            case["initial_angle_deg"].as_f64().unwrap(),
        )
        .unwrap();

        for expected in case["scans"].as_array().unwrap() {
            let scan = scheduler.next_scan().unwrap();
            assert_eq!(scan.sensor_id(), "lidar_1");
            assert_eq!(scan.scan_id(), expected["scan_id"].as_u64().unwrap());
            assert_eq!(
                scan.rotation_started_at_s(),
                expected["rotation_started_at_s"].as_f64().unwrap()
            );
            assert_eq!(
                scan.completed_at_s(),
                expected["completed_at_s"].as_f64().unwrap()
            );
            assert_eq!(scan.angles_deg(), floats(&expected["angles_deg"]));
            assert_eq!(
                scan.point_elapsed_times_s(),
                floats(&expected["point_elapsed_times_s"])
            );
            assert_eq!(
                scan.point_count(),
                expected["angles_deg"].as_array().unwrap().len()
            );
            assert_eq!(scan.captured_elapsed_s(), scan.point_elapsed_times_s()[0]);
        }
    }
}

#[test]
fn fractional_ratio_alternates_without_losing_or_repeating_samples() {
    let mut scheduler = SensorRotationScheduler::new("sensor-a", 5.0, 2.0, 10.0).unwrap();
    let scans: Vec<_> = (0..4).map(|_| scheduler.next_scan().unwrap()).collect();

    assert_eq!(
        scans
            .iter()
            .map(ScheduledScan::point_count)
            .collect::<Vec<_>>(),
        [3, 2, 3, 2]
    );
    assert_eq!(
        scans
            .iter()
            .flat_map(|scan| scan.point_elapsed_times_s().iter().copied())
            .collect::<Vec<_>>(),
        (0..10).map(|index| index as f64 / 5.0).collect::<Vec<_>>()
    );
}

#[test]
fn decimal_rotation_boundaries_match_fraction_conversion() {
    let mut scheduler = SensorRotationScheduler::new("sensor-a", 9.9, 3.3, 10.0).unwrap();
    assert_eq!(
        scheduler.next_completion_elapsed_s().unwrap().to_bits(),
        0x3fd3_64d9_364d_9365
    );
    let scans: Vec<_> = (0..3).map(|_| scheduler.next_scan().unwrap()).collect();
    assert_eq!(scans[0].rotation_started_at_s().to_bits(), 0);
    assert_eq!(scans[0].completed_at_s().to_bits(), 0x3fd3_64d9_364d_9365);
    assert_eq!(
        scans[2].rotation_started_at_s().to_bits(),
        0x3fe3_64d9_364d_9365
    );
    assert_eq!(scans[2].completed_at_s().to_bits(), 0x3fed_1745_d174_5d17);
}

#[test]
fn long_running_decimal_boundary_uses_one_exact_rational_rounding() {
    let mut scheduler =
        SensorRotationScheduler::new("sensor-a", 32_000.0, 6.351_617_329_050_948, 10.0).unwrap();
    scheduler.set_state_for_test(3_212_757, 0);
    assert_eq!(
        scheduler.next_completion_elapsed_s().unwrap().to_bits(),
        0x411e_df65_3ece_3771
    );
}

#[test]
fn large_decimal_rate_keeps_fraction_rounding_without_a_float_fallback() {
    let mut scheduler = SensorRotationScheduler::new("sensor-a", 1e39, 1e39, 10.0).unwrap();
    assert_eq!(
        scheduler.next_completion_elapsed_s().unwrap().to_bits(),
        0x37d5_c72f_b155_2d83
    );
    assert_eq!(
        scheduler.next_scan().unwrap().completed_at_s().to_bits(),
        0x37d5_c72f_b155_2d83
    );

    let scheduler = SensorRotationScheduler::new("sensor-a", 1e-320, 1e-320, 10.0).unwrap();
    assert_eq!(
        scheduler.next_completion_elapsed_s(),
        Err(RotationError::RotationTimeOutOfRange)
    );
}

#[test]
fn seeded_angles_match_model_v1_and_are_sensor_specific() {
    let fixture: Value = serde_json::from_str(FIXTURE).unwrap();
    let expected = &fixture["seeded_initial_angles_deg"];
    for sensor_id in ["lidar_1", "lidar_2"] {
        let scheduler =
            create_seeded_rotation_scheduler(sensor_id, 32_000.0, 10.0, 123_456_789).unwrap();
        assert_eq!(
            scheduler.initial_angle_deg(),
            expected[sensor_id].as_f64().unwrap()
        );
    }
    let first = SensorRotationScheduler::seeded("lidar_1", 8.0, 2.0, 123).unwrap();
    let repeated = SensorRotationScheduler::seeded("lidar_1", 8.0, 2.0, 123).unwrap();
    let other = SensorRotationScheduler::seeded("lidar_2", 8.0, 2.0, 123).unwrap();
    assert_eq!(first.initial_angle_deg(), repeated.initial_angle_deg());
    assert_ne!(first.initial_angle_deg(), other.initial_angle_deg());
}

#[test]
fn hq_quantization_matches_half_up_fixture_ticks() {
    let fixture: Value = serde_json::from_str(FIXTURE).unwrap();
    let angles = &fixture["angle_quantization"];
    let angle_inputs = floats(&angles["input_deg"]);
    assert_eq!(
        quantize_hq_angles_deg(&angle_inputs).unwrap(),
        floats(&angles["output_deg"])
    );
    for (input, expected) in angles["input_deg"]
        .as_array()
        .unwrap()
        .iter()
        .zip(angles["hq_ticks"].as_array().unwrap())
    {
        assert_eq!(
            u64::from(quantize_hq_angle_ticks(input.as_f64().unwrap()).unwrap()),
            expected.as_u64().unwrap()
        );
    }
    let distances = &fixture["distance_quantization"];
    let distance_inputs = floats(&distances["input_m"]);
    assert_eq!(
        quantize_hq_distances_m(&distance_inputs).unwrap(),
        floats(&distances["output_m"])
    );
    assert_eq!(HQ_DISTANCE_STEP_M, 0.00025);
    for (input, expected) in distances["input_m"]
        .as_array()
        .unwrap()
        .iter()
        .zip(distances["hq_ticks"].as_array().unwrap())
    {
        assert_eq!(
            quantize_hq_distance_ticks(input.as_f64().unwrap()).unwrap(),
            expected.as_u64().unwrap()
        );
    }
}

#[test]
fn hq_driver_decode_uses_wide_integer_arithmetic() {
    assert_eq!(decode_hq_angle_mdeg(0), 0);
    assert_eq!(decode_hq_angle_mdeg(16_384), 90_000);
    assert_eq!(decode_hq_angle_mdeg(u16::MAX), 359_995);
    assert_eq!(decode_hq_distance_mm(120_000), Ok(30_000));
    assert_eq!(
        decode_hq_distance_mm(u64::MAX),
        Err(SdkCompatibilityError::DistanceMillimeterOverflow)
    );
}

#[test]
fn scheduler_rejects_invalid_inputs_and_preserves_independent_state() {
    assert_eq!(validate_scan_point_limit(32_768.0, 1.0), Ok(()));
    assert!(matches!(
        SensorRotationScheduler::new("", 8.0, 2.0, 0.0),
        Err(RotationError::EmptySensorId)
    ));
    assert!(matches!(
        SensorRotationScheduler::new("a", 0.0, 2.0, 0.0),
        Err(RotationError::InvalidSampleRate)
    ));
    assert!(matches!(
        SensorRotationScheduler::new("a", 1.0, 2.0, 0.0),
        Err(RotationError::SampleRateBelowRotationRate)
    ));
    assert!(matches!(
        SensorRotationScheduler::new("a", 8.0, 2.0, 360.0),
        Err(RotationError::InvalidInitialAngle)
    ));
    assert!(SensorRotationScheduler::new("a", 32_768.0, 1.0, 0.0).is_ok());
    assert!(matches!(
        SensorRotationScheduler::new("a", f64::from_bits(32_768.0_f64.to_bits() + 1), 1.0, 0.0),
        Err(RotationError::ScanPointLimitExceeded)
    ));

    let mut first = SensorRotationScheduler::new("a", 8.0, 2.0, 0.0).unwrap();
    let mut second = SensorRotationScheduler::new("b", 8.0, 2.0, 0.0).unwrap();
    first.next_scan().unwrap();
    first.next_scan().unwrap();
    second.next_scan().unwrap();
    assert_eq!(first.next_scan_id(), 3);
    assert_eq!(second.next_scan_id(), 2);
    assert_eq!(first.sensor_id(), "a");
    assert_eq!(first.next_completion_elapsed_s(), Ok(1.5));
}

#[test]
fn scheduled_scan_rejects_non_monotonic_or_out_of_interval_times() {
    assert_eq!(
        ScheduledScan::new("a", 1, 0.0, 1.0, vec![0.0, 1.0], vec![0.5, 0.5]),
        Err(RotationError::NonMonotonicPointTime)
    );
    assert_eq!(
        ScheduledScan::new("a", 1, 0.0, 1.0, vec![0.0], vec![1.0]),
        Err(RotationError::PointAtOrAfterCompletion)
    );
    assert_eq!(
        ScheduledScan::new("a", 1, 1.0, 2.0, vec![0.0], vec![0.5]),
        Err(RotationError::PointBeforeRotation)
    );
}

#[test]
fn scan_and_sample_integer_ranges_fail_without_wrapping() {
    let mut scan_overflow = SensorRotationScheduler::new("a", 8.0, 2.0, 0.0).unwrap();
    scan_overflow.set_state_for_test(i64::MAX as u64, 0);
    assert!(matches!(
        scan_overflow.next_scan(),
        Err(RotationError::ScanIdExhausted)
    ));

    let mut sample_overflow = SensorRotationScheduler::new("a", 8.0, 2.0, 0.0).unwrap();
    sample_overflow.set_state_for_test(i64::MAX as u64 - 1, 0);
    assert!(matches!(
        sample_overflow.next_scan(),
        Err(RotationError::SampleIndexExhausted)
    ));

    assert!(matches!(
        SensorRotationScheduler::new("a", f64::MAX, 1.0, 0.0),
        Err(RotationError::ScanPointLimitExceeded)
    ));
}

#[test]
fn hq_input_domains_and_tick_boundaries_are_checked() {
    assert_eq!(
        quantize_hq_angle_ticks(f64::NAN),
        Err(SdkCompatibilityError::InvalidAngle)
    );
    assert_eq!(
        quantize_hq_angle_ticks(360.0),
        Err(SdkCompatibilityError::InvalidAngle)
    );
    assert_eq!(
        quantize_hq_distance_ticks(-0.1),
        Err(SdkCompatibilityError::InvalidDistance)
    );
    assert_eq!(
        quantize_hq_distance_ticks(f64::MAX),
        Err(SdkCompatibilityError::DistanceTickOverflow)
    );
    assert_eq!(quantize_hq_angle_ticks(HQ_ANGLE_STEP_DEG / 2.0), Ok(1));
}
