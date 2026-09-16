use crate::geometry::{GeometryError, Polygon2, Result, TOLERANCE, Vec2, sum};

/// Engine resource limit, not a physical environment or sensor specification.
pub const MAX_GRID_NODES: usize = 262_144;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CellCoverage {
    Empty,
    Partial,
    Full,
}

#[derive(Debug)]
pub(super) struct NeighborPairs {
    pub first: Vec<usize>,
    pub second: Vec<usize>,
    pub distance: Vec<f64>,
}

#[derive(Debug)]
pub(super) struct Grid {
    pub boundary: Polygon2,
    pub floor: f64,
    pub top: f64,
    pub cell_size: f64,
    pub x: Vec<f64>,
    pub y: Vec<f64>,
    pub weights: Vec<f64>,
    pub coverage: Vec<CellCoverage>,
    pub neighbors: NeighborPairs,
    pub capacity: f64,
}

pub(super) fn filled<T: Clone>(length: usize, value: T) -> Result<Vec<T>> {
    let mut values = Vec::new();
    values
        .try_reserve_exact(length)
        .map_err(|_| GeometryError::Allocation("surface allocation failed"))?;
    values.resize(length, value);
    Ok(values)
}

impl Grid {
    pub fn new(boundary: Polygon2, floor: f64, top: f64, cell_size: f64) -> Result<Self> {
        if !floor.is_finite() || !top.is_finite() || top <= floor {
            return Err(GeometryError::Invalid(
                "height field bounds must be finite with top above floor",
            ));
        }
        if !cell_size.is_finite() || cell_size <= 0.0 {
            return Err(GeometryError::Invalid(
                "height field cell size must be finite and positive",
            ));
        }
        let cell_area = cell_size * cell_size;
        let capacity = boundary.signed_area().abs() * (top - floor);
        if !cell_area.is_finite() || cell_area <= 0.0 || !capacity.is_finite() || capacity <= 0.0 {
            return Err(GeometryError::Numerical(
                "surface area and capacity must be finite and positive",
            ));
        }
        let min_x = boundary
            .vertices()
            .iter()
            .map(|p| p.x())
            .fold(f64::INFINITY, f64::min);
        let max_x = boundary
            .vertices()
            .iter()
            .map(|p| p.x())
            .fold(f64::NEG_INFINITY, f64::max);
        let min_y = boundary
            .vertices()
            .iter()
            .map(|p| p.y())
            .fold(f64::INFINITY, f64::min);
        let max_y = boundary
            .vertices()
            .iter()
            .map(|p| p.y())
            .fold(f64::NEG_INFINITY, f64::max);
        let columns = node_count(max_x - min_x, cell_size)?;
        let rows = node_count(max_y - min_y, cell_size)?;
        let count = rows
            .checked_mul(columns)
            .filter(|count| *count <= MAX_GRID_NODES)
            .ok_or(GeometryError::Allocation(
                "height field exceeds MAX_GRID_NODES",
            ))?;
        let x = coordinates(min_x, columns, cell_size)?;
        let y = coordinates(min_y, rows, cell_size)?;
        let (weights, areas) = volume_weights(&boundary, &x, &y, cell_size, count)?;
        let neighbors = neighbor_pairs(rows, columns, cell_size, &weights)?;
        let tolerance = TOLERANCE.max(cell_area * 1e-10);
        let mut coverage = filled(areas.len(), CellCoverage::Empty)?;
        for (coverage, area) in coverage.iter_mut().zip(areas) {
            *coverage = if (area - cell_area).abs() <= tolerance {
                CellCoverage::Full
            } else if area > tolerance {
                CellCoverage::Partial
            } else {
                CellCoverage::Empty
            };
        }
        Ok(Self {
            boundary,
            floor,
            top,
            cell_size,
            x,
            y,
            weights,
            coverage,
            neighbors,
            capacity,
        })
    }

