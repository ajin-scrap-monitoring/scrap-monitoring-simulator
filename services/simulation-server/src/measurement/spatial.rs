//! Half-open spatial distortion events shared by all sensor scans.

use std::sync::Arc;

use crate::{
    geometry::{Polygon2, Vec2},
    randomness::{ModelRng, RandomSource, StreamScope},
    rate_profile::SmoothRateProfile,
    scenario::{ActiveScenarioPhase, SurfaceSnapshot},
};

use super::{
    EnvironmentScene, MeasurementError, Result, TimedReferenceScan,
    reference::ReferenceSpatialMetadata, require_distance_bounds,
};

const TIME_TOLERANCE_S: f64 = 1e-12;
const HEIGHT_TOLERANCE_M: f64 = 1e-12;
const MAX_EVENTS_PER_ADVANCE: usize = 100_000;
const MAX_FALLING_CENTER_ATTEMPTS: usize = 32;
const MAX_COLLECTION_PATH_ATTEMPTS: usize = 64;
const MAX_COLLECTION_POINT_ATTEMPTS: usize = 16;
const MAX_VOID_EVENT_ATTEMPTS: usize = 8;
const MAX_VOID_CENTER_ATTEMPTS: usize = 32;

#[derive(Clone, Debug, PartialEq)]
pub struct FallingMaterialSettings {
    pub event_rate_per_s: f64,
    pub radius_m_range: [f64; 2],
    pub duration_s_range: [f64; 2],
    pub distance_reduction_m_range: [f64; 2],
    pub inlet_positions: Vec<Vec2>,
    pub placement_radius_m: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct VoidSettings {
    pub surface_area_ratio: f64,
    pub radius_m_range: [f64; 2],
    pub duration_s_range: [f64; 2],
    pub cover_height_increase_m: f64,
    pub distance_increase_m_range: [f64; 2],
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CollectionOcclusionSettings {
    pub event_interval_s_range: [f64; 2],
    pub radius_m_range: [f64; 2],
    pub duration_s_range: [f64; 2],
    pub distance_reduction_m_range: [f64; 2],
}

#[derive(Clone, Copy, Debug)]
pub enum DistortionPhase<'a> {
    Filling {
        cycle_index: u64,
        started_at_s: f64,
        ends_at_s: f64,
        inlet_index: usize,
        rate_profile: &'a SmoothRateProfile,
    },
    Collecting {
        cycle_index: u64,
        started_at_s: f64,
        ends_at_s: f64,
    },
}

impl<'a> From<ActiveScenarioPhase<'a>> for DistortionPhase<'a> {
    fn from(value: ActiveScenarioPhase<'a>) -> Self {
        match value {
            ActiveScenarioPhase::Filling {
                cycle_index,
                started_at_s,
                ends_at_s,
                inlet_index,
                rate_profile,
            } => Self::Filling {
                cycle_index,
                started_at_s,
                ends_at_s,
                inlet_index,
                rate_profile,
            },
            ActiveScenarioPhase::Collecting {
                cycle_index,
                started_at_s,
                ends_at_s,
            } => Self::Collecting {
                cycle_index,
                started_at_s,
                ends_at_s,
            },
        }
    }
}

