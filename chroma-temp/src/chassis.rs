use vexide::sync::Mutex;
use alloc::rc::Rc;
use vexide::prelude::*;
use crate::{localization::{self, localization::Localization}, subsystems::drivetrain::Drivetrain};
use crate::helpers::Pose;
pub struct Chassis {
    pub drivetrain: Rc<Mutex<Drivetrain>>,
    pub localization: Option<Localization>,
    pub pose: Rc<Mutex<Pose>>
}

impl Chassis {
    pub fn new(drivetrain: Rc<Mutex<Drivetrain>>, localization: Localization) -> Self {
        let pose = Rc::clone(&localization.pose);
        Self { 
            drivetrain, 
            localization: Some(localization),
            pose
        } 
    }

    pub fn init(&mut self) {
        let localization = self.localization.take().expect("No localization object!");
        spawn(localization.run()).detach();
        spawn(Drivetrain::run_vel_ticking(Rc::clone(&self.drivetrain))).detach();
    }

    pub fn get_pose(&self) -> Pose {
        *self.pose.try_lock().expect("Pose unexpectedly locked while reading!")
    }
}