    pub fn height_at(&self, heights: &[f64], point: Vec2) -> Result<f64> {
        if !self.boundary.contains(point)? {
            return Err(GeometryError::Invalid(
                "height query point must lie inside the surface boundary",
            ));
        }
        let x_offset = (point.x() - self.x[0]) / self.cell_size;
        let y_offset = (point.y() - self.y[0]) / self.cell_size;
        let xi = (x_offset.floor().max(0.0) as usize).min(self.x.len() - 2);
        let yi = (y_offset.floor().max(0.0) as usize).min(self.y.len() - 2);
        let xf = (x_offset - xi as f64).clamp(0.0, 1.0);
        let yf = (y_offset - yi as f64).clamp(0.0, 1.0);
        let index = yi * self.x.len() + xi;
        let lower = heights[index] + xf * (heights[index + 1] - heights[index]);
        let upper_index = index + self.x.len();
        let upper = heights[upper_index] + xf * (heights[upper_index + 1] - heights[upper_index]);
        Ok(lower + yf * (upper - lower))
    }
}

fn node_count(extent: f64, cell_size: f64) -> Result<usize> {
    let cells = (extent / cell_size).ceil().max(1.0);
    if !cells.is_finite() || cells >= MAX_GRID_NODES as f64 {
        return Err(GeometryError::Allocation(
            "height field exceeds MAX_GRID_NODES",
        ));
    }
    Ok(cells as usize + 1)
}

fn coordinates(minimum: f64, count: usize, step: f64) -> Result<Vec<f64>> {
    let mut coordinates = filled(count, 0.0)?;
    for (index, coordinate) in coordinates.iter_mut().enumerate() {
        *coordinate = minimum + step * index as f64;
    }
    if coordinates.iter().any(|value| !value.is_finite())
        || coordinates.windows(2).any(|pair| pair[0] >= pair[1])
    {
        return Err(GeometryError::Numerical(
            "surface grid coordinates must be finite and strictly increasing",
        ));
    }
    Ok(coordinates)
}

fn neighbor_pairs(
    rows: usize,
    columns: usize,
    step: f64,
    weights: &[f64],
) -> Result<NeighborPairs> {
    let mut pairs = NeighborPairs {
        first: Vec::new(),
        second: Vec::new(),
        distance: Vec::new(),
    };
    let capacity = weights
        .len()
        .checked_mul(4)
        .ok_or(GeometryError::Allocation("neighbor pair capacity overflow"))?;
    pairs
        .first
        .try_reserve_exact(capacity)
        .map_err(|_| GeometryError::Allocation("neighbor allocation failed"))?;
    pairs
        .second
        .try_reserve_exact(capacity)
        .map_err(|_| GeometryError::Allocation("neighbor allocation failed"))?;
    pairs
        .distance
        .try_reserve_exact(capacity)
        .map_err(|_| GeometryError::Allocation("neighbor allocation failed"))?;
    for y in 0..rows {
        for x in 0..columns {
            for (dy, dx) in [(0_isize, 1_isize), (1, 0), (1, 1), (1, -1)] {
                let Some(ny) = y.checked_add_signed(dy).filter(|ny| *ny < rows) else {
                    continue;
                };
                let Some(nx) = x.checked_add_signed(dx).filter(|nx| *nx < columns) else {
                    continue;
                };
                let first = y * columns + x;
                let second = ny * columns + nx;
                if weights[first] > 0.0 && weights[second] > 0.0 {
                    pairs.first.push(first);
                    pairs.second.push(second);
                    pairs.distance.push(step * (dx as f64).hypot(dy as f64));
                }
            }
        }
    }
    Ok(pairs)
}

