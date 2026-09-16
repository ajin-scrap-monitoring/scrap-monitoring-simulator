use std::path::Path;

use scrap_monitoring_simulation_server::{
    MAX_INLET_POSITIONS,
    configuration::{SimulatorInputs, load_simulator_inputs},
    measurement::SensorRotationScheduler,
    randomness::SIMULATION_MODEL_VERSION,
    scenario::{
        DEFAULT_ANGLE_OF_REPOSE_DEG, HeightField, MAX_EVENT_SNAPSHOT_BYTES, MAX_EVENTS_PER_ADVANCE,
        ScenarioPhase, ScenarioSettings, build_scenario_simulator,
    },
};
use serde_json::Value;
use sha2::{Digest, Sha256};

fn public_inputs() -> SimulatorInputs {
    load_simulator_inputs(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("config/simulation-server.v1.json"),
    )
    .unwrap()
}

fn scripted_inputs() -> SimulatorInputs {
    let mut inputs = public_inputs();
    let scenario = &mut inputs.simulator.scenario;
    scenario.mean_fill_duration_s = 4.25;
    scenario.fill_duration_factor_range = [1.0, 1.0];
    scenario.fill_rate_factor_range = [1.0, 1.0];
    scenario.collection_threshold_range = [0.6, 0.6];
    scenario.collection_duration_factor_range = [0.2, 0.2];
    scenario.collection_rate_factor_range = [1.0, 1.0];
    scenario.surface.update_interval_s = 0.5;
    scenario.surface.pile_spread_radius_m = 0.5;
    scenario.surface.roughness_height_range_m = [0.0, 0.0];
    inputs
}

fn number(value: &Value) -> f64 {
    value.as_f64().unwrap()
}

fn close(actual: f64, expected: f64, absolute: f64) {
    let tolerance = absolute.max(1e-12 * actual.abs().max(expected.abs()));
    assert!(
        actual.is_finite() && expected.is_finite() && (actual - expected).abs() <= tolerance,
        "actual={actual:.17e} expected={expected:.17e} tolerance={tolerance:.17e}"
    );
}

fn radial_material_second_moment_m2(surface: &HeightField, center_xy_m: [f64; 2]) -> f64 {
    let columns = surface.shape().1;
    let volume_m3 = surface.volume_m3();
    assert!(volume_m3 > 0.0);
    surface
        .heights_m()
        .iter()
        .enumerate()
        .map(|(index, height_m)| {
            let dx_m = surface.x_coordinates_m()[index % columns] - center_xy_m[0];
            let dy_m = surface.y_coordinates_m()[index / columns] - center_xy_m[1];
            surface.node_area_m2()[index]
                * (height_m - surface.floor_z_m())
                * (dx_m * dx_m + dy_m * dy_m)
        })
        .sum::<f64>()
        / volume_m3
}

fn maximum_neighbor_slope(surface: &HeightField) -> f64 {
    let (rows, columns) = surface.shape();
    let heights = surface.heights_m();
    let weights = surface.node_area_m2();
    let mut maximum = 0.0_f64;
    for y in 0..rows {
        for x in 0..columns {
            let first = y * columns + x;
            for (dy, dx) in [(0_isize, 1_isize), (1, 0), (1, 1), (1, -1)] {
                let Some(ny) = y.checked_add_signed(dy).filter(|ny| *ny < rows) else {
                    continue;
                };
                let Some(nx) = x.checked_add_signed(dx).filter(|nx| *nx < columns) else {
                    continue;
                };
                let second = ny * columns + nx;
                if weights[first] == 0.0 || weights[second] == 0.0 {
                    continue;
                }
                let distance_m = surface.cell_size_m() * (dx as f64).hypot(dy as f64);
                maximum = maximum.max((heights[first] - heights[second]).abs() / distance_m);
            }
        }
    }
    maximum
}

