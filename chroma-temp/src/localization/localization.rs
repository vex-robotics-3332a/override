use vexide::{prelude::*, startup::banner::print};
use vexide::math::Angle;
use super::odometry::{Odometry, Encoder};
use crate::helpers::Pose;

pub struct Localization<L: Encoder, H: Encoder> {
    pub odometry: Odometry<L, H>,
    pub imu: InertialSensor,
    //expandable later for distance sensors etc.
    pub pose: Pose, 

    previous_heading: Angle
}

impl<L: Encoder, H: Encoder> Localization<L, H> {
    pub fn new(odometry: Odometry<L, H>, imu: InertialSensor) -> Self {
        Self {
            odometry,
            imu,
            pose: Pose::new(0.0, 0.0, Angle::ZERO),
            previous_heading: Angle::from_degrees(0.0)
        }
    }

    pub async fn run(mut self) {
        loop {
            let current_heading = self.imu.heading().unwrap_or_default();
            let odometry_update = self.odometry.update(current_heading, self.previous_heading);

            self.pose = self.pose + Pose::new(odometry_update.x, odometry_update.y, Angle::ZERO);
            self.pose.heading = current_heading;

            self.previous_heading = current_heading;
            
            sleep(Controller::UPDATE_INTERVAL).await;
        }
    }
}