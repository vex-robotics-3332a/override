use vexide::{smart::motor::BrakeMode, sync::Mutex};
use alloc::rc::Rc;
use vexide::prelude::*;
use crate::{SLEEP_INTERVAL, localization::{self, localization::Localization}, motion_control::ramsete::RamseteConfig, subsystems::drivetrain::Drivetrain, telemetry};
use crate::helpers::Pose;
use crate::telemetry::*;
pub struct Chassis {
    pub drivetrain: Rc<Mutex<Drivetrain>>,
    pub localization: Option<Localization>,

    pub ramsete_config: RamseteConfig,
    pub pose: Rc<Mutex<Pose>>
}

impl Chassis {
    pub fn new(drivetrain: Rc<Mutex<Drivetrain>>, localization: Localization, ramsete_config: RamseteConfig) -> Self {
        let pose = Rc::clone(&localization.pose);
        Self { 
            drivetrain, 
            localization: Some(localization),
            ramsete_config,
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

    pub fn set_pose(&mut self, new_pose: Pose) {
        *self.pose.try_lock().expect("Pose unexpectedly locked while writing!") = new_pose;
    }

    pub fn set_brake_mode(&mut self, brake_mode: BrakeMode) {
        let mut dt = self.drivetrain.try_lock().expect("Dt locked while setting brake mode!");
        dt.set_brake_mode(brake_mode);
    }

    pub async fn log_pose(&self) {
        let pose_capture = TelemetryFile::new_csv(
            "pose_capture.txt",
            &["timestamp", "x", "y", "heading"],
            TelemetryFile::DEFAULT_BUFFER_SIZE,
            TelemetryFile::DEFAULT_ITER_TIMEOUT,
        );

        loop {
            let pose = *self.pose.try_lock().expect("pose locked when logging!");
            telemetry!(pose_capture, pose.x, pose.y, pose.heading.as_degrees());
            sleep(SLEEP_INTERVAL).await;
        }
    }
}