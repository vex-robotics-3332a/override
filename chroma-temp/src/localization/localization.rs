use vexide::{prelude::*};
use alloc::rc::Rc;
use std::cell::Cell;
use vexide::sync::Mutex;
use vexide::math::Angle;
use super::odometry::{Odometry};
use crate::{SLEEP_INTERVAL, helpers::{CartesianVector, Pose}};

pub struct Localization {
    pub odometry: Odometry,
    pub imu: Rc<Mutex<InertialSensor>>,
    //expandable later for distance sensors etc.
    pub pose: Rc<Mutex<Pose>>, 
    pub cancel_odometry_tick: Rc<Cell<bool>>,

    previous_heading: Angle
}

impl Localization {
    pub fn new(odometry: Odometry, imu: InertialSensor) -> Self {
        Self {
            odometry,
            imu: Rc::new(Mutex::new(imu)),
            pose: Rc::new(Mutex::new(Pose::new(0.0, 0.0, Angle::ZERO))),
            previous_heading: Angle::from_degrees(0.0),
            cancel_odometry_tick: Rc::new(Cell::new(false))
        }
    }

    pub fn run_tick(&mut self) {
        let mut pose = self.pose.try_lock().expect("Pose locked while changing!");
        // imu is clockwise+ instead of counterclockwise+ so we need to invert it
        let current_heading = -self.imu.try_lock().expect("IMU locked while updating pose").heading().unwrap_or_default();

        if !self.cancel_odometry_tick.get() {
            let odometry_update = self.odometry.update(self.previous_heading, current_heading);

            *pose = *pose + Pose::new(odometry_update.x, odometry_update.y, Angle::ZERO);
            pose.heading = current_heading;
        }
        else {
            self.cancel_odometry_tick.set(false);
        }
        self.previous_heading = current_heading;
        drop(pose);
    }
}