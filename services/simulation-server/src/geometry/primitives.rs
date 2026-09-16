use super::{GeometryError, Result, TOLERANCE};

/// A finite position or displacement in the World XY plane, in meters.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Vec2 {
    x: f64,
    y: f64,
}

impl Vec2 {
    pub fn new(x: f64, y: f64) -> Result<Self> {
        if !x.is_finite() || !y.is_finite() {
            return Err(GeometryError::Invalid(
                "geometry coordinates must be finite",
            ));
        }
        Ok(Self { x, y })
    }

    pub fn x(self) -> f64 {
        self.x
    }
    pub fn y(self) -> f64 {
        self.y
    }
    pub fn coordinates(self) -> [f64; 2] {
        [self.x, self.y]
    }

    pub fn checked_sub(self, other: Self) -> Result<Self> {
        Self::new(self.x - other.x, self.y - other.y)
    }

    pub fn cross(self, other: Self) -> Result<f64> {
        let result = self.x * other.y - self.y * other.x;
        if !result.is_finite() {
            return Err(GeometryError::Numerical(
                "vector cross product must be finite",
            ));
        }
        Ok(result)
    }
}

impl TryFrom<[f64; 2]> for Vec2 {
    type Error = GeometryError;
    fn try_from(value: [f64; 2]) -> Result<Self> {
        Self::new(value[0], value[1])
    }
}

/// A finite position or displacement in the right-handed World XYZ frame.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Vec3 {
    x: f64,
    y: f64,
    z: f64,
}

impl Vec3 {
    pub fn new(x: f64, y: f64, z: f64) -> Result<Self> {
        if !x.is_finite() || !y.is_finite() || !z.is_finite() {
            return Err(GeometryError::Invalid(
                "geometry coordinates must be finite",
            ));
        }
        Ok(Self { x, y, z })
    }
    pub fn x(self) -> f64 {
        self.x
    }
    pub fn y(self) -> f64 {
        self.y
    }
    pub fn z(self) -> f64 {
        self.z
    }
    pub fn coordinates(self) -> [f64; 3] {
        [self.x, self.y, self.z]
    }
    pub fn checked_add(self, other: Self) -> Result<Self> {
        Self::new(self.x + other.x, self.y + other.y, self.z + other.z)
    }
    pub fn checked_sub(self, other: Self) -> Result<Self> {
        Self::new(self.x - other.x, self.y - other.y, self.z - other.z)
    }
    pub fn checked_mul(self, scalar: f64) -> Result<Self> {
        Self::new(self.x * scalar, self.y * scalar, self.z * scalar)
    }
    pub fn dot(self, other: Self) -> Result<f64> {
        let result = self.x * other.x + self.y * other.y + self.z * other.z;
        if !result.is_finite() {
            return Err(GeometryError::Numerical(
                "vector dot product must be finite",
            ));
        }
        Ok(result)
    }
    pub fn cross(self, other: Self) -> Result<Self> {
        Self::new(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )
    }
    pub fn length(self) -> f64 {
        self.x.hypot(self.y).hypot(self.z)
    }
    pub fn normalized(self) -> Result<Self> {
        let length = self.length();
        if length <= TOLERANCE || !length.is_finite() {
            return Err(GeometryError::Invalid(
                "cannot normalize a zero-length or unrepresentable vector",
            ));
        }
        Self::new(self.x / length, self.y / length, self.z / length)
    }
}

impl TryFrom<[f64; 3]> for Vec3 {
    type Error = GeometryError;
    fn try_from(value: [f64; 3]) -> Result<Self> {
        Self::new(value[0], value[1], value[2])
    }
}

/// A World-space ray parameterized by physical distance in meters.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Ray {
    origin: Vec3,
    direction: Vec3,
}

impl Ray {
    pub fn new(origin: Vec3, direction: Vec3) -> Result<Self> {
        if (direction.length() - 1.0).abs() > 1e-6 {
            return Err(GeometryError::Invalid(
                "ray direction must be a unit vector",
            ));
        }
        Ok(Self { origin, direction })
    }

    pub fn origin(self) -> Vec3 {
        self.origin
    }
    pub fn direction(self) -> Vec3 {
        self.direction
    }

    pub fn point_at(self, distance_m: f64) -> Result<Vec3> {
        if !distance_m.is_finite() || distance_m < 0.0 {
            return Err(GeometryError::Invalid(
                "ray distance must be finite and non-negative",
            ));
        }
        self.origin
            .checked_add(self.direction.checked_mul(distance_m)?)
    }
}

/// A non-degenerate World-space triangle; intersection belongs to measurement.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Triangle {
    vertices: [Vec3; 3],
}

impl Triangle {
    pub fn new(a: Vec3, b: Vec3, c: Vec3) -> Result<Self> {
        if b.checked_sub(a)?.cross(c.checked_sub(a)?)?.length() <= TOLERANCE {
            return Err(GeometryError::Invalid("triangle must be non-degenerate"));
        }
        Ok(Self {
            vertices: [a, b, c],
        })
    }

    pub fn vertices(self) -> [Vec3; 3] {
        self.vertices
    }
}
