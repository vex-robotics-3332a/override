use vexide::{prelude::*, startup::banner::print};
use alloc::rc::Rc;
use vexide::sync::Mutex;
use vexide::math::Angle;
use super::odometry::{Odometry, Encoder};
use crate::helpers::Pose;

pub struct Localization {
    pub odometry: Odometry,
    pub imu: InertialSensor,
    //expandable later for distance sensors etc.
    pub pose: Rc<Mutex<Pose>>, 

    previous_heading: Angle
}

impl Localization {
    pub fn new(odometry: Odometry, imu: InertialSensor) -> Self {
        Self {
            odometry,
            imu,
            pose: Rc::new(Mutex::new(Pose::new(0.0, 0.0, Angle::ZERO))),
            previous_heading: Angle::from_degrees(0.0)
        }
    }

    pub async fn run(mut self) {
        loop {
            let mut pose = self.pose.try_lock().expect("Pose locked while changing!");
            // The IMU reports heading clockwise-positive; negate it so the whole
            // localization/control stack is counterclockwise-positive (standard
            // math convention), matching the motion profile's frame.
            let current_heading = -self.imu.heading().unwrap_or_default();
            let odometry_update = self.odometry.update(current_heading, self.previous_heading);

            *pose = *pose + Pose::new(odometry_update.x, odometry_update.y, Angle::ZERO);
            pose.heading = current_heading;

            self.previous_heading = current_heading;
            drop(pose);
            
            sleep(Controller::UPDATE_INTERVAL).await;
        }
    }
}