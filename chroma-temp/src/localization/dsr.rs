// Distance sensor resets: snap one pose axis to a field wall.
use vexide::math::Angle;
use vexide::prelude::*;
use crate::helpers::{CartesianVector, PolarVector, Pose};
use crate::MM_PER_INCH;

/// Inner wall-to-wall field size, in inches.
pub const FIELD_WIDTH: f64 = 140.5;
pub const FIELD_HEIGHT: f64 = 140.5;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Wall {
    /// x = FIELD_WIDTH
    Right,
    /// y = FIELD_HEIGHT
    Top,
    /// x = 0
    Left,
    /// y = 0
    Bottom,
}

impl Wall {
    /// Direction the sensor faces when pointing straight at this wall.
    pub fn angle(self) -> Angle {
        match self {
            Wall::Right => Angle::from_degrees(0.0),
            Wall::Top => Angle::from_degrees(90.0),
            Wall::Left => Angle::from_degrees(180.0),
            Wall::Bottom => Angle::from_degrees(270.0),
        }
    }
}

pub struct Dsrs {
    pub left: Dsr,
    pub front: Dsr,
    pub right: Dsr,
    pub back: Dsr,
}

pub struct Dsr {
    pub sensor: DistanceSensor,
    /// Sensor position and facing relative to the tracking center, in the robot frame.
    pub offset: Pose,
    /// Fixed correction added to every reading, in inches.
    pub distance_offset: f64,
}

impl Dsr {
    pub fn new(sensor: DistanceSensor, offset: Pose, distance_offset: f64) -> Self {
        Self { sensor, offset, distance_offset }
    }
    /// Corrected distance to the detected object in inches, or `None` if nothing is detected or the reading is invalid (exactly 0).
    pub fn distance(&self) -> Option<f64> {
        let object = self.sensor.object().ok()??;
        (object.distance != 0).then(|| object.distance as f64 / MM_PER_INCH + self.distance_offset)
    }

    /// `pose` with the axis perpendicular to `wall` replaced by the sensor reading.
    /// Returns `None` if the reading is missing, longer than the field, or shifts that axis by more than 10".
    pub fn get_pose(&self, pose: Pose, wall: Wall) -> Option<Pose> {
        let distance = self.distance()?;
        if distance > FIELD_WIDTH.max(FIELD_HEIGHT) {
            return None;
        }

        let global_offset = CartesianVector::new(self.offset.x, self.offset.y).rotate(pose.heading);
        let heading_against_wall = pose.heading + self.offset.heading - wall.angle();
        let perpendicular_distance = heading_against_wall.as_radians().cos() * distance;

        let reset = match wall {
            Wall::Right => Pose::new(FIELD_WIDTH - perpendicular_distance - global_offset.x, pose.y, pose.heading),
            Wall::Top => Pose::new(pose.x, FIELD_HEIGHT - perpendicular_distance - global_offset.y, pose.heading),
            Wall::Left => Pose::new(perpendicular_distance - global_offset.x, pose.y, pose.heading),
            Wall::Bottom => Pose::new(pose.x, perpendicular_distance - global_offset.y, pose.heading),
        };

        let axis_error = match wall {
            Wall::Right | Wall::Left => (reset.x - pose.x).abs(),
            Wall::Top | Wall::Bottom => (reset.y - pose.y).abs(),
        };
        (axis_error <= 10.0).then_some(reset)
    }
}

pub struct GoalReset {
    pub goal_radius: f64,
    pub tracking_offset: f64,
}

impl GoalReset {
    pub fn new(goal_radius: f64, tracking_offset: f64) -> Self {
        Self { goal_radius, tracking_offset }
    }

    pub fn reset(&self, pose: Pose, goal_position: Pose) -> Pose {
        let goal_vector = PolarVector::new(self.goal_radius + self.tracking_offset, pose.heading.as_degrees()).to_cartesian();
        Pose::new(goal_vector.x + goal_position.x, goal_vector.y + goal_position.y, Angle::ZERO)
    }
}