#[test]
fn scripted_scenario_matches_every_model_v1_state_and_surface() {
    let fixture: Value =
        serde_json::from_str(include_str!("fixtures/model-v1/scenario-scripted.json")).unwrap();
    assert_eq!(fixture["comparison_class"], "rng-independent");
    let mut simulator = build_scenario_simulator(&scripted_inputs()).unwrap();
    let shape = simulator.surface().shape();
    let volume_tolerance = simulator.surface().capacity_m3().max(1.0) * 1e-10;

    for expected in fixture["states"].as_array().unwrap() {
        let state = &expected["snapshot"];
        let actual = simulator
            .advance_to(number(&state["elapsed_s"]))
            .unwrap()
            .state;
        assert_eq!(actual.cycle_index, state["cycle_index"].as_u64().unwrap());
        assert_eq!(actual.phase.as_str(), state["phase"].as_str().unwrap());
        assert_eq!(
            actual.current_inlet_index.map(|value| value as u64),
            state["current_inlet_index"].as_u64()
        );
        for (actual, name, tolerance) in [
            (actual.elapsed_s, "elapsed_s", 1e-10),
            (actual.surface_updated_at_s, "surface_updated_at_s", 1e-10),
            (actual.phase_started_at_s, "phase_started_at_s", 1e-10),
            (actual.phase_ends_at_s, "phase_ends_at_s", 1e-10),
            (actual.phase_duration_s, "phase_duration_s", 1e-10),
            (actual.rate_factor, "rate_factor", 1e-10),
            (actual.target_fill_ratio, "target_fill_ratio", 1e-10),
            (actual.surface_fill_ratio, "surface_fill_ratio", 1e-10),
            (
                actual.surface_volume_m3,
                "surface_volume_m3",
                volume_tolerance,
            ),
        ] {
            close(actual, number(&state[name]), tolerance);
        }
        if let Some(rows) = expected["heights_m"].as_array() {
            assert_eq!(rows.len(), shape.0);
            for (actual_row, expected_row) in simulator
                .surface()
                .heights_m()
                .chunks_exact(shape.1)
                .zip(rows)
            {
                let expected_row = expected_row.as_array().unwrap();
                assert_eq!(actual_row.len(), expected_row.len());
                for (actual, expected) in actual_row.iter().zip(expected_row) {
                    close(*actual, number(expected), 1e-10);
                }
            }
        }
    }
}

#[test]
fn advance_returns_ordered_after_event_snapshots_with_immutable_heights() {
    let mut simulator = build_scenario_simulator(&scripted_inputs()).unwrap();
    let first_advance = simulator.advance_to(1.0).unwrap();
    assert_eq!(first_advance.events.len(), 2);
    assert_eq!(first_advance.events[0].elapsed_s, 0.5);
    assert_eq!(first_advance.events[1].elapsed_s, 1.0);
    assert!(
        first_advance
            .events
            .iter()
            .all(|event| event.surface_update_due)
    );
    assert!(
        first_advance
            .events
            .iter()
            .all(|event| !event.phase_transition_due)
    );
    let first_heights = first_advance.events[0].model.surface.heights_m().to_vec();
    assert_ne!(
        first_heights,
        first_advance.events[1].model.surface.heights_m()
    );
    assert_eq!(
        first_advance.events[0]
            .model
            .surface
            .x_coordinates_m()
            .as_ptr(),
        first_advance.events[1]
            .model
            .surface
            .x_coordinates_m()
            .as_ptr()
    );

    let transition = simulator.advance_to(4.25).unwrap();
    let final_event = transition.events.last().unwrap();
    assert_eq!(final_event.elapsed_s, 4.25);
    assert!(!final_event.surface_update_due);
    assert!(final_event.phase_transition_due);
    assert_eq!(final_event.model.state.phase, ScenarioPhase::Collecting);
    assert_eq!(final_event.model.state.current_inlet_index, None);
    assert_eq!(
        first_advance.events[0].model.surface.heights_m(),
        first_heights
    );
}

#[test]
fn same_model_seed_reproduces_nonflat_surface_and_different_seed_isolated_streams() {
    let inputs = public_inputs();
    let mut first = build_scenario_simulator(&inputs).unwrap();
    let mut second = build_scenario_simulator(&inputs).unwrap();
    let mut different_inputs = inputs.clone();
    different_inputs.simulator.seed += 1;
    let mut different = build_scenario_simulator(&different_inputs).unwrap();

    let first_state = first.advance_to(2.0).unwrap().state;
    let second_state = second.advance_to(2.0).unwrap().state;
    let different_state = different.advance_to(2.0).unwrap().state;
    assert_eq!(first_state, second_state);
    assert_eq!(first.surface().heights_m(), second.surface().heights_m());
    assert_ne!(first.surface().heights_m(), different.surface().heights_m());
    assert_ne!(first_state.phase_ends_at_s, different_state.phase_ends_at_s);
}

