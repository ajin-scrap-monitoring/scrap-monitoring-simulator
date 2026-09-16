use super::{GeometryError, Result, TOLERANCE, Vec2, sum};

/// Engine resource limit for a single environment boundary.
pub const MAX_POLYGON_VERTICES: usize = 256;

/// An immutable simple polygon with an implicit closing edge.
#[derive(Clone, Debug, PartialEq)]
pub struct Polygon2 {
    vertices: Vec<Vec2>,
    signed_area: f64,
}

impl Polygon2 {
    pub fn new(vertices: Vec<Vec2>) -> Result<Self> {
        if vertices.len() < 3 {
            return Err(GeometryError::Invalid(
                "polygon must contain at least 3 vertices",
            ));
        }
        if vertices.len() > MAX_POLYGON_VERTICES {
            return Err(GeometryError::Allocation(
                "polygon exceeds MAX_POLYGON_VERTICES",
            ));
        }
        for (index, vertex) in vertices.iter().enumerate() {
            if vertices[..index].contains(vertex) {
                return Err(GeometryError::Invalid(
                    "polygon must contain unique vertices",
                ));
            }
        }
        let doubled_area = sum((0..vertices.len()).map(|index| {
            let a = vertices[index];
            let b = vertices[(index + 1) % vertices.len()];
            a.x() * b.y() - b.x() * a.y()
        }));
        if !doubled_area.is_finite() {
            return Err(GeometryError::Numerical(
                "polygon area calculation must be finite",
            ));
        }
        if doubled_area.abs() <= TOLERANCE {
            return Err(GeometryError::Invalid(
                "polygon must enclose a non-zero area",
            ));
        }
        for first in 0..vertices.len() {
            for second in first + 1..vertices.len() {
                if second == first + 1 || (first == 0 && second == vertices.len() - 1) {
                    continue;
                }
                if intersects(
                    vertices[first],
                    vertices[(first + 1) % vertices.len()],
                    vertices[second],
                    vertices[(second + 1) % vertices.len()],
                )? {
                    return Err(GeometryError::Invalid("polygon must not self-intersect"));
                }
            }
        }
        Ok(Self {
            vertices,
            signed_area: 0.5 * doubled_area,
        })
    }

    pub fn vertices(&self) -> &[Vec2] {
        &self.vertices
    }
    pub fn signed_area(&self) -> f64 {
        self.signed_area
    }
    pub fn edges(&self) -> impl Iterator<Item = (Vec2, Vec2)> + '_ {
        (0..self.vertices.len()).map(|index| {
            (
                self.vertices[index],
                self.vertices[(index + 1) % self.vertices.len()],
            )
        })
    }

    pub fn contains(&self, point: Vec2) -> Result<bool> {
        self.contains_with_boundary(point, true)
    }

    pub fn contains_with_boundary(&self, point: Vec2, include_boundary: bool) -> Result<bool> {
        for (start, end) in self.edges() {
            if on_segment(point, start, end)? {
                return Ok(include_boundary);
            }
        }
        let mut inside = false;
        for (start, end) in self.edges() {
            if (start.y() > point.y()) == (end.y() > point.y()) {
                continue;
            }
            let intersection =
                start.x() + (point.y() - start.y()) * (end.x() - start.x()) / (end.y() - start.y());
            if point.x() < intersection {
                inside = !inside;
            }
        }
        Ok(inside)
    }
}

fn orientation(a: Vec2, b: Vec2, c: Vec2) -> Result<i8> {
    let cross = b.checked_sub(a)?.cross(c.checked_sub(a)?)?;
    Ok(if cross.abs() <= TOLERANCE {
        0
    } else if cross > 0.0 {
        1
    } else {
        -1
    })
}

fn on_segment(point: Vec2, start: Vec2, end: Vec2) -> Result<bool> {
    Ok(orientation(start, end, point)? == 0
        && point.x() >= start.x().min(end.x()) - TOLERANCE
        && point.x() <= start.x().max(end.x()) + TOLERANCE
        && point.y() >= start.y().min(end.y()) - TOLERANCE
        && point.y() <= start.y().max(end.y()) + TOLERANCE)
}

fn intersects(a: Vec2, b: Vec2, c: Vec2, d: Vec2) -> Result<bool> {
    let first = orientation(a, b, c)?;
    let second = orientation(a, b, d)?;
    let third = orientation(c, d, a)?;
    let fourth = orientation(c, d, b)?;
    Ok((first * second < 0 && third * fourth < 0)
        || (first == 0 && on_segment(c, a, b)?)
        || (second == 0 && on_segment(d, a, b)?)
        || (third == 0 && on_segment(a, c, d)?)
        || (fourth == 0 && on_segment(b, c, d)?))
}
