use vexide::math::Angle;
use core::fmt::{self, Write as FmtWrite};
use core::ops::{Add, Sub, Mul, Div};

fn format_to_string(args: fmt::Arguments<'_>) -> String {
    let mut s = String::new();
    FmtWrite::write_fmt(&mut s, args).expect("writing to String cannot fail");
    s
}

// --- Free functions ---

pub fn interp(v1: f64, v2: f64, scale: f64) -> f64 {
    v1 * (1.0 - scale) + v2 * scale
}

pub fn angle_difference(theta_1: Angle, theta_2: Angle) -> Angle {
    (theta_1 - theta_2).wrapped_half()
}

pub fn normalize_to_360(theta: Angle) -> Angle {
    theta.wrapped_full()
}

pub fn convert_to_180(theta: Angle) -> Angle {
    theta.wrapped_half()
}

pub fn average_angle(theta_1: Angle, theta_2: Angle) -> Angle {
    let t1 = normalize_to_360(theta_1);
    let diff = angle_difference(theta_2, t1);
    normalize_to_360(t1 + diff / 2.0)
}

// --- Pose ---

#[derive(Clone, Copy, PartialEq)]
pub struct Pose {
    pub x: f64,
    pub y: f64,
    pub heading: Angle,
}

impl Pose {
    pub fn new(x: f64, y: f64, heading: Angle) -> Self {
        Self { x, y, heading }
    }

    pub fn set(&mut self, x: Option<f64>, y: Option<f64>, heading: Option<Angle>) {
        if let Some(v) = x { self.x = v; }
        if let Some(v) = y { self.y = v; }
        if let Some(v) = heading { self.heading = v; }
    }

    pub fn angle_to(&self, other: &Pose) -> Angle {
        normalize_to_360(Angle::atan2(other.y - self.y, other.x - self.x))
    }

    pub fn signed_distance_to(&self, other: &Pose) -> f64 {
        let mut distance = self.distance_to(other);
        let line_angle = normalize_to_360(Angle::atan2(other.y - self.y, other.x - self.x));
        let heading_error = angle_difference(self.heading, line_angle);
        if heading_error.abs() > Angle::from_degrees(90.0) {
            distance *= -1.0;
        }
        distance
    }

    pub fn heading_to(&self, other: &Pose) -> Angle {
        angle_difference(other.heading, self.heading)
    }

    pub fn distance_to(&self, other: &Pose) -> f64 {
        ((other.x - self.x).powi(2) + (other.y - self.y).powi(2)).sqrt()
    }

    pub fn get_rounded(&self) -> String {
        format_to_string(format_args!(
            "X: {:.2}, Y: {:.2}, Heading: {:.2}",
            self.x,
            self.y,
            self.heading.as_degrees()
        ))
    }
}

impl Add for Pose {
    type Output = Self;
    fn add(self, rhs: Self) -> Self {
        Self::new(self.x + rhs.x, self.y + rhs.y, self.heading + rhs.heading)
    }
}

impl Sub for Pose {
    type Output = Self;
    fn sub(self, rhs: Self) -> Self {
        Self::new(self.x - rhs.x, self.y - rhs.y, self.heading - rhs.heading)
    }
}

impl Div<f64> for Pose {
    type Output = Self;
    fn div(self, scalar: f64) -> Self {
        Self::new(self.x / scalar, self.y / scalar, self.heading)
    }
}

impl fmt::Display for Pose {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_fmt(format_args!(
            "X: {}, Y: {}, Heading: {}°",
            self.x,
            self.y,
            self.heading.as_degrees()
        ))
    }
}

impl fmt::Debug for Pose {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_fmt(format_args!(
            "X: {}, Y: {}, Heading: {}°",
            self.x,
            self.y,
            self.heading.as_degrees()
        ))
    }
}

impl From<Pose> for (f64, f64, Angle) {
    fn from(pose: Pose) -> (f64, f64, Angle) {
        (pose.x, pose.y, pose.heading)
    }
}

// --- CartesianVector ---

#[derive(Clone, Copy)]
pub struct CartesianVector {
    pub x: f64,
    pub y: f64,
}

impl CartesianVector {
    pub fn new(x: f64, y: f64) -> Self {
        Self { x, y }
    }

    pub fn magnitude(&self) -> f64 {
        (self.x.powi(2) + self.y.powi(2)).sqrt()
    }

    pub fn rotate(&self, angle: Angle) -> Self {
        let rad = angle.as_radians();
        Self::new(
            self.x * rad.cos() - self.y * rad.sin(),
            self.x * rad.sin() + self.y * rad.cos(),
        )
    }

    pub fn to_polar(&self) -> PolarVector {
        PolarVector::new(self.magnitude(), self.y.atan2(self.x).to_degrees())
    }
}

impl Add for CartesianVector {
    type Output = Self;
    fn add(self, rhs: Self) -> Self {
        Self::new(self.x + rhs.x, self.y + rhs.y)
    }
}

impl Sub for CartesianVector {
    type Output = Self;
    fn sub(self, rhs: Self) -> Self {
        Self::new(self.x - rhs.x, self.y - rhs.y)
    }
}

impl Mul for CartesianVector {
    type Output = Self;
    fn mul(self, rhs: Self) -> Self {
        Self::new(self.x * rhs.x, self.y * rhs.y)
    }
}

impl Div for CartesianVector {
    type Output = Self;
    fn div(self, rhs: Self) -> Self {
        Self::new(self.x / rhs.x, self.y / rhs.y)
    }
}

impl fmt::Display for CartesianVector {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_fmt(format_args!(
            "CartesianVector(x={:.2}, y={:.2})",
            self.x,
            self.y
        ))
    }
}

impl fmt::Debug for CartesianVector {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_fmt(format_args!(
            "CartesianVector(x={}, y={})",
            self.x,
            self.y
        ))
    }
}

// --- PolarVector ---

#[derive(Clone, Copy)]
pub struct PolarVector {
    pub magnitude: f64,
    pub theta: f64,
}

impl PolarVector {
    pub fn new(magnitude: f64, theta: f64) -> Self {
        Self { magnitude, theta }
    }

    pub fn rotate(&self, theta: f64) -> Self {
        Self::new(self.magnitude, self.theta + theta)
    }

    pub fn to_cartesian(&self) -> CartesianVector {
        CartesianVector::new(
            self.magnitude * self.theta.to_radians().cos(),
            self.magnitude * self.theta.to_radians().sin(),
        )
    }
}

impl Add for PolarVector {
    type Output = Self;
    fn add(self, rhs: Self) -> Self {
        Self::new(self.magnitude + rhs.magnitude, self.theta + rhs.theta)
    }
}

impl Sub for PolarVector {
    type Output = Self;
    fn sub(self, rhs: Self) -> Self {
        Self::new(self.magnitude - rhs.magnitude, self.theta - rhs.theta)
    }
}

impl Mul for PolarVector {
    type Output = Self;
    fn mul(self, rhs: Self) -> Self {
        Self::new(self.magnitude * rhs.magnitude, self.theta + rhs.theta)
    }
}

impl Div for PolarVector {
    type Output = Self;
    fn div(self, rhs: Self) -> Self {
        Self::new(self.magnitude / rhs.magnitude, self.theta - rhs.theta)
    }
}

impl fmt::Display for PolarVector {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_fmt(format_args!(
            "PolarVector(magnitude={:.2}, theta={:.2})",
            self.magnitude,
            self.theta
        ))
    }
}

impl fmt::Debug for PolarVector {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_fmt(format_args!(
            "PolarVector(magnitude={}, theta={})",
            self.magnitude,
            self.theta
        ))
    }
}