fn volume_weights(
    boundary: &Polygon2,
    x: &[f64],
    y: &[f64],
    step: f64,
    count: usize,
) -> Result<(Vec<f64>, Vec<f64>)> {
    let mut weights = filled(count, 0.0)?;
    let columns = x.len() - 1;
    let rows = y.len() - 1;
    let mut areas = filled(rows * columns, 0.0)?;
    for triangle in triangulate(boundary)? {
        let min_x = triangle.iter().map(|p| p.x()).fold(f64::INFINITY, f64::min);
        let max_x = triangle
            .iter()
            .map(|p| p.x())
            .fold(f64::NEG_INFINITY, f64::max);
        let min_y = triangle.iter().map(|p| p.y()).fold(f64::INFINITY, f64::min);
        let max_y = triangle
            .iter()
            .map(|p| p.y())
            .fold(f64::NEG_INFINITY, f64::max);
        let first_x = ((min_x - x[0]) / step).floor().max(0.0) as usize;
        let last_x = (((max_x - x[0]) / step).floor().max(0.0) as usize).min(columns - 1);
        let first_y = ((min_y - y[0]) / step).floor().max(0.0) as usize;
        let last_y = (((max_y - y[0]) / step).floor().max(0.0) as usize).min(rows - 1);
        for yi in first_y..=last_y {
            for xi in first_x..=last_x {
                let mut clipped = triangle.to_vec();
                for (axis, bound, greater) in [
                    (0, x[xi], true),
                    (0, x[xi] + step, false),
                    (1, y[yi], true),
                    (1, y[yi] + step, false),
                ] {
                    clipped = clip_axis(clipped, axis, bound, greater)?;
                }
                if clipped.len() < 3 {
                    continue;
                }
                let normalized = clipped
                    .iter()
                    .map(|point| Vec2::new((point.x() - x[xi]) / step, (point.y() - y[yi]) / step))
                    .collect::<Result<Vec<_>>>()?;
                let corner_weights = bilinear_weights(&normalized);
                areas[yi * columns + xi] += sum(corner_weights) * step * step;
                for (dy, dx, weight) in [
                    (0, 0, corner_weights[0]),
                    (0, 1, corner_weights[1]),
                    (1, 0, corner_weights[2]),
                    (1, 1, corner_weights[3]),
                ] {
                    weights[(yi + dy) * x.len() + xi + dx] += weight * step * step;
                }
            }
        }
    }
    let area = boundary.signed_area().abs();
    if weights
        .iter()
        .any(|weight| !weight.is_finite() || *weight < -TOLERANCE * area)
    {
        return Err(GeometryError::Numerical(
            "height field integration produced an invalid node weight",
        ));
    }
    for weight in &mut weights {
        *weight = weight.max(0.0);
    }
    let represented_area = sum(weights.iter().copied());
    if !represented_area.is_finite() || represented_area <= 0.0 {
        return Err(GeometryError::Numerical(
            "height field integration produced no finite surface area",
        ));
    }
    let scale = area / represented_area;
    for weight in &mut weights {
        *weight *= scale;
    }
    for value in &mut areas {
        *value *= scale;
    }
    if weights.iter().chain(&areas).any(|value| !value.is_finite()) {
        return Err(GeometryError::Numerical(
            "height field area normalization must be finite",
        ));
    }
    Ok((weights, areas))
}

fn cross(a: Vec2, b: Vec2, c: Vec2) -> Result<f64> {
    b.checked_sub(a)?.cross(c.checked_sub(b)?)
}