#[test]
fn model_version_one_scenario_has_an_exact_nonflat_vector() {
    assert_eq!(SIMULATION_MODEL_VERSION, 1);
    let mut inputs = public_inputs();
    inputs.simulator.scenario.mean_fill_duration_s = 86_400.0;
    inputs.simulator.scenario.surface.update_interval_s = 0.5;
    inputs.simulator.scenario.surface.pile_spread_radius_m = 0.5;
    inputs.simulator.scenario.surface.roughness_height_range_m = [-0.2, 0.2];
    inputs.simulator.scenario.surface.roughness_radius_range_m = [0.1, 0.3];
    let mut simulator = build_scenario_simulator(&inputs).unwrap();
    let advance = simulator.advance_to(2.0).unwrap();
    let mut hash = Sha256::new();
    for height in simulator.surface().heights_m() {
        hash.update(height.to_bits().to_be_bytes());
    }
    assert_eq!(
        advance.state.phase_ends_at_s.to_bits(),
        0x40f3_7ae8_6b7c_6af9
    );
    assert_eq!(
        advance.state.target_fill_ratio.to_bits(),
        0x3fee_3560_6f34_43ac
    );
    assert_eq!(
        advance.state.surface_volume_m3.to_bits(),
        0x3f6c_9c8b_d24e_311b
    );
    assert_eq!(advance.state.rate_factor.to_bits(), 0x3ff0_002f_90c0_1cdd);
    assert_eq!(
        hash.finalize().as_slice(),
        [
            54, 99, 197, 158, 152, 2, 196, 209, 34, 206, 55, 51, 124, 230, 72, 160, 58, 19, 52,
            148, 40, 72, 212, 207, 173, 17, 22, 113, 136, 192, 218, 189,
        ]
    );
    assert_eq!(
        advance
            .events
            .iter()
            .map(|event| event.elapsed_s.to_bits())
            .collect::<Vec<_>>(),
        [
            0x3fe0_0000_0000_0000,
            0x3ff0_0000_0000_0000,
            0x3ff8_0000_0000_0000,
            0x4000_0000_0000_0000,
        ]
    );
}

#[test]
fn local_scrap_roughness_remains_after_macro_slope_relaxation() {
    let public_surface = public_inputs().simulator.scenario.surface;
    assert_eq!(public_surface.pile_spread_radius_m, 0.9);
    assert_eq!(public_surface.roughness_height_range_m, [-0.45, 0.45]);
    assert_eq!(public_surface.roughness_radius_range_m, [0.3, 0.65]);
    let mut inputs = scripted_inputs();
    inputs.simulator.scenario.surface.roughness_height_range_m =
        public_surface.roughness_height_range_m;
    inputs.simulator.scenario.surface.roughness_radius_range_m =
        public_surface.roughness_radius_range_m;
    let mut simulator = build_scenario_simulator(&inputs).unwrap();

    simulator.advance_to(2.0).unwrap();

    let surface = simulator.surface();
    let columns = surface.shape().1;
    let heights = surface.heights_m();
    let maximum_neighbor_delta = heights
        .chunks_exact(columns)
        .flat_map(|row| row.windows(2).map(|pair| (pair[0] - pair[1]).abs()))
        .chain(
            (0..heights.len() - columns)
                .map(|index| (heights[index] - heights[index + columns]).abs()),
        )
        .fold(0.0_f64, f64::max);
    let macro_slope_limit = DEFAULT_ANGLE_OF_REPOSE_DEG.to_radians().tan()
        * inputs.simulator.scenario.surface.cell_size_m;

    assert!(maximum_neighbor_delta > macro_slope_limit);
}

#[test]
fn public_macro_pile_keeps_a_bounded_bell_concentration() {
    let mut configured_inputs = public_inputs();
    configured_inputs
        .simulator
        .scenario
        .surface
        .roughness_height_range_m = [0.0, 0.0];
    configured_inputs
        .simulator
        .scenario
        .inlet_switch_activation_ratio = 1.0;
    let inlet = configured_inputs.simulator.scenario.inlet_positions_xy_m[0];
    let mut broad_inputs = configured_inputs.clone();
    broad_inputs.simulator.scenario.surface.pile_spread_radius_m = 1.0;

    let mut configured = build_scenario_simulator(&configured_inputs).unwrap();
    let mut broad = build_scenario_simulator(&broad_inputs).unwrap();
    configured.advance_to(10.0).unwrap();
    broad.advance_to(10.0).unwrap();

    close(
        configured.surface().volume_m3(),
        broad.surface().volume_m3(),
        1e-10,
    );
    let configured_moment_m2 = radial_material_second_moment_m2(configured.surface(), inlet);
    let broad_moment_m2 = radial_material_second_moment_m2(broad.surface(), inlet);
    let concentration_gain = (broad_moment_m2 - configured_moment_m2) / broad_moment_m2;
    let configured_peak_m = configured
        .surface()
        .heights_m()
        .iter()
        .copied()
        .fold(f64::NEG_INFINITY, f64::max);
    let broad_peak_m = broad
        .surface()
        .heights_m()
        .iter()
        .copied()
        .fold(f64::NEG_INFINITY, f64::max);
    let peak_gain = (configured_peak_m - broad_peak_m) / broad_peak_m;
    let configured_maximum_slope = maximum_neighbor_slope(configured.surface());
    let repose_slope = DEFAULT_ANGLE_OF_REPOSE_DEG.to_radians().tan();

    assert!(
        (0.12..=0.14).contains(&concentration_gain),
        "radial concentration gain was {concentration_gain}"
    );
    assert!(
        (0.14..=0.18).contains(&peak_gain),
        "peak height gain was {peak_gain}"
    );
    assert!(
        configured_maximum_slope <= repose_slope + 1e-10,
        "macro slope {configured_maximum_slope} exceeded {repose_slope}"
    );
}

