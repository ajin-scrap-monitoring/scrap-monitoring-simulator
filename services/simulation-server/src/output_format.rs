use serde::{Serialize, Serializer, ser::SerializeSeq};

use crate::scenario::{ScenarioModelSnapshot, ScenarioSnapshot, SurfaceSnapshot};

#[derive(Serialize)]
pub(crate) struct ScenarioDocument {
    elapsed_s: f64,
    surface_updated_at_s: f64,
    cycle_index: u64,
    phase: &'static str,
    phase_started_at_s: f64,
    phase_ends_at_s: f64,
    phase_duration_s: f64,
    rate_factor: f64,
    target_fill_ratio: f64,
    surface_fill_ratio: f64,
    surface_volume_m3: f64,
    current_inlet_index: Option<usize>,
}

impl From<&ScenarioSnapshot> for ScenarioDocument {
    fn from(snapshot: &ScenarioSnapshot) -> Self {
        Self {
            elapsed_s: snapshot.elapsed_s,
            surface_updated_at_s: snapshot.surface_updated_at_s,
            cycle_index: snapshot.cycle_index,
            phase: snapshot.phase.as_str(),
            phase_started_at_s: snapshot.phase_started_at_s,
            phase_ends_at_s: snapshot.phase_ends_at_s,
            phase_duration_s: snapshot.phase_duration_s,
            rate_factor: snapshot.rate_factor,
            target_fill_ratio: snapshot.target_fill_ratio,
            surface_fill_ratio: snapshot.surface_fill_ratio,
            surface_volume_m3: snapshot.surface_volume_m3,
            current_inlet_index: snapshot.current_inlet_index,
        }
    }
}

#[derive(Serialize)]
pub(crate) struct SceneSurfaceDocument<'a> {
    cell_size_m: f64,
    x_coordinates_m: &'a [f64],
    y_coordinates_m: &'a [f64],
    heights_m: HeightRows<'a>,
}

impl<'a> From<&'a SurfaceSnapshot> for SceneSurfaceDocument<'a> {
    fn from(surface: &'a SurfaceSnapshot) -> Self {
        Self {
            cell_size_m: surface.cell_size_m(),
            x_coordinates_m: surface.x_coordinates_m(),
            y_coordinates_m: surface.y_coordinates_m(),
            heights_m: HeightRows::new(surface),
        }
    }
}

pub(crate) fn validate_model_snapshot(
    snapshot: &ScenarioModelSnapshot,
) -> Result<(), &'static str> {
    let state = &snapshot.state;
    for value in [
        state.elapsed_s,
        state.surface_updated_at_s,
        state.phase_started_at_s,
        state.phase_ends_at_s,
        state.surface_volume_m3,
    ] {
        if !value.is_finite() || value < 0.0 {
            return Err("scenario non-negative values must be finite");
        }
    }
    for value in [state.phase_duration_s, state.rate_factor] {
        if !value.is_finite() || value <= 0.0 {
            return Err("scenario positive values must be finite");
        }
    }
    for value in [state.target_fill_ratio, state.surface_fill_ratio] {
        if !value.is_finite() || !(0.0..=1.0).contains(&value) {
            return Err("scenario ratios must be finite values from 0 through 1");
        }
    }

    let surface = &snapshot.surface;
    let (height, width) = surface.shape();
    if height < 2 || width < 2 {
        return Err("surface coordinates must contain at least 2 nodes per axis");
    }
    if !surface.cell_size_m().is_finite() || surface.cell_size_m() <= 0.0 {
        return Err("surface cell size must be finite and positive");
    }
    if !strictly_increasing_finite(surface.x_coordinates_m())
        || !strictly_increasing_finite(surface.y_coordinates_m())
    {
        return Err("surface coordinates must be finite and strictly increasing");
    }
    if surface.heights_m().len() != height.saturating_mul(width)
        || surface.heights_m().iter().any(|value| !value.is_finite())
    {
        return Err("surface heights must be finite and match the coordinate dimensions");
    }
    Ok(())
}

fn strictly_increasing_finite(values: &[f64]) -> bool {
    values.len() >= 2
        && values.iter().all(|value| value.is_finite())
        && values.windows(2).all(|pair| pair[0] < pair[1])
}

struct HeightRows<'a> {
    heights: &'a [f64],
    row_count: usize,
    column_count: usize,
}

impl<'a> HeightRows<'a> {
    fn new(surface: &'a SurfaceSnapshot) -> Self {
        let (row_count, column_count) = surface.shape();
        Self {
            heights: surface.heights_m(),
            row_count,
            column_count,
        }
    }
}

impl Serialize for HeightRows<'_> {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let mut rows = serializer.serialize_seq(Some(self.row_count))?;
        for row in self.heights.chunks_exact(self.column_count) {
            rows.serialize_element(row)?;
        }
        rows.end()
    }
}