fn triangulate(boundary: &Polygon2) -> Result<Vec<[Vec2; 3]>> {
    let mut vertices = boundary.vertices().to_vec();
    if boundary.signed_area() < 0.0 {
        vertices.reverse();
    }
    loop {
        if vertices.len() <= 3 {
            break;
        }
        let mut simplified = Vec::with_capacity(vertices.len());
        for (index, current) in vertices.iter().enumerate() {
            if cross(
                vertices[(index + vertices.len() - 1) % vertices.len()],
                *current,
                vertices[(index + 1) % vertices.len()],
            )?
            .abs()
                > TOLERANCE
            {
                simplified.push(*current);
            }
        }
        if simplified.len() == vertices.len() {
            break;
        }
        vertices = simplified;
    }
    if vertices.len() < 3 {
        return Err(GeometryError::Numerical(
            "simple polygon could not be triangulated",
        ));
    }
    let mut triangles = Vec::with_capacity(vertices.len() - 2);
    while vertices.len() > 3 {
        let mut ear = None;
        for (index, current) in vertices.iter().enumerate() {
            let previous_index = (index + vertices.len() - 1) % vertices.len();
            let next_index = (index + 1) % vertices.len();
            let previous = vertices[previous_index];
            let following = vertices[next_index];
            if cross(previous, *current, following)? <= TOLERANCE {
                continue;
            }
            let mut occupied = false;
            for (candidate_index, candidate) in vertices.iter().enumerate() {
                if [previous_index, index, next_index].contains(&candidate_index) {
                    continue;
                }
                if in_triangle(*candidate, previous, *current, following)? {
                    occupied = true;
                    break;
                }
            }
            if !occupied {
                ear = Some(index);
                triangles.push([previous, *current, following]);
                break;
            }
        }
        let index = ear.ok_or(GeometryError::Numerical(
            "simple polygon could not be triangulated",
        ))?;
        vertices.remove(index);
    }
    triangles.push([vertices[0], vertices[1], vertices[2]]);
    Ok(triangles)
}

fn in_triangle(point: Vec2, a: Vec2, b: Vec2, c: Vec2) -> Result<bool> {
    Ok(
        b.checked_sub(a)?.cross(point.checked_sub(a)?)? >= -TOLERANCE
            && c.checked_sub(b)?.cross(point.checked_sub(b)?)? >= -TOLERANCE
            && a.checked_sub(c)?.cross(point.checked_sub(c)?)? >= -TOLERANCE,
    )
}

fn clip_axis(polygon: Vec<Vec2>, axis: usize, bound: f64, keep_greater: bool) -> Result<Vec<Vec2>> {
    let Some(mut previous) = polygon.last().copied() else {
        return Ok(Vec::new());
    };
    let mut previous_value = previous.coordinates()[axis];
    let inside = |value: f64| {
        if keep_greater {
            value >= bound
        } else {
            value <= bound
        }
    };
    let mut previous_inside = inside(previous_value);
    let mut result = Vec::with_capacity(polygon.len() + 1);
    for current in polygon {
        let current_value = current.coordinates()[axis];
        let current_inside = inside(current_value);
        if current_inside != previous_inside {
            let fraction = (bound - previous_value) / (current_value - previous_value);
            result.push(if axis == 0 {
                Vec2::new(
                    bound,
                    previous.y() + fraction * (current.y() - previous.y()),
                )?
            } else {
                Vec2::new(
                    previous.x() + fraction * (current.x() - previous.x()),
                    bound,
                )?
            });
        }
        if current_inside {
            result.push(current);
        }
        previous = current;
        previous_value = current_value;
        previous_inside = current_inside;
    }
    Ok(result)
}

fn bilinear_weights(polygon: &[Vec2]) -> [f64; 4] {
    let mut doubled_area = 0.0;
    let mut moment_x = 0.0;
    let mut moment_y = 0.0;
    let mut mixed = 0.0;
    for (index, current) in polygon.iter().enumerate() {
        let following = polygon[(index + 1) % polygon.len()];
        let cross = current.x() * following.y() - following.x() * current.y();
        doubled_area += cross;
        moment_x += (current.x() + following.x()) * cross;
        moment_y += (current.y() + following.y()) * cross;
        mixed += (2.0 * current.x() * current.y()
            + current.x() * following.y()
            + following.x() * current.y()
            + 2.0 * following.x() * following.y())
            * cross;
    }
    let area = 0.5 * doubled_area;
    if area <= TOLERANCE {
        return [0.0; 4];
    }
    let ix = moment_x / 6.0;
    let iy = moment_y / 6.0;
    let ixy = mixed / 24.0;
    [area - ix - iy + ixy, ix - ixy, iy - ixy, ixy]
}
