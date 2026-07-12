use vexide::{smart::motor::BrakeMode, sync::Mutex};
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

    pub async fn init(&mut self) {
        let mut localization = self.localization.take().expect("No localization object/Localization prematurely moved!");
        if let Err(_) = localization.imu.calibrate().await {
            println!("Calibration failed!");
        }
        spawn(localization.run()).detach();
        spawn(Drivetrain::run_vel_ticking(Rc::clone(&self.drivetrain))).detach();
    }

    pub fn get_pose(&self) -> Pose {
        *self.pose.try_lock().expect("Pose unexpectedly locked while reading!")
    }

    pub fn set_brake_mode(&mut self, brake_mode: BrakeMode) {
        let mut dt = self.drivetrain.try_lock().expect("Dt locked while setting brake mode!");
        dt.set_brake_mode(brake_mode);
    }
}