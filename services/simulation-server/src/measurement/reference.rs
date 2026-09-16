//! Immutable reference and measured scans with a sensor-angle ray cache.

use std::{collections::BTreeMap, sync::Arc};

use crate::{
    geometry::{Ray, Vec3},
    scenario::SurfaceSnapshot,
};

use super::{
    EnvironmentScene, HitKind, MeasurementError, Result, ScheduledScan, SensorFrame,
    SnapshotEventCoordinator, require_distance_bounds,
};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ReferencePoint {
    pub angle_deg: f64,
    pub distance_m: f64,
    pub hit_kind: Option<HitKind>,
}

impl ReferencePoint {
    pub fn new(angle_deg: f64, distance_m: f64, hit_kind: Option<HitKind>) -> Result<Self> {
        if !angle_deg.is_finite() || !(0.0..360.0).contains(&angle_deg) {
            return Err(MeasurementError::Invalid(
                "reference angle must be finite and in [0, 360)",
            ));
        }
        if !distance_m.is_finite() || distance_m < 0.0 {
            return Err(MeasurementError::Invalid(
                "reference distance must be finite and non-negative",
            ));
        }
        if (distance_m == 0.0) != hit_kind.is_none() {
            return Err(MeasurementError::Invalid(
                "only a reference point without a hit may have zero distance",
            ));
        }
        Ok(Self {
            angle_deg,
            distance_m,
            hit_kind,
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ReferenceScan {
    sensor_id: String,
    points: Vec<ReferencePoint>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct ReferenceSpatialContext {
    dynamic_surface_hit: bool,
    surface_hit_position_m: Option<Vec3>,
    static_hit_distance_m: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ReferenceSpatialMetadata {
    static_scene: Arc<EnvironmentScene>,
    min_distance_m: f64,
    max_distance_m: f64,
    contexts: Vec<ReferenceSpatialContext>,
}

impl ReferenceSpatialMetadata {
    fn new(
        static_scene: Arc<EnvironmentScene>,
        min_distance_m: f64,
        max_distance_m: f64,
        contexts: Vec<ReferenceSpatialContext>,
    ) -> Result<Self> {
        require_distance_bounds(min_distance_m, max_distance_m)?;
        if min_distance_m == 0.0 || !max_distance_m.is_finite() {
            return Err(MeasurementError::Invalid(
                "reference spatial metadata requires finite positive bounds",
            ));
        }
        Ok(Self {
            static_scene,
            min_distance_m,
            max_distance_m,
            contexts,
        })
    }

    pub(crate) fn static_scene(&self) -> &EnvironmentScene {
        &self.static_scene
    }

    pub(crate) fn matches_bounds(&self, min_distance_m: f64, max_distance_m: f64) -> bool {
        self.matches_minimum(min_distance_m)
            && self.max_distance_m.to_bits() == max_distance_m.to_bits()
    }

    pub(crate) fn matches_minimum(&self, min_distance_m: f64) -> bool {
        self.min_distance_m.to_bits() == min_distance_m.to_bits()
    }

    pub(crate) fn contexts(&self) -> &[ReferenceSpatialContext] {
        &self.contexts
    }
}

impl ReferenceSpatialContext {
    fn new(
        dynamic_surface_hit: bool,
        surface_hit_position_m: Option<Vec3>,
        static_hit_distance_m: Option<f64>,
    ) -> Result<Self> {
        if dynamic_surface_hit != surface_hit_position_m.is_some() {
            return Err(MeasurementError::Invalid(
                "dynamic surface hits require one hit position",
            ));
        }
        if static_hit_distance_m.is_some_and(|distance| !distance.is_finite() || distance <= 0.0) {
            return Err(MeasurementError::Invalid(
                "reference static hit distance must be finite and positive",
            ));
        }
        Ok(Self {
            dynamic_surface_hit,
            surface_hit_position_m,
            static_hit_distance_m,
        })
    }

    pub(crate) fn dynamic_surface_hit(self) -> bool {
        self.dynamic_surface_hit
    }

    pub(crate) fn surface_hit_position_m(self) -> Option<Vec3> {
        self.surface_hit_position_m
    }

    pub(crate) fn static_hit_distance_m(self) -> Option<f64> {
        self.static_hit_distance_m
    }
}

impl ReferenceScan {
    pub fn new(sensor_id: impl Into<String>, points: Vec<ReferencePoint>) -> Result<Self> {
        let sensor_id = sensor_id.into();
        if sensor_id.is_empty() || points.is_empty() {
            return Err(MeasurementError::Invalid(
                "reference scan requires a sensor and at least one point",
            ));
        }
        Ok(Self { sensor_id, points })
    }

    pub fn sensor_id(&self) -> &str {
        &self.sensor_id
    }

    pub fn points(&self) -> &[ReferencePoint] {
        &self.points
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct TimedReferenceScan {
    schedule: ScheduledScan,
    scan: ReferenceScan,
    spatial_metadata: Option<ReferenceSpatialMetadata>,
}

impl TimedReferenceScan {
    pub fn new(schedule: ScheduledScan, scan: ReferenceScan) -> Result<Self> {
        Self::build(schedule, scan, None)
    }

    pub(crate) fn with_spatial_metadata(
        schedule: ScheduledScan,
        scan: ReferenceScan,
        spatial_metadata: ReferenceSpatialMetadata,
    ) -> Result<Self> {
        Self::build(schedule, scan, Some(spatial_metadata))
    }

    fn build(
        schedule: ScheduledScan,
        scan: ReferenceScan,
        spatial_metadata: Option<ReferenceSpatialMetadata>,
    ) -> Result<Self> {
        if schedule.sensor_id() != scan.sensor_id()
            || schedule.point_count() != scan.points().len()
            || schedule
                .angles_deg()
                .iter()
                .zip(scan.points())
                .any(|(angle, point)| angle.to_bits() != point.angle_deg.to_bits())
        {
            return Err(MeasurementError::Invalid(
                "timed reference scan must match its schedule",
            ));
        }
        if let Some(metadata) = &spatial_metadata
            && (metadata.contexts().len() != scan.points().len()
                || metadata
                    .contexts()
                    .iter()
                    .zip(scan.points())
                    .any(|(context, point)| {
                        (context.dynamic_surface_hit && point.hit_kind != Some(HitKind::Surface))
                            || if context.dynamic_surface_hit {
                                context
                                    .static_hit_distance_m
                                    .is_some_and(|distance| distance <= point.distance_m)
                            } else {
                                context.surface_hit_position_m.is_some()
                                    || match (point.hit_kind, context.static_hit_distance_m) {
                                        (None, None) => false,
                                        (Some(_), Some(distance)) => {
                                            distance.to_bits() != point.distance_m.to_bits()
                                        }
                                        _ => true,
                                    }
                            }
                    }))
        {
            return Err(MeasurementError::Invalid(
                "reference spatial contexts must match all reference points",
            ));
        }
        Ok(Self {
            schedule,
            scan,
            spatial_metadata,
        })
    }

    pub fn schedule(&self) -> &ScheduledScan {
        &self.schedule
    }

    pub fn scan(&self) -> &ReferenceScan {
        &self.scan
    }

    pub fn sensor_id(&self) -> &str {
        self.schedule.sensor_id()
    }

    pub fn scan_id(&self) -> u64 {
        self.schedule.scan_id()
    }

    pub fn completed_at_s(&self) -> f64 {
        self.schedule.completed_at_s()
    }

    pub(crate) fn spatial_metadata(&self) -> Option<&ReferenceSpatialMetadata> {
        self.spatial_metadata.as_ref()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct HqSample {
    pub angle_z_q14: u16,
    pub dist_mm_q2: u64,
    pub quality: u8,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MeasuredScan {
    sensor_id: String,
    hq_samples: Vec<HqSample>,
}

impl MeasuredScan {
    pub fn new(
        sensor_id: impl Into<String>,
        angles_deg: Vec<f64>,
        distances_m: Vec<f64>,
        qualities: Vec<u8>,
    ) -> Result<Self> {
        let sensor_id = sensor_id.into();
        let count = angles_deg.len();
        if sensor_id.is_empty()
            || count == 0
            || distances_m.len() != count
            || qualities.len() != count
        {
            return Err(MeasurementError::Invalid(
                "measured scan arrays must be finite, non-empty, and equal length",
            ));
        }
        let mut hq_samples = Vec::new();
        hq_samples
            .try_reserve_exact(count)
            .map_err(|_| MeasurementError::Exhausted("HQ sample allocation failed"))?;
        for ((angle_deg, distance_m), quality) in
            angles_deg.into_iter().zip(distances_m).zip(qualities)
        {
            hq_samples.push(HqSample {
                angle_z_q14: super::sdk::quantize_hq_angle_ticks(angle_deg)?,
                dist_mm_q2: super::sdk::quantize_hq_distance_ticks(distance_m)?,
                quality,
            });
        }
        Self::from_hq_samples(sensor_id, hq_samples)
    }

    pub fn from_hq_samples(
        sensor_id: impl Into<String>,
        hq_samples: Vec<HqSample>,
    ) -> Result<Self> {
        let sensor_id = sensor_id.into();
        if sensor_id.is_empty() || hq_samples.is_empty() {
            return Err(MeasurementError::Invalid(
                "measured scan requires a sensor and at least one HQ sample",
            ));
        }
        Ok(Self {
            sensor_id,
            hq_samples,
        })
    }

    pub fn sensor_id(&self) -> &str {
        &self.sensor_id
    }
    pub fn hq_samples(&self) -> &[HqSample] {
        &self.hq_samples
    }

    pub fn distances_m(&self) -> Vec<f64> {
        self.hq_samples
            .iter()
            .map(|sample| sample.dist_mm_q2 as f64 / super::sdk::HQ_DISTANCE_STEPS_PER_METER as f64)
            .collect()
    }

    pub fn qualities(&self) -> Vec<u8> {
        self.hq_samples
            .iter()
            .map(|sample| sample.quality)
            .collect()
    }

    pub fn into_hq_samples(self) -> Vec<HqSample> {
        self.hq_samples
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct MeasurementResult {
    reference: TimedReferenceScan,
    measured: MeasuredScan,
}

impl MeasurementResult {
    pub fn new(reference: TimedReferenceScan, measured: MeasuredScan) -> Result<Self> {
        let schedule = reference.schedule();
        if measured.sensor_id() != reference.sensor_id()
            || measured.hq_samples().len() != schedule.point_count()
            || measured
                .hq_samples()
                .iter()
                .zip(schedule.angles_deg())
                .any(|(sample, angle)| {
                    super::sdk::quantize_hq_angle_ticks(*angle) != Ok(sample.angle_z_q14)
                })
        {
            return Err(MeasurementError::Invalid(
                "measurement result must match its reference schedule",
            ));
        }
        Ok(Self {
            reference,
            measured,
        })
    }

    pub fn sensor_id(&self) -> &str {
        self.reference.sensor_id()
    }
    pub fn scan_id(&self) -> u64 {
        self.reference.scan_id()
    }
    pub fn reference(&self) -> &TimedReferenceScan {
        &self.reference
    }
    pub fn measured(&self) -> &MeasuredScan {
        &self.measured
    }

    pub fn into_measured(self) -> MeasuredScan {
        self.measured
    }
}

#[derive(Clone, Debug)]
pub struct ReferenceScanner {
    sensor_id: String,
    frame: SensorFrame,
    min_distance_m: f64,
    max_distance_m: f64,
    rays_by_hq_tick: BTreeMap<u16, Ray>,
    static_hits_by_hq_tick: BTreeMap<u16, Option<super::RayHit>>,
    cached_scene: Option<Arc<EnvironmentScene>>,
}

impl ReferenceScanner {
    pub fn new(
        sensor_id: impl Into<String>,
        frame: SensorFrame,
        min_distance_m: f64,
        max_distance_m: f64,
    ) -> Result<Self> {
        let sensor_id = sensor_id.into();
        require_distance_bounds(min_distance_m, max_distance_m)?;
        if sensor_id.is_empty() || min_distance_m == 0.0 || !max_distance_m.is_finite() {
            return Err(MeasurementError::Invalid(
                "reference scanner requires a sensor and finite positive bounds",
            ));
        }
        Ok(Self {
            sensor_id,
            frame,
            min_distance_m,
            max_distance_m,
            rays_by_hq_tick: BTreeMap::new(),
            static_hits_by_hq_tick: BTreeMap::new(),
            cached_scene: None,
        })
    }

    pub fn sensor_id(&self) -> &str {
        &self.sensor_id
    }

    pub fn cached_angle_count(&self) -> usize {
        self.rays_by_hq_tick.len()
    }

    pub fn cached_static_hit_count(&self) -> usize {
        self.static_hits_by_hq_tick.len()
    }

    pub fn generate(
        &mut self,
        scene: &EnvironmentScene,
        surface: Option<&SurfaceSnapshot>,
        schedule: ScheduledScan,
    ) -> Result<TimedReferenceScan> {
        if schedule.sensor_id() != self.sensor_id {
            return Err(MeasurementError::Invalid(
                "reference scanner and schedule sensors must match",
            ));
        }
        self.prepare_scene(scene);
        let mut points = Vec::new();
        let mut contexts = Vec::new();
        points
            .try_reserve_exact(schedule.point_count())
            .map_err(|_| MeasurementError::Exhausted("reference point allocation failed"))?;
        contexts
            .try_reserve_exact(schedule.point_count())
            .map_err(|_| MeasurementError::Exhausted("spatial context allocation failed"))?;
        for &angle in schedule.angles_deg() {
            let (ray, static_hit) = self.cached_ray_and_static_hit(scene, angle)?;
            let hit = scene.first_hit_from_static(
                ray,
                static_hit,
                surface,
                self.min_distance_m,
                self.max_distance_m,
            )?;
            let dynamic_surface_hit = hit.is_some_and(|value| {
                value.kind == HitKind::Surface
                    && static_hit
                        .is_none_or(|static_value| value.distance_m < static_value.distance_m)
            });
            contexts.push(ReferenceSpatialContext::new(
                dynamic_surface_hit,
                hit.filter(|_| dynamic_surface_hit)
                    .map(|value| value.position_m),
                static_hit.map(|value| value.distance_m),
            )?);
            points.push(match hit {
                Some(hit) => ReferencePoint::new(angle, hit.distance_m, Some(hit.kind))?,
                None => ReferencePoint::new(angle, 0.0, None)?,
            });
        }
        let scan = ReferenceScan::new(self.sensor_id.clone(), points)?;
        let metadata = self.spatial_metadata(contexts)?;
        TimedReferenceScan::with_spatial_metadata(schedule, scan, metadata)
    }

    pub fn generate_with_snapshots(
        &mut self,
        scene: &EnvironmentScene,
        snapshots: &SnapshotEventCoordinator,
        schedule: ScheduledScan,
    ) -> Result<TimedReferenceScan> {
        if schedule.sensor_id() != self.sensor_id {
            return Err(MeasurementError::Invalid(
                "reference scanner and schedule sensors must match",
            ));
        }
        self.prepare_scene(scene);
        let mut points = Vec::new();
        let mut contexts = Vec::new();
        points
            .try_reserve_exact(schedule.point_count())
            .map_err(|_| MeasurementError::Exhausted("reference point allocation failed"))?;
        contexts
            .try_reserve_exact(schedule.point_count())
            .map_err(|_| MeasurementError::Exhausted("spatial context allocation failed"))?;
        for (&angle, &elapsed_s) in schedule
            .angles_deg()
            .iter()
            .zip(schedule.point_elapsed_times_s())
        {
            let surface = snapshots.snapshot_at(elapsed_s)?;
            let (ray, static_hit) = self.cached_ray_and_static_hit(scene, angle)?;
            let hit = scene.first_hit_from_static(
                ray,
                static_hit,
                Some(&surface),
                self.min_distance_m,
                self.max_distance_m,
            )?;
            let dynamic_surface_hit = hit.is_some_and(|value| {
                value.kind == HitKind::Surface
                    && static_hit
                        .is_none_or(|static_value| value.distance_m < static_value.distance_m)
            });
            contexts.push(ReferenceSpatialContext::new(
                dynamic_surface_hit,
                hit.filter(|_| dynamic_surface_hit)
                    .map(|value| value.position_m),
                static_hit.map(|value| value.distance_m),
            )?);
            points.push(match hit {
                Some(hit) => ReferencePoint::new(angle, hit.distance_m, Some(hit.kind))?,
                None => ReferencePoint::new(angle, 0.0, None)?,
            });
        }
        let scan = ReferenceScan::new(self.sensor_id.clone(), points)?;
        let metadata = self.spatial_metadata(contexts)?;
        TimedReferenceScan::with_spatial_metadata(schedule, scan, metadata)
    }

    pub fn measure(
        &mut self,
        scene: &EnvironmentScene,
        surface: Option<&SurfaceSnapshot>,
        angle_deg: f64,
    ) -> Result<ReferencePoint> {
        self.prepare_scene(scene);
        let (ray, static_hit) = self.cached_ray_and_static_hit(scene, angle_deg)?;
        Ok(
            match scene.first_hit_from_static(
                ray,
                static_hit,
                surface,
                self.min_distance_m,
                self.max_distance_m,
            )? {
                Some(hit) => ReferencePoint::new(angle_deg, hit.distance_m, Some(hit.kind))?,
                None => ReferencePoint::new(angle_deg, 0.0, None)?,
            },
        )
    }

    fn cached_ray(&mut self, angle_deg: f64) -> Result<Ray> {
        let tick = super::sdk::quantize_hq_angle_ticks(angle_deg)?;
        if let Some(ray) = self.rays_by_hq_tick.get(&tick) {
            return Ok(*ray);
        }
        let quantized = f64::from(tick) * super::sdk::HQ_ANGLE_STEP_DEG;
        let ray = self.frame.ray_at(quantized)?;
        self.rays_by_hq_tick.insert(tick, ray);
        Ok(ray)
    }

    fn cached_ray_and_static_hit(
        &mut self,
        scene: &EnvironmentScene,
        angle_deg: f64,
    ) -> Result<(Ray, Option<super::RayHit>)> {
        let tick = super::sdk::quantize_hq_angle_ticks(angle_deg)?;
        let ray = self.cached_ray(angle_deg)?;
        let hit = match self.static_hits_by_hq_tick.get(&tick) {
            Some(hit) => *hit,
            None => {
                let hit = scene.first_static_hit(ray, self.min_distance_m, self.max_distance_m)?;
                self.static_hits_by_hq_tick.insert(tick, hit);
                hit
            }
        };
        Ok((ray, hit))
    }

    fn prepare_scene(&mut self, scene: &EnvironmentScene) {
        if self.cached_scene.as_deref() != Some(scene) {
            self.cached_scene = Some(Arc::new(scene.clone()));
            self.static_hits_by_hq_tick.clear();
        }
    }

    fn spatial_metadata(
        &self,
        contexts: Vec<ReferenceSpatialContext>,
    ) -> Result<ReferenceSpatialMetadata> {
        let scene = self
            .cached_scene
            .clone()
            .ok_or(MeasurementError::Numerical(
                "reference scanner scene was not prepared",
            ))?;
        ReferenceSpatialMetadata::new(scene, self.min_distance_m, self.max_distance_m, contexts)
    }
}