impl DistortionPhase<'_> {
    fn started_at_s(self) -> f64 {
        match self {
            Self::Filling { started_at_s, .. } | Self::Collecting { started_at_s, .. } => {
                started_at_s
            }
        }
    }

    fn ends_at_s(self) -> f64 {
        match self {
            Self::Filling { ends_at_s, .. } | Self::Collecting { ends_at_s, .. } => ends_at_s,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct DistortionInterval<'a> {
    pub started_at_s: f64,
    pub ends_at_s: f64,
    pub phase: DistortionPhase<'a>,
    pub surface: &'a SurfaceSnapshot,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FallingMaterialEvent {
    pub cycle_index: u64,
    pub started_at_s: f64,
    pub ends_at_s: f64,
    pub center: Vec2,
    pub radius_m: f64,
    pub distance_reduction_m: f64,
}

impl FallingMaterialEvent {
    pub fn validate(self) -> Result<Self> {
        require_event(self.started_at_s, self.ends_at_s, self.radius_m)?;
        require_positive(self.distance_reduction_m, "falling distance reduction")?;
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct VoidEvent {
    pub cycle_index: u64,
    pub started_at_s: f64,
    pub expires_at_s: f64,
    pub ends_at_s: f64,
    pub center: Vec2,
    pub surface_height_at_start_m: f64,
    pub radius_m: f64,
    pub distance_increase_m: f64,
}

impl VoidEvent {
    pub fn validate(self) -> Result<Self> {
        require_event(self.started_at_s, self.ends_at_s, self.radius_m)?;
        if !self.expires_at_s.is_finite()
            || self.expires_at_s <= self.started_at_s
            || self.ends_at_s > self.expires_at_s
            || !self.surface_height_at_start_m.is_finite()
        {
            return Err(MeasurementError::Invalid("invalid void event lifetime"));
        }
        require_positive(self.distance_increase_m, "void distance increase")?;
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CollectionOcclusionEvent {
    pub cycle_index: u64,
    pub started_at_s: f64,
    pub ends_at_s: f64,
    pub start_center: Vec2,
    pub end_center: Vec2,
    pub radius_m: f64,
    pub distance_reduction_m: f64,
}

impl CollectionOcclusionEvent {
    pub fn validate(self) -> Result<Self> {
        require_event(self.started_at_s, self.ends_at_s, self.radius_m)?;
        require_positive(self.distance_reduction_m, "collection distance reduction")?;
        Ok(self)
    }

    pub fn center_at(self, elapsed_s: f64) -> Result<Vec2> {
        if !elapsed_s.is_finite() || elapsed_s < self.started_at_s || elapsed_s > self.ends_at_s {
            return Err(MeasurementError::Invalid(
                "collection time must be within the event",
            ));
        }
        let progress = (elapsed_s - self.started_at_s) / (self.ends_at_s - self.started_at_s);
        Ok(Vec2::new(
            self.start_center.x() + progress * (self.end_center.x() - self.start_center.x()),
            self.start_center.y() + progress * (self.end_center.y() - self.start_center.y()),
        )?)
    }
}

#[derive(Clone, Debug)]
pub struct SpatialDistortionResolver {
    static_scene: EnvironmentScene,
    falling: Arc<Vec<FallingMaterialEvent>>,
    voids: Arc<Vec<VoidEvent>>,
    collection: Arc<Vec<CollectionOcclusionEvent>>,
    advanced_to_s: f64,
    retained_from_s: f64,
}

impl SpatialDistortionResolver {
    pub fn new(static_scene: EnvironmentScene) -> Self {
        Self {
            static_scene,
            falling: Arc::new(Vec::new()),
            voids: Arc::new(Vec::new()),
            collection: Arc::new(Vec::new()),
            advanced_to_s: 0.0,
            retained_from_s: 0.0,
        }
    }

    pub fn advanced_to_s(&self) -> f64 {
        self.advanced_to_s
    }

    pub fn retained_from_s(&self) -> f64 {
        self.retained_from_s
    }

    pub fn falling_events(&self) -> &[FallingMaterialEvent] {
        self.falling.as_slice()
    }
    pub fn void_events(&self) -> &[VoidEvent] {
        self.voids.as_slice()
    }
    pub fn collection_events(&self) -> &[CollectionOcclusionEvent] {
        self.collection.as_slice()
    }

    pub(crate) fn retained_event_count(&self) -> usize {
        self.falling
            .len()
            .saturating_add(self.voids.len())
            .saturating_add(self.collection.len())
    }

    pub fn advance_with_events(
        &mut self,
        elapsed_s: f64,
        falling: impl IntoIterator<Item = FallingMaterialEvent>,
        voids: impl IntoIterator<Item = VoidEvent>,
        collection: impl IntoIterator<Item = CollectionOcclusionEvent>,
    ) -> Result<()> {
        let mut candidate = self.clone();
        candidate.advance_with_events_inner(elapsed_s, falling, voids, collection)?;
        *self = candidate;
        Ok(())
    }

    fn advance_with_events_inner(
        &mut self,
        elapsed_s: f64,
        falling: impl IntoIterator<Item = FallingMaterialEvent>,
        voids: impl IntoIterator<Item = VoidEvent>,
        collection: impl IntoIterator<Item = CollectionOcclusionEvent>,
    ) -> Result<()> {
        if !elapsed_s.is_finite() || elapsed_s < self.advanced_to_s {
            return Err(MeasurementError::Invalid(
                "spatial distortion time must be finite and monotonic",
            ));
        }
        let mut new_event_count = 0_usize;
        let mut new_falling = Vec::new();
        for event in falling {
            let event = event.validate()?;
            if event.started_at_s + TIME_TOLERANCE_S < self.advanced_to_s
                || event.started_at_s >= elapsed_s
            {
                return Err(MeasurementError::Invalid(
                    "new falling events must start in the advanced interval",
                ));
            }
            reserve_spatial_event(&mut new_falling, &mut new_event_count)?;
            new_falling.push(event);
        }
        let mut new_voids = Vec::new();
        for event in voids {
            let event = event.validate()?;
            if event.started_at_s + TIME_TOLERANCE_S < self.advanced_to_s
                || event.started_at_s > elapsed_s
            {
                return Err(MeasurementError::Invalid(
                    "new void events must start in the advanced interval",
                ));
            }
            reserve_spatial_event(&mut new_voids, &mut new_event_count)?;
            new_voids.push(event);
        }
        let mut new_collection = Vec::new();
        for event in collection {
            let event = event.validate()?;
            if event.started_at_s + TIME_TOLERANCE_S < self.advanced_to_s
                || event.started_at_s >= elapsed_s
            {
                return Err(MeasurementError::Invalid(
                    "new collection events must start in the advanced interval",
                ));
            }
            reserve_spatial_event(&mut new_collection, &mut new_event_count)?;
            new_collection.push(event);
        }
        if !new_falling.is_empty() {
            let falling = Arc::make_mut(&mut self.falling);
            falling
                .try_reserve(new_falling.len())
                .map_err(|_| MeasurementError::Exhausted("spatial event allocation failed"))?;
            falling.extend(new_falling);
            falling.sort_by(|left, right| left.started_at_s.total_cmp(&right.started_at_s));
        }
        if !new_voids.is_empty() {
            let voids = Arc::make_mut(&mut self.voids);
            voids
                .try_reserve(new_voids.len())
                .map_err(|_| MeasurementError::Exhausted("spatial event allocation failed"))?;
            voids.extend(new_voids);
            voids.sort_by(|left, right| left.started_at_s.total_cmp(&right.started_at_s));
        }
        if !new_collection.is_empty() {
            let collection = Arc::make_mut(&mut self.collection);
            collection
                .try_reserve(new_collection.len())
                .map_err(|_| MeasurementError::Exhausted("spatial event allocation failed"))?;
            collection.extend(new_collection);
            collection.sort_by(|left, right| left.started_at_s.total_cmp(&right.started_at_s));
        }
        self.advanced_to_s = elapsed_s;
        Ok(())
    }

    pub fn resolve_distances(
        &self,
        reference: &TimedReferenceScan,
        min_distance_m: f64,
        max_distance_m: f64,
    ) -> Result<Vec<f64>> {
        let metadata = require_spatial_metadata(reference)?;
        if metadata.static_scene() != &self.static_scene {
            return Err(MeasurementError::Invalid(
                "reference scan and distortion resolver static scenes must match",
            ));
        }
        if reference.completed_at_s() > self.advanced_to_s + TIME_TOLERANCE_S {
            return Err(MeasurementError::Invalid(
                "spatial events must cover the completed scan",
            ));
        }
        if reference.schedule().point_elapsed_times_s()[0] < self.retained_from_s {
            return Err(MeasurementError::Invalid(
                "spatial event history no longer covers the scan",
            ));
        }
        let mut resolved = resolve_void_distances(
            reference,
            self.voids.as_slice(),
            min_distance_m,
            max_distance_m,
        )?;
        let falling =
            resolve_falling_material_distances(reference, self.falling.as_slice(), min_distance_m)?;
        let collection = resolve_collection_occlusion_distances(
            reference,
            self.collection.as_slice(),
            min_distance_m,
        )?;
        for (((value, falling), collection), point) in resolved
            .iter_mut()
            .zip(falling)
            .zip(collection)
            .zip(reference.scan().points())
        {
            let foreground = falling.min(collection);
            if foreground < point.distance_m {
                *value = value.min(foreground);
            }
        }
        Ok(resolved)
    }

    pub fn discard_before(&mut self, elapsed_s: f64) -> Result<()> {
        if !elapsed_s.is_finite()
            || elapsed_s < self.retained_from_s
            || elapsed_s > self.advanced_to_s + TIME_TOLERANCE_S
        {
            return Err(MeasurementError::Invalid(
                "distortion retention time must be within generated history",
            ));
        }
        if self
            .falling
            .iter()
            .any(|event| event.ends_at_s <= elapsed_s)
        {
            Arc::make_mut(&mut self.falling).retain(|event| event.ends_at_s > elapsed_s);
        }
        if self.voids.iter().any(|event| event.ends_at_s <= elapsed_s) {
            Arc::make_mut(&mut self.voids).retain(|event| event.ends_at_s > elapsed_s);
        }
        if self
            .collection
            .iter()
            .any(|event| event.ends_at_s <= elapsed_s)
        {
            Arc::make_mut(&mut self.collection).retain(|event| event.ends_at_s > elapsed_s);
        }
        self.retained_from_s = elapsed_s;
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct SpatialDistortionTimeline {
    boundary: Polygon2,
    resolver: SpatialDistortionResolver,
    falling_settings: Option<FallingMaterialSettings>,
    void_settings: Option<VoidSettings>,
    collection_settings: Option<CollectionOcclusionSettings>,
    falling_rng: ModelRng,
    void_rng: ModelRng,
    collection_rng: ModelRng,
    falling_phase: Option<(u64, u64)>,
    collection_phase: Option<(u64, u64)>,
    next_falling_candidate_at_s: f64,
    next_collection_event_at_s: f64,
    x_bounds_m: [f64; 2],
    y_bounds_m: [f64; 2],
}

impl SpatialDistortionTimeline {
    pub fn new(
        boundary: Polygon2,
        static_scene: EnvironmentScene,
        falling_settings: Option<FallingMaterialSettings>,
        void_settings: Option<VoidSettings>,
        collection_settings: Option<CollectionOcclusionSettings>,
        seed: u64,
    ) -> Result<Self> {
        if static_scene.boundary() != &boundary {
            return Err(MeasurementError::Invalid(
                "spatial static scene must match the boundary",
            ));
        }
        if let Some(settings) = &falling_settings {
            validate_falling_settings(settings, &boundary)?;
        }
        if let Some(settings) = void_settings {
            validate_void_settings(settings)?;
        }
        if let Some(settings) = collection_settings {
            validate_collection_settings(settings)?;
        }
        let x_bounds_m = bounds(boundary.vertices().iter().map(|point| point.x()));
        let y_bounds_m = bounds(boundary.vertices().iter().map(|point| point.y()));
        Ok(Self {
            resolver: SpatialDistortionResolver::new(static_scene),
            boundary,
            falling_settings,
            void_settings,
            collection_settings,
            falling_rng: ModelRng::new(seed, StreamScope::Global, "falling-material")?,
            void_rng: ModelRng::new(seed, StreamScope::Global, "voids")?,
            collection_rng: ModelRng::new(seed, StreamScope::Global, "collection-occlusion")?,
            falling_phase: None,
            collection_phase: None,
            next_falling_candidate_at_s: f64::INFINITY,
            next_collection_event_at_s: f64::INFINITY,
            x_bounds_m,
            y_bounds_m,
        })
    }

    pub fn resolver(&self) -> &SpatialDistortionResolver {
        &self.resolver
    }

    pub fn advanced_to_s(&self) -> f64 {
        self.resolver.advanced_to_s()
    }

    pub fn advance_to(&mut self, interval: DistortionInterval<'_>) -> Result<()> {
        let mut candidate = self.clone();
        candidate.advance_to_inner(interval)?;
        *self = candidate;
        Ok(())
    }

    fn advance_to_inner(&mut self, interval: DistortionInterval<'_>) -> Result<()> {
        let phase_start = interval.phase.started_at_s();
        let phase_end = interval.phase.ends_at_s();
        if !interval.started_at_s.is_finite()
            || !interval.ends_at_s.is_finite()
            || !phase_start.is_finite()
            || !phase_end.is_finite()
            || phase_start < 0.0
            || phase_end <= phase_start
            || interval.started_at_s < 0.0
            || interval.ends_at_s < interval.started_at_s
            || (interval.started_at_s - self.advanced_to_s()).abs() > TIME_TOLERANCE_S
            || interval.ends_at_s > phase_end + TIME_TOLERANCE_S
            || interval.started_at_s < phase_start - TIME_TOLERANCE_S
        {
            return Err(MeasurementError::Invalid(
                "distortion interval must be finite, monotonic, and within one phase",
            ));
        }
        if interval.surface.boundary() != &self.boundary
            || (interval.surface.floor_z_m() - self.resolver.static_scene.floor_z_m()).abs()
                > HEIGHT_TOLERANCE_M
            || (interval.surface.top_z_m() - self.resolver.static_scene.top_z_m()).abs()
                > HEIGHT_TOLERANCE_M
        {
            return Err(MeasurementError::Invalid(
                "distortion surface and static scene must share bounds",
            ));
        }

        self.synchronize_voids(interval.started_at_s, interval.phase, interval.surface)?;
        let mut falling = Vec::new();
        let mut voids = Vec::new();
        let mut collection = Vec::new();
        match interval.phase {
            DistortionPhase::Filling {
                cycle_index,
                started_at_s,
                ends_at_s,
                inlet_index,
                rate_profile,
            } => {
                if (rate_profile.duration_s() - (ends_at_s - started_at_s)).abs()
                    > (ends_at_s - started_at_s).max(1.0) * TIME_TOLERANCE_S
                {
                    return Err(MeasurementError::Invalid(
                        "falling rate profile must span the fill phase",
                    ));
                }
                self.generate_falling(
                    cycle_index,
                    started_at_s,
                    ends_at_s,
                    inlet_index,
                    rate_profile,
                    interval.ends_at_s,
                    &mut falling,
                )?;
                self.maintain_void_coverage(
                    cycle_index,
                    ends_at_s,
                    interval.started_at_s,
                    interval.ends_at_s,
                    interval.surface,
                    &mut voids,
                )?;
            }
            DistortionPhase::Collecting {
                cycle_index,
                started_at_s,
                ends_at_s,
            } => self.generate_collection(
                cycle_index,
                started_at_s,
                ends_at_s,
                interval.ends_at_s,
                &mut collection,
            )?,
        }
        self.resolver
            .advance_with_events_inner(interval.ends_at_s, falling, voids, collection)
    }

    pub fn discard_before(&mut self, earliest_pending_elapsed_s: f64) -> Result<()> {
        self.resolver.discard_before(earliest_pending_elapsed_s)
    }

    #[allow(clippy::too_many_arguments)]
    fn generate_falling(
        &mut self,
        cycle_index: u64,
        phase_start: f64,
        phase_end: f64,
        inlet_index: usize,
        rate_profile: &SmoothRateProfile,
        through: f64,
        output: &mut Vec<FallingMaterialEvent>,
    ) -> Result<()> {
        let Some(settings) = self.falling_settings.clone() else {
            return Ok(());
        };
        if settings.event_rate_per_s == 0.0 {
            return Ok(());
        }
        let inlet = *settings
            .inlet_positions
            .get(inlet_index)
            .ok_or(MeasurementError::Invalid(
                "active inlet index is out of range",
            ))?;
        let maximum_factor = rate_profile
            .segments()
            .iter()
            .map(|segment| 1.0 + segment.deviation())
            .fold(1.0_f64, f64::max);
        let maximum_rate = settings.event_rate_per_s * maximum_factor;
        if !maximum_rate.is_finite() || maximum_rate <= 0.0 {
            return Err(MeasurementError::Numerical(
                "falling event rate must remain finite and positive",
            ));
        }
        let phase_key = (cycle_index, phase_start.to_bits());
        if self.falling_phase != Some(phase_key) {
            if (self.advanced_to_s() - phase_start).abs() > TIME_TOLERANCE_S {
                return Err(MeasurementError::Invalid(
                    "falling timeline must observe a phase from its start",
                ));
            }
            self.falling_phase = Some(phase_key);
            self.next_falling_candidate_at_s =
                advance_exponential(&mut self.falling_rng, phase_start, maximum_rate)?;
        }
        let mut generated = 0;
        while self.next_falling_candidate_at_s < through {
            generated += 1;
            if generated > MAX_EVENTS_PER_ADVANCE {
                return Err(MeasurementError::Exhausted(
                    "falling event count exceeds the per-advance limit",
                ));
            }
            let at_s = self.next_falling_candidate_at_s;
            let factor = rate_profile.factor_at(at_s - phase_start)?;
            if self.falling_rng.unit_f64() < factor / maximum_factor {
                let duration = self
                    .falling_rng
                    .uniform(settings.duration_s_range[0], settings.duration_s_range[1])?;
                let ends_at_s = phase_end.min(at_s + duration);
                if ends_at_s > at_s {
                    let center = self.sample_falling_center(inlet, settings.placement_radius_m)?;
                    let radius = self
                        .falling_rng
                        .uniform(settings.radius_m_range[0], settings.radius_m_range[1])?;
                    let reduction = self.falling_rng.uniform(
                        settings.distance_reduction_m_range[0],
                        settings.distance_reduction_m_range[1],
                    )?;
                    output.push(
                        FallingMaterialEvent {
                            cycle_index,
                            started_at_s: at_s,
                            ends_at_s,
                            center,
                            radius_m: radius,
                            distance_reduction_m: reduction,
                        }
                        .validate()?,
                    );
                }
            }
            self.next_falling_candidate_at_s =
                advance_exponential(&mut self.falling_rng, at_s, maximum_rate)?;
        }
        Ok(())
    }

    fn sample_falling_center(&mut self, inlet: Vec2, placement_radius_m: f64) -> Result<Vec2> {
        for _ in 0..MAX_FALLING_CENTER_ATTEMPTS {
            let distance = placement_radius_m * self.falling_rng.unit_f64().sqrt();
            let angle = std::f64::consts::TAU * self.falling_rng.unit_f64();
            let candidate = Vec2::new(
                inlet.x() + distance * angle.cos(),
                inlet.y() + distance * angle.sin(),
            )?;
            if self.boundary.contains(candidate)? {
                return Ok(candidate);
            }
        }
        Ok(inlet)
    }

    fn generate_collection(
        &mut self,
        cycle_index: u64,
        phase_start: f64,
        phase_end: f64,
        through: f64,
        output: &mut Vec<CollectionOcclusionEvent>,
    ) -> Result<()> {
        let Some(settings) = self.collection_settings else {
            return Ok(());
        };
        let phase_key = (cycle_index, phase_start.to_bits());
        if self.collection_phase != Some(phase_key) {
            if (self.advanced_to_s() - phase_start).abs() > TIME_TOLERANCE_S {
                return Err(MeasurementError::Invalid(
                    "collection timeline must observe a phase from its start",
                ));
            }
            self.collection_phase = Some(phase_key);
            self.next_collection_event_at_s = advance_uniform(
                &mut self.collection_rng,
                phase_start,
                settings.event_interval_s_range,
            )?;
        }
        let mut generated = 0;
        while self.next_collection_event_at_s < through {
            generated += 1;
            if generated > MAX_EVENTS_PER_ADVANCE {
                return Err(MeasurementError::Exhausted(
                    "collection event count exceeds the per-advance limit",
                ));
            }
            let at_s = self.next_collection_event_at_s;
            let radius = self
                .collection_rng
                .uniform(settings.radius_m_range[0], settings.radius_m_range[1])?;
            if let Some((start_center, end_center)) = self.sample_collection_path(radius)? {
                let duration = self
                    .collection_rng
                    .uniform(settings.duration_s_range[0], settings.duration_s_range[1])?;
                let ends_at_s = phase_end.min(at_s + duration);
                if ends_at_s > at_s {
                    output.push(
                        CollectionOcclusionEvent {
                            cycle_index,
                            started_at_s: at_s,
                            ends_at_s,
                            start_center,
                            end_center,
                            radius_m: radius,
                            distance_reduction_m: self.collection_rng.uniform(
                                settings.distance_reduction_m_range[0],
                                settings.distance_reduction_m_range[1],
                            )?,
                        }
                        .validate()?,
                    );
                }
            }
            self.next_collection_event_at_s = advance_uniform(
                &mut self.collection_rng,
                at_s,
                settings.event_interval_s_range,
            )?;
        }
        Ok(())
    }

    fn sample_collection_path(&mut self, radius: f64) -> Result<Option<(Vec2, Vec2)>> {
        for _ in 0..MAX_COLLECTION_PATH_ATTEMPTS {
            let (Some(start), Some(end)) = (
                self.sample_clear_collection_point(radius)?,
                self.sample_clear_collection_point(radius)?,
            ) else {
                continue;
            };
            if minimum_path_boundary_distance(start, end, &self.boundary) >= radius {
                return Ok(Some((start, end)));
            }
        }
        Ok(None)
    }

    fn sample_clear_collection_point(&mut self, radius: f64) -> Result<Option<Vec2>> {
        for _ in 0..MAX_COLLECTION_POINT_ATTEMPTS {
            let candidate = Vec2::new(
                self.collection_rng
                    .uniform(self.x_bounds_m[0], self.x_bounds_m[1])?,
                self.collection_rng
                    .uniform(self.y_bounds_m[0], self.y_bounds_m[1])?,
            )?;
            if self.boundary.contains(candidate)?
                && minimum_boundary_distance(candidate, &self.boundary) >= radius
            {
                return Ok(Some(candidate));
            }
        }
        Ok(None)
    }

    fn synchronize_voids(
        &mut self,
        at_s: f64,
        phase: DistortionPhase<'_>,
        surface: &SurfaceSnapshot,
    ) -> Result<()> {
        let Some(settings) = self.void_settings else {
            return Ok(());
        };
        let mut close_indices = Vec::new();
        for (index, event) in self.resolver.voids.iter().enumerate() {
            if !(event.started_at_s <= at_s && at_s < event.ends_at_s) {
                continue;
            }
            let close = match phase {
                DistortionPhase::Collecting { .. } => true,
                DistortionPhase::Filling { .. } => {
                    let rise = surface.height_at(event.center)? - event.surface_height_at_start_m;
                    rise > HEIGHT_TOLERANCE_M
                        && rise + HEIGHT_TOLERANCE_M >= settings.cover_height_increase_m
                }
            };
            if close {
                close_indices
                    .try_reserve(1)
                    .map_err(|_| MeasurementError::Exhausted("void index allocation failed"))?;
                close_indices.push(index);
            }
        }
        if !close_indices.is_empty() {
            let events = Arc::make_mut(&mut self.resolver.voids);
            for index in close_indices {
                events[index].ends_at_s = at_s;
            }
        }
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    fn maintain_void_coverage(
        &mut self,
        cycle_index: u64,
        phase_end: f64,
        started_at_s: f64,
        through: f64,
        surface: &SurfaceSnapshot,
        output: &mut Vec<VoidEvent>,
    ) -> Result<()> {
        if self.void_settings.is_none() {
            return Ok(());
        }
        let mut cursor = started_at_s;
        loop {
            self.fill_void_coverage(cycle_index, phase_end, cursor, surface, output)?;
            let next_expiry = self
                .resolver
                .voids
                .iter()
                .chain(output.iter())
                .filter(|event| event.started_at_s <= cursor && cursor < event.ends_at_s)
                .map(|event| event.ends_at_s)
                .fold(f64::INFINITY, f64::min);
            if next_expiry >= through {
                return Ok(());
            }
            if next_expiry <= cursor {
                return Err(MeasurementError::Numerical(
                    "void expiry cursor did not advance",
                ));
            }
            cursor = next_expiry;
        }
    }

    fn fill_void_coverage(
        &mut self,
        cycle_index: u64,
        phase_end: f64,
        at_s: f64,
        surface: &SurfaceSnapshot,
        output: &mut Vec<VoidEvent>,
    ) -> Result<()> {
        let Some(settings) = self.void_settings else {
            return Ok(());
        };
        if settings.surface_area_ratio == 0.0 {
            return Ok(());
        }
        let surface_area = surface.capacity_m3() / (surface.top_z_m() - surface.floor_z_m());
        let target_area = surface_area * settings.surface_area_ratio;
        let mut active: Vec<_> = self
            .resolver
            .voids
            .iter()
            .chain(output.iter())
            .copied()
            .filter(|event| event.started_at_s <= at_s && at_s < event.ends_at_s)
            .collect();
        let mut active_area = active
            .iter()
            .map(|event| std::f64::consts::PI * event.radius_m * event.radius_m)
            .sum::<f64>();
        let mut failed = 0;
        while active_area < target_area && failed < MAX_VOID_EVENT_ATTEMPTS {
            let Some(event) =
                self.sample_void_event(cycle_index, phase_end, at_s, surface, &active)?
            else {
                failed += 1;
                continue;
            };
            if output.len() >= MAX_EVENTS_PER_ADVANCE {
                return Err(MeasurementError::Exhausted(
                    "void event count exceeds the per-advance limit",
                ));
            }
            let event_area = std::f64::consts::PI * event.radius_m * event.radius_m;
            let next_area = active_area + event_area;
            if !event_area.is_finite()
                || event_area <= 0.0
                || !next_area.is_finite()
                || next_area <= active_area
            {
                return Err(MeasurementError::Numerical(
                    "void coverage area did not advance",
                ));
            }
            active_area = next_area;
            active.push(event);
            output.push(event);
        }
        Ok(())
    }

    fn sample_void_event(
        &mut self,
        cycle_index: u64,
        phase_end: f64,
        at_s: f64,
        surface: &SurfaceSnapshot,
        active: &[VoidEvent],
    ) -> Result<Option<VoidEvent>> {
        let settings = self
            .void_settings
            .expect("caller checks void settings before sampling");
        let radius = self
            .void_rng
            .uniform(settings.radius_m_range[0], settings.radius_m_range[1])?;
        let Some(center) = self.sample_void_center(surface, radius, active)? else {
            return Ok(None);
        };
        let duration = self
            .void_rng
            .uniform(settings.duration_s_range[0], settings.duration_s_range[1])?;
        let expires_at_s = phase_end.min(at_s + duration);
        if expires_at_s <= at_s {
            return Ok(None);
        }
        Ok(Some(
            VoidEvent {
                cycle_index,
                started_at_s: at_s,
                expires_at_s,
                ends_at_s: expires_at_s,
                center,
                surface_height_at_start_m: surface.height_at(center)?,
                radius_m: radius,
                distance_increase_m: self.void_rng.uniform(
                    settings.distance_increase_m_range[0],
                    settings.distance_increase_m_range[1],
                )?,
            }
            .validate()?,
        ))
    }

    fn sample_void_center(
        &mut self,
        surface: &SurfaceSnapshot,
        radius: f64,
        active: &[VoidEvent],
    ) -> Result<Option<Vec2>> {
        for _ in 0..MAX_VOID_CENTER_ATTEMPTS {
            let candidate = Vec2::new(
                self.void_rng
                    .uniform(self.x_bounds_m[0], self.x_bounds_m[1])?,
                self.void_rng
                    .uniform(self.y_bounds_m[0], self.y_bounds_m[1])?,
            )?;
            if !self.boundary.contains(candidate)?
                || minimum_boundary_distance(candidate, &self.boundary) < radius
                || active.iter().any(|event| {
                    (candidate.x() - event.center.x()).hypot(candidate.y() - event.center.y())
                        < radius + event.radius_m
                })
                || surface.height_at(candidate)? <= surface.floor_z_m() + HEIGHT_TOLERANCE_M
            {
                continue;
            }
            return Ok(Some(candidate));
        }
        Ok(None)
    }
}

pub fn resolve_falling_material_distances(
    reference: &TimedReferenceScan,
    events: &[FallingMaterialEvent],
    min_distance_m: f64,
) -> Result<Vec<f64>> {
    require_positive(min_distance_m, "minimum measurement distance")?;
    for &event in events {
        event.validate()?;
    }
    let mut resolved = reference_distances(reference);
    let metadata = require_spatial_metadata(reference)?;
    if !metadata.matches_minimum(min_distance_m) {
        return Err(MeasurementError::Invalid(
            "spatial distortion bounds must match the reference scanner",
        ));
    }
    let contexts = metadata.contexts();
    for (index, ((point, &elapsed_s), context)) in reference
        .scan()
        .points()
        .iter()
        .zip(reference.schedule().point_elapsed_times_s())
        .zip(contexts)
        .enumerate()
    {
        if !context.dynamic_surface_hit() {
            continue;
        }
        let hit = context
            .surface_hit_position_m()
            .expect("validated dynamic surface context has a hit position");
        for event in events.iter().copied() {
            if elapsed_s < event.started_at_s || elapsed_s >= event.ends_at_s {
                continue;
            }
            let dx = hit.x() - event.center.x();
            let dy = hit.y() - event.center.y();
            let candidate = point.distance_m - event.distance_reduction_m;
            if dx * dx + dy * dy <= event.radius_m * event.radius_m && candidate >= min_distance_m {
                resolved[index] = resolved[index].min(candidate);
            }
        }
    }
    Ok(resolved)
}

pub fn resolve_collection_occlusion_distances(
    reference: &TimedReferenceScan,
    events: &[CollectionOcclusionEvent],
    min_distance_m: f64,
) -> Result<Vec<f64>> {
    require_positive(min_distance_m, "minimum measurement distance")?;
    for &event in events {
        event.validate()?;
    }
    let mut resolved = reference_distances(reference);
    let metadata = require_spatial_metadata(reference)?;
    if !metadata.matches_minimum(min_distance_m) {
        return Err(MeasurementError::Invalid(
            "spatial distortion bounds must match the reference scanner",
        ));
    }
    let contexts = metadata.contexts();
    for (index, ((point, &elapsed_s), context)) in reference
        .scan()
        .points()
        .iter()
        .zip(reference.schedule().point_elapsed_times_s())
        .zip(contexts)
        .enumerate()
    {
        if !context.dynamic_surface_hit() {
            continue;
        }
        let hit = context
            .surface_hit_position_m()
            .expect("validated dynamic surface context has a hit position");
        for event in events.iter().copied() {
            if elapsed_s < event.started_at_s || elapsed_s >= event.ends_at_s {
                continue;
            }
            let center = event.center_at(elapsed_s)?;
            let dx = hit.x() - center.x();
            let dy = hit.y() - center.y();
            let candidate = point.distance_m - event.distance_reduction_m;
            if dx * dx + dy * dy <= event.radius_m * event.radius_m && candidate >= min_distance_m {
                resolved[index] = resolved[index].min(candidate);
            }
        }
    }
    Ok(resolved)
}

pub fn resolve_void_distances(
    reference: &TimedReferenceScan,
    events: &[VoidEvent],
    min_distance_m: f64,
    max_distance_m: f64,
) -> Result<Vec<f64>> {
    require_distance_bounds(min_distance_m, max_distance_m)?;
    if min_distance_m == 0.0 || !max_distance_m.is_finite() {
        return Err(MeasurementError::Invalid(
            "void resolution requires finite positive measurement bounds",
        ));
    }
    for &event in events {
        event.validate()?;
    }
    let mut resolved = reference_distances(reference);
    let mut selected_before = vec![false; resolved.len()];
    let metadata = require_spatial_metadata(reference)?;
    if !metadata.matches_bounds(min_distance_m, max_distance_m) {
        return Err(MeasurementError::Invalid(
            "spatial distortion bounds must match the reference scanner",
        ));
    }
    let contexts = metadata.contexts();
    for (index, ((point, &elapsed_s), context)) in reference
        .scan()
        .points()
        .iter()
        .zip(reference.schedule().point_elapsed_times_s())
        .zip(contexts)
        .enumerate()
    {
        if !context.dynamic_surface_hit() {
            continue;
        }
        let hit = context
            .surface_hit_position_m()
            .expect("validated dynamic surface context has a hit position");
        let static_distance = context.static_hit_distance_m().unwrap_or(f64::INFINITY);
        for event in events.iter().copied() {
            if elapsed_s < event.started_at_s || elapsed_s >= event.ends_at_s {
                continue;
            }
            let dx = hit.x() - event.center.x();
            let dy = hit.y() - event.center.y();
            let candidate = point.distance_m + event.distance_increase_m;
            if dx * dx + dy * dy > event.radius_m * event.radius_m
                || candidate > max_distance_m
                || candidate > static_distance
            {
                continue;
            }
            if !selected_before[index] || candidate < resolved[index] {
                resolved[index] = candidate;
            }
            selected_before[index] = true;
        }
    }
    Ok(resolved)
}

fn require_spatial_metadata(reference: &TimedReferenceScan) -> Result<&ReferenceSpatialMetadata> {
    reference
        .spatial_metadata()
        .ok_or(MeasurementError::Invalid(
            "spatial distortion requires cached reference geometry",
        ))
}

fn reference_distances(reference: &TimedReferenceScan) -> Vec<f64> {
    reference
        .scan()
        .points()
        .iter()
        .map(|point| point.distance_m)
        .collect()
}

fn validate_falling_settings(
    settings: &FallingMaterialSettings,
    boundary: &Polygon2,
) -> Result<()> {
    if !settings.event_rate_per_s.is_finite()
        || settings.event_rate_per_s < 0.0
        || settings.inlet_positions.is_empty()
        || !settings.placement_radius_m.is_finite()
        || settings.placement_radius_m <= 0.0
    {
        return Err(MeasurementError::Invalid(
            "falling settings contain an invalid scalar or inlet list",
        ));
    }
    require_range(settings.radius_m_range)?;
    require_range(settings.duration_s_range)?;
    require_range(settings.distance_reduction_m_range)?;
    if settings
        .inlet_positions
        .iter()
        .any(|position| !boundary.contains(*position).unwrap_or(false))
    {
        return Err(MeasurementError::Invalid(
            "falling inlet positions must lie inside the boundary",
        ));
    }
    Ok(())
}

fn validate_void_settings(settings: VoidSettings) -> Result<()> {
    if !settings.surface_area_ratio.is_finite()
        || !(0.0..=1.0).contains(&settings.surface_area_ratio)
        || !settings.cover_height_increase_m.is_finite()
        || settings.cover_height_increase_m < 0.0
    {
        return Err(MeasurementError::Invalid(
            "void settings contain an invalid ratio or height",
        ));
    }
    require_range(settings.radius_m_range)?;
    let minimum_area =
        std::f64::consts::PI * settings.radius_m_range[0] * settings.radius_m_range[0];
    let maximum_area =
        std::f64::consts::PI * settings.radius_m_range[1] * settings.radius_m_range[1];
    if !minimum_area.is_finite()
        || minimum_area <= 0.0
        || !maximum_area.is_finite()
        || maximum_area <= 0.0
    {
        return Err(MeasurementError::Invalid(
            "void radius must have a finite positive area",
        ));
    }
    require_range(settings.duration_s_range)?;
    require_range(settings.distance_increase_m_range)
}

fn validate_collection_settings(settings: CollectionOcclusionSettings) -> Result<()> {
    require_range(settings.event_interval_s_range)?;
    require_range(settings.radius_m_range)?;
    require_range(settings.duration_s_range)?;
    require_range(settings.distance_reduction_m_range)
}

fn require_range(range: [f64; 2]) -> Result<()> {
    if !range[0].is_finite() || !range[1].is_finite() || range[0] <= 0.0 || range[1] < range[0] {
        return Err(MeasurementError::Invalid(
            "distortion range must be finite, positive, and ordered",
        ));
    }
    Ok(())
}

fn bounds(values: impl IntoIterator<Item = f64>) -> [f64; 2] {
    let mut lower = f64::INFINITY;
    let mut upper = f64::NEG_INFINITY;
    for value in values {
        lower = lower.min(value);
        upper = upper.max(value);
    }
    [lower, upper]
}

fn advance_exponential(rng: &mut impl RandomSource, previous: f64, rate: f64) -> Result<f64> {
    let delay = -(1.0 - rng.unit_f64()).ln() / rate;
    let next = previous + delay;
    if !next.is_finite() || next <= previous {
        return Err(MeasurementError::Numerical(
            "exponential event schedule did not advance",
        ));
    }
    Ok(next)
}

fn advance_uniform(rng: &mut impl RandomSource, previous: f64, range: [f64; 2]) -> Result<f64> {
    let next = previous + rng.uniform(range[0], range[1])?;
    if !next.is_finite() || next <= previous {
        return Err(MeasurementError::Numerical(
            "uniform event schedule did not advance",
        ));
    }
    Ok(next)
}

fn minimum_boundary_distance(point: Vec2, boundary: &Polygon2) -> f64 {
    boundary
        .edges()
        .map(|(start, end)| point_segment_distance(point, start, end))
        .fold(f64::INFINITY, f64::min)
}

fn minimum_path_boundary_distance(start: Vec2, end: Vec2, boundary: &Polygon2) -> f64 {
    boundary
        .edges()
        .map(|(edge_start, edge_end)| segment_distance(start, end, edge_start, edge_end))
        .fold(f64::INFINITY, f64::min)
}

fn segment_distance(
    first_start: Vec2,
    first_end: Vec2,
    second_start: Vec2,
    second_end: Vec2,
) -> f64 {
    if segments_intersect(first_start, first_end, second_start, second_end) {
        return 0.0;
    }
    point_segment_distance(first_start, second_start, second_end)
        .min(point_segment_distance(first_end, second_start, second_end))
        .min(point_segment_distance(second_start, first_start, first_end))
        .min(point_segment_distance(second_end, first_start, first_end))
}

fn segments_intersect(
    first_start: Vec2,
    first_end: Vec2,
    second_start: Vec2,
    second_end: Vec2,
) -> bool {
    let first_x = first_end.x() - first_start.x();
    let first_y = first_end.y() - first_start.y();
    let second_x = second_end.x() - second_start.x();
    let second_y = second_end.y() - second_start.y();
    let cross = |ax: f64, ay: f64, bx: f64, by: f64| ax * by - ay * bx;
    let first_cross_start = cross(
        first_x,
        first_y,
        second_start.x() - first_start.x(),
        second_start.y() - first_start.y(),
    );
    let first_cross_end = cross(
        first_x,
        first_y,
        second_end.x() - first_start.x(),
        second_end.y() - first_start.y(),
    );
    let second_cross_start = cross(
        second_x,
        second_y,
        first_start.x() - second_start.x(),
        first_start.y() - second_start.y(),
    );
    let second_cross_end = cross(
        second_x,
        second_y,
        first_end.x() - second_start.x(),
        first_end.y() - second_start.y(),
    );
    let tolerance = 1e-12;
    (first_cross_start * first_cross_end < -tolerance
        && second_cross_start * second_cross_end < -tolerance)
        || (first_cross_start.abs() <= tolerance
            && point_on_segment(second_start, first_start, first_end))
        || (first_cross_end.abs() <= tolerance
            && point_on_segment(second_end, first_start, first_end))
        || (second_cross_start.abs() <= tolerance
            && point_on_segment(first_start, second_start, second_end))
        || (second_cross_end.abs() <= tolerance
            && point_on_segment(first_end, second_start, second_end))
}

fn point_on_segment(point: Vec2, start: Vec2, end: Vec2) -> bool {
    let tolerance = 1e-12;
    point.x() >= start.x().min(end.x()) - tolerance
        && point.x() <= start.x().max(end.x()) + tolerance
        && point.y() >= start.y().min(end.y()) - tolerance
        && point.y() <= start.y().max(end.y()) + tolerance
}

fn point_segment_distance(point: Vec2, start: Vec2, end: Vec2) -> f64 {
    let edge_x = end.x() - start.x();
    let edge_y = end.y() - start.y();
    let squared_length = edge_x * edge_x + edge_y * edge_y;
    if squared_length == 0.0 {
        return (point.x() - start.x()).hypot(point.y() - start.y());
    }
    let projection = (((point.x() - start.x()) * edge_x + (point.y() - start.y()) * edge_y)
        / squared_length)
        .clamp(0.0, 1.0);
    let closest_x = start.x() + projection * edge_x;
    let closest_y = start.y() + projection * edge_y;
    (point.x() - closest_x).hypot(point.y() - closest_y)
}

fn reserve_spatial_event<T>(events: &mut Vec<T>, total: &mut usize) -> Result<()> {
    if *total == MAX_EVENTS_PER_ADVANCE {
        return Err(MeasurementError::Exhausted(
            "spatial event count exceeds the per-advance limit",
        ));
    }
    events
        .try_reserve(1)
        .map_err(|_| MeasurementError::Exhausted("spatial event allocation failed"))?;
    *total += 1;
    Ok(())
}

fn require_event(started_at_s: f64, ends_at_s: f64, radius_m: f64) -> Result<()> {
    if !started_at_s.is_finite()
        || started_at_s < 0.0
        || !ends_at_s.is_finite()
        || ends_at_s <= started_at_s
    {
        return Err(MeasurementError::Invalid(
            "invalid distortion event lifetime",
        ));
    }
    require_positive(radius_m, "distortion radius")
}

fn require_positive(value: f64, _name: &'static str) -> Result<()> {
    if !value.is_finite() || value <= 0.0 {
        return Err(MeasurementError::Invalid(
            "distortion value must be finite and positive",
        ));
    }
    Ok(())
}
