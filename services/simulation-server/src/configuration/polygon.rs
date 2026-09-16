//! Polygon checks used by configuration validation, before scene construction.

use super::Coordinate2;
use crate::error::{ConfigurationError, ErrorKind, Result};

const TOLERANCE: f64 = 1e-12;

fn failure(message: &str) -> ConfigurationError {
    ConfigurationError::new(ErrorKind::Geometry, "$.boundary_xy_m", message)
}

fn orientation(a: Coordinate2, b: Coordinate2, c: Coordinate2) -> Result<i8> {
    let cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    if !cross.is_finite() {
        return Err(failure("polygon intersection calculation must be finite"));
    }
    Ok(if cross.abs() <= TOLERANCE {
        0
    } else if cross > 0.0 {
        1
    } else {
        -1
    })
}

fn point_on_segment(point: Coordinate2, start: Coordinate2, end: Coordinate2) -> Result<bool> {
    Ok(orientation(start, end, point)? == 0
        && (0..2).all(|axis| {
            point[axis] >= start[axis].min(end[axis]) - TOLERANCE
                && point[axis] <= start[axis].max(end[axis]) + TOLERANCE
        }))
}

fn intersects(a: Coordinate2, b: Coordinate2, c: Coordinate2, d: Coordinate2) -> Result<bool> {
    let first = orientation(a, b, c)?;
    let second = orientation(a, b, d)?;
    let third = orientation(c, d, a)?;
    let fourth = orientation(c, d, b)?;
    Ok((first * second < 0 && third * fourth < 0)
        || (first == 0 && point_on_segment(c, a, b)?)
        || (second == 0 && point_on_segment(d, a, b)?)
        || (third == 0 && point_on_segment(a, c, d)?)
        || (fourth == 0 && point_on_segment(b, c, d)?))
}

pub(super) fn validate(vertices: &[Coordinate2]) -> Result<()> {
    if vertices.len() < 3 {
        return Err(failure("polygon must contain at least 3 vertices"));
    }
    for (index, vertex) in vertices.iter().enumerate() {
        if vertices[..index].contains(vertex) {
            return Err(failure("polygon must contain unique vertices"));
        }
    }
    // Compensated accumulation preserves small area terms during cancellation.
    let mut partials: Vec<f64> = Vec::new();
    for index in 0..vertices.len() {
        let left = vertices[index];
        let right = vertices[(index + 1) % vertices.len()];
        let mut term = left[0] * right[1] - right[0] * left[1];
        if !term.is_finite() {
            return Err(failure("polygon area calculation must be finite"));
        }
        let mut count = 0;
        for part in 0..partials.len() {
            let mut other = partials[part];
            if term.abs() < other.abs() {
                std::mem::swap(&mut term, &mut other);
            }
            let high = term + other;
            let low = other - (high - term);
            if low != 0.0 {
                partials[count] = low;
                count += 1;
            }
            term = high;
        }
        partials.truncate(count);
        partials.push(term);
    }
    let area: f64 = partials.iter().rev().sum();
    if !area.is_finite() {
        return Err(failure("polygon area calculation must be finite"));
    }
    if area.abs() <= TOLERANCE {
        return Err(failure("polygon must enclose a non-zero area"));
    }
    for first in 0..vertices.len() {
        for second in (first + 1)..vertices.len() {
            if second == first + 1 || (first == 0 && second == vertices.len() - 1) {
                continue;
            }
            if intersects(
                vertices[first],
                vertices[(first + 1) % vertices.len()],
                vertices[second],
                vertices[(second + 1) % vertices.len()],
            )? {
                return Err(failure("polygon must not self-intersect"));
            }
        }
    }
    Ok(())
}

pub(super) fn contains(vertices: &[Coordinate2], point: Coordinate2) -> Result<bool> {
    let mut inside = false;
    for index in 0..vertices.len() {
        let start = vertices[index];
        let end = vertices[(index + 1) % vertices.len()];
        if point_on_segment(point, start, end)? {
            return Ok(true);
        }
        if (start[1] > point[1]) == (end[1] > point[1]) {
            continue;
        }
        let intersection_x =
            start[0] + (point[1] - start[1]) * (end[0] - start[0]) / (end[1] - start[1]);
        if point[0] < intersection_x {
            inside = !inside;
        }
    }
    Ok(inside)
}

#[cfg(test)]
mod tests {
    use super::{contains, validate};

    #[test]
    fn concave_boundary_contains_edges_but_excludes_the_recess() {
        let polygon = [
            [0.0, 0.0],
            [3.0, 0.0],
            [3.0, 1.0],
            [1.0, 1.0],
            [1.0, 3.0],
            [0.0, 3.0],
        ];
        validate(&polygon).unwrap();
        for point in [[0.0, 0.0], [1.0, 1.0], [2.0, 1.0], [0.5, 2.0]] {
            assert!(contains(&polygon, point).unwrap());
        }
        for point in [[2.0, 2.0], [-0.1, 1.0], [3.1, 0.5]] {
            assert!(!contains(&polygon, point).unwrap());
        }
    }

    #[test]
    fn touching_non_adjacent_edges_are_invalid() {
        let polygon = [[0.0, 0.0], [3.0, 0.0], [3.0, 3.0], [1.5, 0.0], [0.0, 3.0]];
        assert!(validate(&polygon).is_err());
    }

    #[test]
    fn finite_coordinates_cannot_overflow_polygon_arithmetic() {
        assert!(validate(&[[0.0, 0.0], [1e308, 0.0], [0.0, 1e308]]).is_err());
    }
}