#[test]
fn public_environment_and_time_scaling_keep_distinct_owners() {
    let mut inputs = public_inputs();
    assert_eq!(inputs.simulator.scenario.mean_fill_duration_s, 600.0);
    let simulator = build_scenario_simulator(&inputs).unwrap();
    assert_eq!(simulator.surface().shape(), (23, 17));
    close(simulator.surface().surface_area_m2(), 14.76, 1e-10);
    close(simulator.surface().capacity_m3(), 147.6, 1e-10);

    inputs.simulator.scenario.mean_fill_duration_s = 3_600.0;
    let settings = ScenarioSettings::from_config(&inputs.simulator.scenario).unwrap();
    assert_eq!(settings.surface_update_interval_s(), 0.1);
    assert_eq!(settings.fill_rate_change_duration_s_range(), [12.5, 37.5]);
    assert_eq!(
        settings.collection_rate_change_duration_s_range(),
        [2.5, 7.5]
    );
}

#[test]
fn public_surface_updates_share_the_lidar_rotation_boundaries() {
    let inputs = public_inputs();
    let mut simulator = build_scenario_simulator(&inputs).unwrap();
    let mut lidar = SensorRotationScheduler::new("lidar", 10.0, 10.0, 0.0).unwrap();

    for index in 1..=12 {
        let scan = lidar.next_scan().unwrap();
        let surface_boundary = simulator.next_surface_event_elapsed_s().unwrap();
        assert_eq!(
            surface_boundary.to_bits(),
            scan.completed_at_s().to_bits(),
            "boundary {index}"
        );
        let advance = simulator.advance_to(scan.completed_at_s()).unwrap();
        assert_eq!(advance.events.len(), 1);
        assert_eq!(
            advance.events[0].elapsed_s.to_bits(),
            scan.completed_at_s().to_bits()
        );
    }
}

#[test]
fn invalid_or_unbounded_advance_is_transactional() {
    let mut simulator = build_scenario_simulator(&scripted_inputs()).unwrap();
    let initial = simulator.snapshot().unwrap();
    for invalid in [f64::NAN, f64::INFINITY, -1.0] {
        assert!(simulator.advance_to(invalid).is_err());
        assert_eq!(simulator.snapshot().unwrap(), initial);
    }
    let excessive = (MAX_EVENTS_PER_ADVANCE as f64 + 1.0) * 0.5;
    assert!(simulator.advance_to(excessive).is_err());
    assert_eq!(simulator.snapshot().unwrap(), initial);

    let mut large_inputs = scripted_inputs();
    large_inputs.simulator.scenario.surface.cell_size_m = 0.05;
    let mut large = build_scenario_simulator(&large_inputs).unwrap();
    let byte_limited_events =
        MAX_EVENT_SNAPSHOT_BYTES / std::mem::size_of_val(large.surface().heights_m());
    assert!(byte_limited_events < MAX_EVENTS_PER_ADVANCE);
    let initial = large.snapshot().unwrap();
    let excessive = (byte_limited_events as f64 + 1.0) * 0.5;
    assert!(large.advance_to(excessive).is_err());
    assert_eq!(large.snapshot().unwrap(), initial);
}

#[test]
fn inlet_and_phase_schedule_errors_are_rejected_before_mutation() {
    let mut inputs = scripted_inputs();
    inputs.simulator.scenario.inlet_positions_xy_m[0] = [100.0, 100.0];
    assert!(build_scenario_simulator(&inputs).is_err());

    let mut inputs = scripted_inputs();
    inputs.simulator.scenario.inlet_positions_xy_m[1] =
        inputs.simulator.scenario.inlet_positions_xy_m[0];
    assert!(build_scenario_simulator(&inputs).is_err());

    let mut inputs = scripted_inputs();
    inputs.simulator.scenario.inlet_positions_xy_m = (0..=MAX_INLET_POSITIONS)
        .map(|index| [index as f64, 0.0])
        .collect();
    assert!(build_scenario_simulator(&inputs).is_err());

    let mut inputs = scripted_inputs();
    inputs.simulator.scenario.mean_fill_duration_s = f64::MAX;
    inputs.simulator.scenario.fill_duration_factor_range = [1.0, 1.0];
    assert!(build_scenario_simulator(&inputs).is_err());
}
