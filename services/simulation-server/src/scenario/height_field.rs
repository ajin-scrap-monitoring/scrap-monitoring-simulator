use std::sync::Arc;

use crate::geometry::{GeometryError, Polygon2, Result, Vec2, sum};

use super::grid::{CellCoverage, Grid, filled};

const VOLUME_TOLERANCE: f64 = 1e-12;
const KERNEL_WEIGHT_FLOOR: f64 = 1e-12;
pub const DEFAULT_ANGLE_OF_REPOSE_DEG: f64 = 35.0;
pub const DEFAULT_SLOPE_RELAXATION_MAX_ITERATIONS: usize = 32;
pub const MAX_SLOPE_RELAXATION_ITERATIONS: usize = 256;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct VolumeChange {
    pub requested_m3: f64,
    pub applied_m3: f64,
}

impl VolumeChange {
    pub fn unapplied_m3(self) -> f64 {
        (self.requested_m3 - self.applied_m3).max(0.0)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RoughnessChange {
    pub requested_peak_delta_m: f64,
    pub applied_peak_delta_m: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SlopeRelaxation {
    pub iterations: usize,
    pub moved_volume_m3: f64,
    pub remaining_excess_height_m: f64,
}

/// Mutable heights in y-major order; immutable grid geometry is shared by clones.
#[derive(Clone, Debug)]
pub struct HeightField {
    grid: Arc<Grid>,
    heights: Vec<f64>,
}

/// A frozen surface; subsequent simulation updates cannot change its heights.
#[derive(Clone, Debug)]
pub struct SurfaceSnapshot {
    grid: Arc<Grid>,
    heights: Arc<[f64]>,
}

impl SurfaceSnapshot {
    pub fn heights_m(&self) -> &[f64] {
        &self.heights
    }
    pub fn x_coordinates_m(&self) -> &[f64] {
        &self.grid.x
    }
    pub fn y_coordinates_m(&self) -> &[f64] {
        &self.grid.y
    }
    pub fn node_area_m2(&self) -> &[f64] {
        &self.grid.weights
    }
    pub fn cell_coverage(&self) -> &[CellCoverage] {
        &self.grid.coverage
    }
    pub fn shape(&self) -> (usize, usize) {
        (self.grid.y.len(), self.grid.x.len())
    }
    pub fn cell_size_m(&self) -> f64 {
        self.grid.cell_size
    }
    pub fn floor_z_m(&self) -> f64 {
        self.grid.floor
    }
    pub fn top_z_m(&self) -> f64 {
        self.grid.top
    }
    pub fn boundary(&self) -> &Polygon2 {
        &self.grid.boundary
    }
    pub fn capacity_m3(&self) -> f64 {
        self.grid.capacity
    }
    pub fn volume_m3(&self) -> f64 {
        volume(&self.grid, &self.heights)
    }
    pub fn height_at(&self, point: Vec2) -> Result<f64> {
        self.grid.height_at(&self.heights, point)
    }
    pub(crate) fn shares_grid_with(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.grid, &other.grid)
    }
}

impl HeightField {
    pub fn new(boundary: Polygon2, floor_z_m: f64, top_z_m: f64, cell_size_m: f64) -> Result<Self> {
        let grid = Arc::new(Grid::new(boundary, floor_z_m, top_z_m, cell_size_m)?);
        let heights = filled(grid.weights.len(), floor_z_m)?;
        Ok(Self { grid, heights })
    }

    pub fn boundary(&self) -> &Polygon2 {
        &self.grid.boundary
    }
    pub fn floor_z_m(&self) -> f64 {
        self.grid.floor
    }
    pub fn top_z_m(&self) -> f64 {
        self.grid.top
    }
    pub fn cell_size_m(&self) -> f64 {
        self.grid.cell_size
    }
    pub fn shape(&self) -> (usize, usize) {
        (self.grid.y.len(), self.grid.x.len())
    }
    pub fn surface_area_m2(&self) -> f64 {
        self.grid.boundary.signed_area().abs()
    }
    pub fn capacity_m3(&self) -> f64 {
        self.grid.capacity
    }
    pub fn volume_m3(&self) -> f64 {
        volume(&self.grid, &self.heights)
    }
    pub fn fill_ratio(&self) -> f64 {
        (self.volume_m3() / self.capacity_m3()).clamp(0.0, 1.0)
    }
    pub fn heights_m(&self) -> &[f64] {
        &self.heights
    }
    pub fn x_coordinates_m(&self) -> &[f64] {
        &self.grid.x
    }
    pub fn y_coordinates_m(&self) -> &[f64] {
        &self.grid.y
    }
    pub fn node_area_m2(&self) -> &[f64] {
        &self.grid.weights
    }
    pub fn cell_coverage(&self) -> &[CellCoverage] {
        &self.grid.coverage
    }

    pub fn surface_snapshot(&self) -> Result<SurfaceSnapshot> {
        let mut heights = filled(self.heights.len(), 0.0)?;
        heights.copy_from_slice(&self.heights);
        Ok(SurfaceSnapshot {
            grid: Arc::clone(&self.grid),
            heights: heights.into(),
        })
    }

    pub fn height_at(&self, point: Vec2) -> Result<f64> {
        self.grid.height_at(&self.heights, point)
    }

    pub fn mean_height_within(&self, center: Vec2, radius_m: f64) -> Result<f64> {
        self.require_center_radius(center, radius_m)?;
        let mut weights = filled(self.heights.len(), 0.0)?;
        for (index, weight) in weights.iter_mut().enumerate() {
            let (dx, dy) = self.offset(index, center);
            if dx * dx + dy * dy <= radius_m * radius_m {
                *weight = self.grid.weights[index];
            }
        }
        let area = sum(weights.iter().copied());
        if area == 0.0 {
            return self.height_at(center);
        }
        Ok(sum(weights
            .iter()
            .zip(&self.heights)
            .map(|(weight, height)| weight * height))
            / area)
    }

    pub fn add_volume(
        &mut self,
        volume_m3: f64,
        center: Vec2,
        spread_radius_m: f64,
    ) -> Result<VolumeChange> {
        require_volume(volume_m3)?;
        let profile = self.local_profile(center, spread_radius_m)?;
        self.change_volume(volume_m3, &profile, true)
    }

    pub fn remove_volume(
        &mut self,
        volume_m3: f64,
        center: Vec2,
        spread_radius_m: f64,
    ) -> Result<VolumeChange> {
        require_volume(volume_m3)?;
        let profile = self.local_profile(center, spread_radius_m)?;
        self.change_volume(volume_m3, &profile, false)
    }

    pub fn remove_volume_uniformly(&mut self, volume_m3: f64) -> Result<VolumeChange> {
        require_volume(volume_m3)?;
        let mut profile = filled(self.heights.len(), 0.0)?;
        for (value, weight) in profile.iter_mut().zip(&self.grid.weights) {
            *value = if *weight > 0.0 { 1.0 } else { 0.0 };
        }
        self.change_volume(volume_m3, &profile, false)
    }

    fn change_volume(
        &mut self,
        requested: f64,
        profile: &[f64],
        adding: bool,
    ) -> Result<VolumeChange> {
        let mut available = filled(self.heights.len(), 0.0)?;
        for (value, height) in available.iter_mut().zip(&self.heights) {
            *value = if adding {
                self.grid.top - height
            } else {
                height - self.grid.floor
            };
        }
        let delta = solve_height_delta(profile, &available, &self.grid.weights, requested)?;
        let applied = sum(self
            .grid
            .weights
            .iter()
            .zip(&delta)
            .map(|(weight, delta)| weight * delta));
        if !applied.is_finite() || applied < 0.0 || applied - requested > self.volume_tolerance() {
            return Err(GeometryError::Numerical(
                "surface update did not preserve the requested volume",
            ));
        }
        for (height, delta) in self.heights.iter_mut().zip(delta) {
            *height = if adding {
                (*height + delta).min(self.grid.top)
            } else {
                (*height - delta).max(self.grid.floor)
            };
        }
        Ok(VolumeChange {
            requested_m3: requested,
            applied_m3: requested.min(applied),
        })
    }

    pub fn apply_local_roughness(
        &mut self,
        center: Vec2,
        radius_m: f64,
        peak_delta_m: f64,
    ) -> Result<RoughnessChange> {
        self.require_center_radius(center, radius_m)?;
        if !peak_delta_m.is_finite() {
            return Err(GeometryError::Invalid(
                "roughness peak delta must be finite",
            ));
        }
        let unchanged = RoughnessChange {
            requested_peak_delta_m: peak_delta_m,
            applied_peak_delta_m: 0.0,
        };
        if peak_delta_m == 0.0 {
            return Ok(unchanged);
        }
        let mut support = filled(self.heights.len(), false)?;
        let mut profile = filled(self.heights.len(), 0.0)?;
        for (index, supported) in support.iter_mut().enumerate() {
            let (dx, dy) = self.offset(index, center);
            let squared_radius = (dx / radius_m).powi(2) + (dy / radius_m).powi(2);
            *supported = squared_radius < 1.0 && self.grid.weights[index] > 0.0;
            if *supported {
                profile[index] = (1.0 - squared_radius).powi(2);
            }
        }
        if support.iter().filter(|supported| **supported).count() < 2 {
            return Ok(unchanged);
        }
        let weighted_mean = sum(profile
            .iter()
            .zip(&self.grid.weights)
            .map(|(profile, weight)| profile * weight))
            / sum(support
                .iter()
                .zip(&self.grid.weights)
                .filter_map(|(supported, weight)| supported.then_some(*weight)));
        for (value, supported) in profile.iter_mut().zip(support) {
            if supported {
                *value -= weighted_mean;
            }
        }
        let positive_peak = profile.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let negative_peak = profile.iter().copied().fold(f64::INFINITY, f64::min);
        if positive_peak <= 0.0 || negative_peak >= 0.0 {
            return Ok(unchanged);
        }
        let amplitude = peak_delta_m / positive_peak;
        let mut scale = 1.0_f64;
        for (delta, height) in profile.iter_mut().zip(&self.heights) {
            *delta *= amplitude;
            if !delta.is_finite() {
                return Err(GeometryError::Numerical("roughness delta must be finite"));
            }
            if *delta > 0.0 {
                scale = scale.min((self.grid.top - height) / *delta);
            }
            if *delta < 0.0 {
                scale = scale.min((height - self.grid.floor) / -*delta);
            }
        }
        scale = scale.clamp(0.0, 1.0);
        for delta in &mut profile {
            *delta *= scale;
        }
        let volume_delta = sum(self
            .grid
            .weights
            .iter()
            .zip(&profile)
            .map(|(weight, delta)| weight * delta));
        if !volume_delta.is_finite() || volume_delta.abs() > self.volume_tolerance() {
            return Err(GeometryError::Numerical(
                "local roughness did not preserve surface volume",
            ));
        }
        for (height, delta) in self.heights.iter_mut().zip(profile) {
            *height = (*height + delta).clamp(self.grid.floor, self.grid.top);
        }
        Ok(RoughnessChange {
            requested_peak_delta_m: peak_delta_m,
            applied_peak_delta_m: peak_delta_m * scale,
        })
    }

    pub fn relax_slopes(&mut self) -> Result<SlopeRelaxation> {
        self.relax_slopes_with(
            DEFAULT_ANGLE_OF_REPOSE_DEG,
            DEFAULT_SLOPE_RELAXATION_MAX_ITERATIONS,
        )
    }

    pub fn relax_slopes_with(
        &mut self,
        angle_of_repose_deg: f64,
        max_iterations: usize,
    ) -> Result<SlopeRelaxation> {
        if !angle_of_repose_deg.is_finite()
            || angle_of_repose_deg <= 0.0
            || angle_of_repose_deg >= 90.0
        {
            return Err(GeometryError::Invalid(
                "angle of repose must be finite and between 0 and 90 degrees",
            ));
        }
        if max_iterations == 0 {
            return Err(GeometryError::Invalid(
                "slope relaxation iterations must be positive",
            ));
        }
        if max_iterations > MAX_SLOPE_RELAXATION_ITERATIONS {
            return Err(GeometryError::Allocation(
                "slope relaxation exceeds MAX_SLOPE_RELAXATION_ITERATIONS",
            ));
        }
        let initial_volume = self.volume_m3();
        let maximum_slope = angle_of_repose_deg.to_radians().tan();
        let mut moved_volume = 0.0;
        let mut iterations = 0;
        for _ in 0..max_iterations {
            let moved = self.relax_neighbor_pairs(maximum_slope)?;
            moved_volume += moved;
            iterations += 1;
            if moved <= self.volume_tolerance() {
                break;
            }
        }
        for height in &mut self.heights {
            *height = height.clamp(self.grid.floor, self.grid.top);
        }
        if (self.volume_m3() - initial_volume).abs() > self.volume_tolerance() {
            return Err(GeometryError::Numerical(
                "slope relaxation did not preserve surface volume",
            ));
        }
        let pairs = &self.grid.neighbors;
        let excess = (0..pairs.first.len())
            .map(|index| {
                (self.heights[pairs.first[index]] - self.heights[pairs.second[index]]).abs()
                    - maximum_slope * pairs.distance[index]
            })
            .fold(0.0_f64, f64::max);
        Ok(SlopeRelaxation {
            iterations,
            moved_volume_m3: moved_volume,
            remaining_excess_height_m: excess,
        })
    }

    fn relax_neighbor_pairs(&mut self, maximum_slope: f64) -> Result<f64> {
        let pairs = &self.grid.neighbors;
        let mut transfers = Vec::new();
        transfers
            .try_reserve_exact(pairs.first.len())
            .map_err(|_| GeometryError::Allocation("slope transfer allocation failed"))?;
        for index in 0..pairs.first.len() {
            let first = pairs.first[index];
            let second = pairs.second[index];
            let difference = self.heights[first] - self.heights[second];
            let excess = difference.abs() - maximum_slope * pairs.distance[index];
            if excess <= 0.0 {
                continue;
            }
            let (source, destination) = if difference > 0.0 {
                (first, second)
            } else {
                (second, first)
            };
            let volume =
                excess / (1.0 / self.grid.weights[source] + 1.0 / self.grid.weights[destination]);
            if !volume.is_finite() {
                return Err(GeometryError::Numerical("slope transfer must be finite"));
            }
            transfers.push(Transfer {
                source,
                destination,
                volume,
                excess,
            });
        }
        if transfers.is_empty() {
            return Ok(0.0);
        }
        let source_scales = self.transfer_scales(&transfers, true)?;
        let destination_scales = self.transfer_scales(&transfers, false)?;
        for transfer in &mut transfers {
            transfer.volume *=
                source_scales[transfer.source].min(destination_scales[transfer.destination]);
        }
        let mut changes = filled(self.heights.len(), 0.0)?;
        // Match the reference's two ordered scatter passes, before changing any height.
        for transfer in &transfers {
            changes[transfer.source] -= transfer.volume;
        }
        for transfer in &transfers {
            changes[transfer.destination] += transfer.volume;
        }
        for ((height, weight), change) in
            self.heights.iter_mut().zip(&self.grid.weights).zip(changes)
        {
            if *weight > 0.0 {
                *height += change / weight;
            }
        }
        Ok(sum(transfers.iter().map(|transfer| transfer.volume)))
    }

    fn transfer_scales(&self, transfers: &[Transfer], source: bool) -> Result<Vec<f64>> {
        let mut requested = filled(self.heights.len(), 0.0)?;
        let mut excess = filled(self.heights.len(), 0.0_f64)?;
        for transfer in transfers {
            let index = if source {
                transfer.source
            } else {
                transfer.destination
            };
            requested[index] += transfer.volume;
            excess[index] = excess[index].max(transfer.excess);
        }
        for (index, scale) in requested.iter_mut().enumerate() {
            if *scale > 0.0 {
                let available_height = if source {
                    self.heights[index] - self.grid.floor
                } else {
                    self.grid.top - self.heights[index]
                };
                let allowed = (available_height * self.grid.weights[index])
                    .min(excess[index] * self.grid.weights[index]);
                *scale = (allowed / *scale).min(1.0);
            } else {
                *scale = 1.0;
            }
        }
        Ok(requested)
    }

    fn local_profile(&self, center: Vec2, radius: f64) -> Result<Vec<f64>> {
        self.require_center_radius(center, radius)?;
        let mut profile = filled(self.heights.len(), 0.0)?;
        for (index, value) in profile.iter_mut().enumerate() {
            if self.grid.weights[index] > 0.0 {
                let (dx, dy) = self.offset(index, center);
                let squared = (dx / radius).powi(2) + (dy / radius).powi(2);
                *value = (-0.5 * squared).exp().max(KERNEL_WEIGHT_FLOOR);
            }
        }
        Ok(profile)
    }

    fn offset(&self, index: usize, center: Vec2) -> (f64, f64) {
        (
            self.grid.x[index % self.grid.x.len()] - center.x(),
            self.grid.y[index / self.grid.x.len()] - center.y(),
        )
    }

    fn require_center_radius(&self, center: Vec2, radius: f64) -> Result<()> {
        if !self.grid.boundary.contains(center)? {
            return Err(GeometryError::Invalid(
                "surface operation center must lie inside the boundary",
            ));
        }
        if !radius.is_finite() || radius <= 0.0 {
            return Err(GeometryError::Invalid(
                "surface radius must be finite and positive",
            ));
        }
        Ok(())
    }

    fn volume_tolerance(&self) -> f64 {
        self.capacity_m3().max(1.0) * VOLUME_TOLERANCE
    }
}

struct Transfer {
    source: usize,
    destination: usize,
    volume: f64,
    excess: f64,
}

fn volume(grid: &Grid, heights: &[f64]) -> f64 {
    sum(grid
        .weights
        .iter()
        .zip(heights)
        .map(|(weight, height)| weight * (height - grid.floor)))
}

fn require_volume(value: f64) -> Result<()> {
    if !value.is_finite() || value < 0.0 {
        return Err(GeometryError::Invalid(
            "volume must be finite and non-negative",
        ));
    }
    Ok(())
}

fn solve_height_delta(
    profile: &[f64],
    available: &[f64],
    weights: &[f64],
    requested: f64,
) -> Result<Vec<f64>> {
    let mut delta = filled(available.len(), 0.0)?;
    if requested == 0.0 {
        return Ok(delta);
    }
    let capacity = sum(weights
        .iter()
        .zip(available)
        .map(|(weight, height)| weight * height));
    if requested >= capacity - VOLUME_TOLERANCE {
        delta.copy_from_slice(available);
        return Ok(delta);
    }
    let mut order = Vec::new();
    order
        .try_reserve_exact(available.len())
        .map_err(|_| GeometryError::Allocation("surface breakpoint allocation failed"))?;
    order.extend(
        (0..available.len()).filter(|index| {
            profile[*index] > 0.0 && available[*index] > 0.0 && weights[*index] > 0.0
        }),
    );
    order.sort_unstable_by(|left, right| {
        (available[*left] / profile[*left])
            .total_cmp(&(available[*right] / profile[*right]))
            .then(left.cmp(right))
    });
    let mut scale = 0.0;
    let mut accumulated = 0.0;
    // Suffix slopes avoid subtracting a saturated dominant kernel weight from a
    // much smaller remaining tail, which would amplify cancellation errors.
    let mut slopes = filled(order.len() + 1, 0.0)?;
    let mut total = 0.0_f64;
    let mut correction = 0.0;
    for position in (0..order.len()).rev() {
        let index = order[position];
        let value = weights[index] * profile[index];
        let next = total + value;
        correction += if total.abs() >= value.abs() {
            (total - next) + value
        } else {
            (value - next) + total
        };
        total = next;
        slopes[position] = total + correction;
    }
    let mut solved = false;
    for (position, index) in order.into_iter().enumerate() {
        let slope = slopes[position];
        let breakpoint = available[index] / profile[index];
        let next_volume = accumulated + (breakpoint - scale) * slope;
        if next_volume >= requested {
            scale += (requested - accumulated) / slope;
            solved = true;
            break;
        }
        accumulated = next_volume;
        scale = breakpoint;
    }
    if !solved {
        delta.copy_from_slice(available);
        return Ok(delta);
    }
    if !scale.is_finite() || scale < 0.0 {
        return Err(GeometryError::Numerical(
            "surface update scale must be finite and non-negative",
        ));
    }
    for ((delta, profile), available) in delta.iter_mut().zip(profile).zip(available) {
        *delta = (scale * profile).min(*available);
    }
    Ok(delta)
}
