use vexide::{smart::motor::BrakeMode, sync::Mutex, controller::ControllerState};
use alloc::rc::Rc;
use vexide::prelude::*;
use std::time::Instant;
use crate::{SLEEP_INTERVAL, localization::localization::Localization, subsystems::drivetrain::Drivetrain, telemetry};
use crate::helpers::Pose;
use crate::telemetry::*;
use crate::configs::ChassisConfig;

pub struct Chassis {
    pub drivetrain: Rc<Mutex<Drivetrain>>,
    pub localization: Option<Localization>,
    pub imu: Rc<Mutex<InertialSensor>>,
    pub pose: Rc<Mutex<Pose>>,
    pub config: ChassisConfig,
}

impl Chassis {
    pub fn new(drivetrain: Rc<Mutex<Drivetrain>>, localization: Localization, config: ChassisConfig) -> Self {
        let pose = Rc::clone(&localization.pose);
        let imu = Rc::clone(&localization.imu);
        Self {
            drivetrain,
            localization: Some(localization),
            imu,
            pose,
            config,
        }
    }

    pub async fn init(&mut self) {
        let localization = self.localization.take().expect("init called twice or localization missing");

        {
            let mut imu = localization
                .imu
                .try_lock()
                .expect("IMU locked during calibration");
            if imu.calibrate().await.is_err() {
                println!("Calibration failed!");
            }
        }

        spawn(localization.run()).detach();
        spawn(Drivetrain::run_vel_ticking(Rc::clone(&self.drivetrain))).detach();
    }

    pub fn get_pose(&self) -> Pose {
        *self.pose.try_lock().expect("Pose unexpectedly locked while reading!")
    }

    pub fn set_pose(&self, new_pose: Pose) {
        *self.pose.try_lock().expect("Pose unexpectedly locked while writing!") = new_pose;
        // pose uses CCW+, IMU heading() is CW+, and localization reads -imu.heading().
        self.imu.try_lock().expect("IMU locked while resetting heading").set_heading(-new_pose.heading).ok();
    }

    pub fn set_brake_mode(&self, brake_mode: BrakeMode) {
        let mut dt = self.drivetrain.try_lock().expect("Dt locked while setting brake mode!");
        dt.set_brake_mode(brake_mode);
    }

    pub async fn log_pose(pose: Rc<Mutex<Pose>>) {
        let pose_capture = TelemetryFile::new_csv(
            "pose_capture.txt",
            &["timestamp", "x", "y", "heading"],
            TelemetryFile::DEFAULT_BUFFER_SIZE,
            TelemetryFile::DEFAULT_ITER_TIMEOUT,
        );

        let clock = Instant::now();

        loop {
            let pose = *pose.try_lock().expect("pose locked when logging!");
            let x = (pose.x * 100.0).round() / 100.0;
            let y = (pose.y * 100.0).round() / 100.0;
            let heading = (pose.heading.as_degrees() * 100.0).round() / 100.0;
            telemetry!(pose_capture, clock.elapsed().as_millis(), x, y, heading);
            sleep(SLEEP_INTERVAL).await;
        }
    }

    pub fn op_control_tick(&mut self, controller_state: &ControllerState) {
        let left_stick = controller_state.left_stick;
        let right_stick = controller_state.right_stick;
        
        let linear_power = left_stick.y() * 12.0;
        let angular_power = right_stick.x() * 12.0;

        let mut dt = self.drivetrain.try_lock().expect("Dt locked in op control tick");
        dt.left_side.spin_v(linear_power + angular_power);
        dt.right_side.spin_v(linear_power - angular_power);
    }
}
