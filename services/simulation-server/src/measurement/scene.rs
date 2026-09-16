//! World-coordinate sensor frames and deterministic first-hit scene queries.

use crate::{
    geometry::{Polygon2, Ray, Triangle, Vec2, Vec3},
    scenario::SurfaceSnapshot,
};

use super::{MeasurementError, Result, require_distance_bounds};

const FRAME_TOLERANCE: f64 = 1e-6;
const SCENE_TOLERANCE: f64 = 1e-9;
const PARALLEL_TOLERANCE: f64 = 1e-12;
const BARYCENTRIC_TOLERANCE: f64 = 1e-12;
const DISTANCE_TOLERANCE: f64 = 1e-10;
const POLYNOMIAL_TOLERANCE: f64 = 1e-12;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum HitKind {
    Floor,
    Wall,
    Surface,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RayHit {
    pub distance_m: f64,
    pub position_m: Vec3,
    pub kind: HitKind,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SensorFrame {
    origin_m: Vec3,
    u0: Vec3,
    u90: Vec3,
}

impl SensorFrame {
    pub fn new(origin_m: Vec3, u0: Vec3, u90: Vec3) -> Result<Self> {
        if (u0.length() - 1.0).abs() > FRAME_TOLERANCE
            || (u90.length() - 1.0).abs() > FRAME_TOLERANCE
        {
            return Err(MeasurementError::Invalid(
                "sensor frame axes must be unit vectors",
            ));
        }
        if u0.dot(u90)?.abs() > FRAME_TOLERANCE {
            return Err(MeasurementError::Invalid(
                "sensor frame axes must be orthogonal",
            ));
        }
        Ok(Self { origin_m, u0, u90 })
    }

    pub fn origin_m(self) -> Vec3 {
        self.origin_m
    }

    pub fn ray_at(self, angle_deg: f64) -> Result<Ray> {
        if !angle_deg.is_finite() || !(0.0..360.0).contains(&angle_deg) {
            return Err(MeasurementError::Invalid(
                "sensor angle must be finite and in [0, 360)",
            ));
        }
        let angle = angle_deg.to_radians();
        let direction = self
            .u0
            .checked_mul(angle.cos())?
            .checked_add(self.u90.checked_mul(angle.sin())?)?
            .normalized()?;
        Ok(Ray::new(self.origin_m, direction)?)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct EnvironmentScene {
    boundary: Polygon2,
    floor_z_m: f64,
    top_z_m: f64,
    surface_triangles: Vec<Triangle>,
    wall_triangles: Vec<Triangle>,
}

impl EnvironmentScene {
    pub fn new(
        boundary: Polygon2,
        floor_z_m: f64,
        top_z_m: f64,
        surface_triangles: Vec<Triangle>,
    ) -> Result<Self> {
        if !floor_z_m.is_finite() || !top_z_m.is_finite() || top_z_m <= floor_z_m {
            return Err(MeasurementError::Invalid(
                "scene heights must be finite with top above floor",
            ));
        }
        for triangle in &surface_triangles {
            for vertex in triangle.vertices() {
                if !boundary.contains(Vec2::new(vertex.x(), vertex.y())?)?
                    || vertex.z() < floor_z_m - SCENE_TOLERANCE
                    || vertex.z() > top_z_m + SCENE_TOLERANCE
                {
                    return Err(MeasurementError::Invalid(
                        "surface vertices must lie inside the scene bounds",
                    ));
                }
            }
        }
        let mut wall_triangles = Vec::new();
        wall_triangles
            .try_reserve_exact(boundary.vertices().len().saturating_mul(2))
            .map_err(|_| MeasurementError::Exhausted("scene wall allocation failed"))?;
        for (start, end) in boundary.edges() {
            let lower_start = Vec3::new(start.x(), start.y(), floor_z_m)?;
            let lower_end = Vec3::new(end.x(), end.y(), floor_z_m)?;
            let upper_start = Vec3::new(start.x(), start.y(), top_z_m)?;
            let upper_end = Vec3::new(end.x(), end.y(), top_z_m)?;
            wall_triangles.push(Triangle::new(lower_start, lower_end, upper_end)?);
            wall_triangles.push(Triangle::new(lower_start, upper_end, upper_start)?);
        }
        Ok(Self {
            boundary,
            floor_z_m,
            top_z_m,
            surface_triangles,
            wall_triangles,
        })
    }

    pub fn boundary(&self) -> &Polygon2 {
        &self.boundary
    }

    pub fn floor_z_m(&self) -> f64 {
        self.floor_z_m
    }

    pub fn top_z_m(&self) -> f64 {
        self.top_z_m
    }

    pub fn first_hit(
        &self,
        ray: Ray,
        dynamic_surface: Option<&SurfaceSnapshot>,
        min_distance_m: f64,
        max_distance_m: f64,
    ) -> Result<Option<RayHit>> {
        require_distance_bounds(min_distance_m, max_distance_m)?;
        let static_hit = self.first_static_hit(ray, min_distance_m, max_distance_m)?;
        self.first_hit_from_static(
            ray,
            static_hit,
            dynamic_surface,
            min_distance_m,
            max_distance_m,
        )
    }

    pub(crate) fn first_static_hit(
        &self,
        ray: Ray,
        min_distance_m: f64,
        max_distance_m: f64,
    ) -> Result<Option<RayHit>> {
        require_distance_bounds(min_distance_m, max_distance_m)?;
        let mut nearest_distance = max_distance_m;
        let mut nearest_kind = None;
        if let Some(distance) = self.intersect_floor(ray, min_distance_m, nearest_distance)? {
            nearest_distance = distance;
            nearest_kind = Some(HitKind::Floor);
        }
        for triangle in &self.wall_triangles {
            if let Some(distance) =
                intersect_triangle(ray, *triangle, min_distance_m, nearest_distance)?
                && (nearest_kind.is_none() || distance < nearest_distance)
            {
                nearest_distance = distance;
                nearest_kind = Some(HitKind::Wall);
            }
        }
        for triangle in &self.surface_triangles {
            let Some(distance) =
                intersect_triangle(ray, *triangle, min_distance_m, nearest_distance)?
            else {
                continue;
            };
            if nearest_kind.is_some() && distance >= nearest_distance {
                continue;
            }
            let position = ray.point_at(distance)?;
            if !self
                .boundary
                .contains(Vec2::new(position.x(), position.y())?)?
            {
                continue;
            }
            nearest_distance = distance;
            nearest_kind = Some(HitKind::Surface);
        }
        let Some(kind) = nearest_kind else {
            return Ok(None);
        };
        Ok(Some(RayHit {
            distance_m: nearest_distance,
            position_m: ray.point_at(nearest_distance)?,
            kind,
        }))
    }

    pub(crate) fn first_hit_from_static(
        &self,
        ray: Ray,
        static_hit: Option<RayHit>,
        dynamic_surface: Option<&SurfaceSnapshot>,
        min_distance_m: f64,
        max_distance_m: f64,
    ) -> Result<Option<RayHit>> {
        require_distance_bounds(min_distance_m, max_distance_m)?;
        if let Some(surface) = dynamic_surface
            && (surface.boundary() != &self.boundary
                || (surface.floor_z_m() - self.floor_z_m).abs() > SCENE_TOLERANCE
                || (surface.top_z_m() - self.top_z_m).abs() > SCENE_TOLERANCE)
        {
            return Err(MeasurementError::Invalid(
                "dynamic surface bounds must match the scene",
            ));
        }

        let mut nearest_distance = static_hit.map_or(max_distance_m, |hit| hit.distance_m);
        let mut nearest_kind = static_hit.map(|hit| hit.kind);
        if let Some(surface) = dynamic_surface
            && let Some(distance) =
                intersect_height_field(ray, surface, min_distance_m, nearest_distance)?
            && (nearest_kind.is_none() || distance < nearest_distance)
        {
            nearest_distance = distance;
            nearest_kind = Some(HitKind::Surface);
        }
        let Some(kind) = nearest_kind else {
            return Ok(None);
        };
        Ok(Some(RayHit {
            distance_m: nearest_distance,
            position_m: ray.point_at(nearest_distance)?,
            kind,
        }))
    }

    fn intersect_floor(&self, ray: Ray, minimum: f64, maximum: f64) -> Result<Option<f64>> {
        if ray.direction().z().abs() <= SCENE_TOLERANCE {
            return Ok(None);
        }
        let distance = (self.floor_z_m - ray.origin().z()) / ray.direction().z();
        if distance < minimum || distance > maximum {
            return Ok(None);
        }
        let point = ray.point_at(distance)?;
        Ok(self
            .boundary
            .contains(Vec2::new(point.x(), point.y())?)?
            .then_some(distance))
    }
}

fn intersect_triangle(
    ray: Ray,
    triangle: Triangle,
    minimum: f64,
    maximum: f64,
) -> Result<Option<f64>> {
    let [a, b, c] = triangle.vertices();
    let edge_ab = b.checked_sub(a)?;
    let edge_ac = c.checked_sub(a)?;
    let direction_cross_ac = ray.direction().cross(edge_ac)?;
    let determinant = edge_ab.dot(direction_cross_ac)?;
    if determinant.abs() <= PARALLEL_TOLERANCE {
        return Ok(None);
    }
    let inverse = 1.0 / determinant;
    let origin_offset = ray.origin().checked_sub(a)?;
    let barycentric_b = inverse * origin_offset.dot(direction_cross_ac)?;
    if !(-BARYCENTRIC_TOLERANCE..=1.0 + BARYCENTRIC_TOLERANCE).contains(&barycentric_b) {
        return Ok(None);
    }
    let offset_cross_ab = origin_offset.cross(edge_ab)?;
    let barycentric_c = inverse * ray.direction().dot(offset_cross_ab)?;
    if barycentric_c < -BARYCENTRIC_TOLERANCE
        || barycentric_b + barycentric_c > 1.0 + BARYCENTRIC_TOLERANCE
    {
        return Ok(None);
    }
    let distance = inverse * edge_ac.dot(offset_cross_ab)?;
    Ok((distance >= minimum && distance <= maximum).then_some(distance))
}

pub(crate) fn intersect_height_field(
    ray: Ray,
    surface: &SurfaceSnapshot,
    minimum: f64,
    maximum: f64,
) -> Result<Option<f64>> {
    require_distance_bounds(minimum, maximum)?;
    let x = surface.x_coordinates_m();
    let y = surface.y_coordinates_m();
    let heights = surface.heights_m();
    let (rows, columns) = surface.shape();
    let Some((entry, exit)) = horizontal_interval(
        ray,
        x[0],
        x[columns - 1],
        y[0],
        y[rows - 1],
        minimum,
        maximum,
    ) else {
        return Ok(None);
    };
    let mut breakpoints = Vec::with_capacity(x.len() + y.len());
    breakpoints.push(entry);
    breakpoints.push(exit);
    for (origin, direction, coordinates) in [
        (ray.origin().x(), ray.direction().x(), x),
        (ray.origin().y(), ray.direction().y(), y),
    ] {
        if direction == 0.0 {
            continue;
        }
        for coordinate in &coordinates[1..coordinates.len() - 1] {
            let crossing = (*coordinate - origin) / direction;
            if crossing > entry && crossing < exit {
                breakpoints.push(crossing);
            }
        }
    }
    breakpoints.sort_by(f64::total_cmp);
    breakpoints.dedup_by(|right, left| (*right - *left).abs() <= DISTANCE_TOLERANCE);
    if breakpoints.len() == 1 {
        breakpoints.push(entry);
    }
    for pair in breakpoints.windows(2) {
        let start = pair[0];
        let end = pair[1];
        let probe_distance = if end.is_infinite() {
            start
        } else {
            start + 0.5 * (end - start)
        };
        let probe = ray.point_at(probe_distance)?;
        let xi = cell_index(probe.x(), x[0], surface.cell_size_m(), columns - 1);
        let yi = cell_index(probe.y(), y[0], surface.cell_size_m(), rows - 1);
        for distance in intersect_cell(
            ray,
            start,
            end,
            x[xi],
            y[yi],
            surface.cell_size_m(),
            heights[yi * columns + xi],
            heights[yi * columns + xi + 1],
            heights[(yi + 1) * columns + xi],
            heights[(yi + 1) * columns + xi + 1],
        )? {
            let position = ray.point_at(distance)?;
            if surface
                .boundary()
                .contains(Vec2::new(position.x(), position.y())?)?
            {
                return Ok(Some(distance));
            }
        }
    }
    Ok(None)
}

fn horizontal_interval(
    ray: Ray,
    minimum_x: f64,
    maximum_x: f64,
    minimum_y: f64,
    maximum_y: f64,
    minimum_distance: f64,
    maximum_distance: f64,
) -> Option<(f64, f64)> {
    let mut entry = minimum_distance;
    let mut exit = maximum_distance;
    for (origin, direction, lower, upper) in [
        (ray.origin().x(), ray.direction().x(), minimum_x, maximum_x),
        (ray.origin().y(), ray.direction().y(), minimum_y, maximum_y),
    ] {
        if direction == 0.0 {
            if origin < lower || origin > upper {
                return None;
            }
            continue;
        }
        let first = (lower - origin) / direction;
        let second = (upper - origin) / direction;
        entry = entry.max(first.min(second));
        exit = exit.min(first.max(second));
        if exit < entry {
            return None;
        }
    }
    Some((entry, exit))
}

fn cell_index(value: f64, minimum: f64, size: f64, count: usize) -> usize {
    let offset = ((value - minimum) / size).floor().max(0.0) as usize;
    offset.min(count - 1)
}

#[allow(clippy::too_many_arguments)]
fn intersect_cell(
    ray: Ray,
    segment_start: f64,
    segment_end: f64,
    lower_x: f64,
    lower_y: f64,
    cell_size: f64,
    lower_left: f64,
    lower_right: f64,
    upper_left: f64,
    upper_right: f64,
) -> Result<Vec<f64>> {
    let segment_origin = ray.point_at(segment_start)?;
    let u_start = (segment_origin.x() - lower_x) / cell_size;
    let v_start = (segment_origin.y() - lower_y) / cell_size;
    let u_rate = ray.direction().x() / cell_size;
    let v_rate = ray.direction().y() / cell_size;
    let x_slope = lower_right - lower_left;
    let y_slope = upper_left - lower_left;
    let mixed = upper_right - lower_right - upper_left + lower_left;
    let surface_constant =
        lower_left + x_slope * u_start + y_slope * v_start + mixed * u_start * v_start;
    let surface_linear =
        x_slope * u_rate + y_slope * v_rate + mixed * (u_start * v_rate + v_start * u_rate);
    let surface_quadratic = mixed * u_rate * v_rate;
    let mut roots = real_roots(
        -surface_quadratic,
        ray.direction().z() - surface_linear,
        segment_origin.z() - surface_constant,
    );
    let length = segment_end - segment_start;
    roots.retain(|offset| {
        *offset >= -DISTANCE_TOLERANCE
            && (length.is_infinite() || *offset <= length + DISTANCE_TOLERANCE)
    });
    for offset in &mut roots {
        *offset = offset.max(0.0);
        if !length.is_infinite() {
            *offset = offset.min(length);
        }
        *offset += segment_start;
    }
    roots.sort_by(f64::total_cmp);
    roots.dedup_by(|right, left| right.to_bits() == left.to_bits());
    Ok(roots)
}

fn real_roots(quadratic: f64, linear: f64, constant: f64) -> Vec<f64> {
    if quadratic == 0.0 {
        return if linear == 0.0 {
            Vec::new()
        } else {
            vec![-constant / linear]
        };
    }
    let mut discriminant = linear * linear - 4.0 * quadratic * constant;
    let scale = 1.0_f64
        .max(linear * linear)
        .max((4.0 * quadratic * constant).abs());
    if discriminant < 0.0 {
        if discriminant < -POLYNOMIAL_TOLERANCE * scale {
            return Vec::new();
        }
        discriminant = 0.0;
    }
    let root = discriminant.sqrt();
    if root == 0.0 {
        return vec![-linear / (2.0 * quadratic)];
    }
    let stable = -0.5 * (linear + root.copysign(linear));
    if stable == 0.0 {
        return vec![-linear / (2.0 * quadratic)];
    }
    let mut roots = vec![stable / quadratic, constant / stable];
    roots.sort_by(f64::total_cmp);
    roots
}
