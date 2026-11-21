use vexide::{smart::motor::BrakeMode, sync::Mutex, controller::ControllerState};
use alloc::rc::Rc;
use core::cell::Cell;
use core::pin::pin;
use futures_util::future::{select, Either};
use vexide::prelude::*;
use vexide::math::Angle;
use std::time::{Duration, Instant};
use crate::motion_control::pid::PIDController;
use crate::{SLEEP_INTERVAL, localization::localization::Localization, subsystems::drivetrain::Drivetrain, telemetry};
use crate::localization::dsr::{Dsr, GoalReset, Wall};
use crate::helpers::Pose;
use crate::telemetry::*;
use crate::configs::ChassisConfig;

pub struct Chassis {
    pub drivetrain: Rc<Mutex<Drivetrain>>,
    pub localization: Rc<Mutex<Localization>>,
    pub imu: Rc<Mutex<InertialSensor>>,
    pub pose: Rc<Mutex<Pose>>,
    pub goal_reset: GoalReset,
    pub turn_pid: PIDController,
    pub config: ChassisConfig,

    pub end_requested: Cell<bool>,
    pub driving: Cell<bool>,
}

impl Chassis {
    pub fn new(drivetrain: Rc<Mutex<Drivetrain>>, localization: Localization,  config: ChassisConfig, goal_reset: GoalReset, turn_pid: PIDController,) -> Self {
        let pose = Rc::clone(&localization.pose);
        let imu = Rc::clone(&localization.imu);
        Self {
            drivetrain,
            localization: Rc::new(Mutex::new(localization)),
            imu,
            pose,
            goal_reset,
            turn_pid,
            config,
            end_requested: Cell::new(false),
            driving: Cell::new(false),
        }
    }

    /// Ends the running `drive_along` on its next tick.
    pub fn end_motion(&self) {
        self.end_requested.set(true);
    }

    /// Polls `condition` every tick and ends the running `drive_along` once it returns `target`.
    /// Gives up when the motion finishes on its own.
    pub async fn end_motion_when(&self, condition: impl Fn() -> bool, target: bool) {
        loop {
            if condition() == target {
                self.end_motion();
                return;
            }
            sleep(SLEEP_INTERVAL).await;
            if !self.driving.get() {
                return;
            }
        }
    }

    /// Awaits `task` and ends the running `drive_along` if it returns `true`.
    pub async fn end_motion_when_task(&self, task: impl Future<Output = bool>) {
        if task.await {
            self.end_motion();
        }
    }

    pub async fn init(&mut self) {

        {
            let localization = self.localization.try_lock().expect("Localization locked during calibration");
            let mut imu = localization.imu
                .try_lock()
                .expect("IMU locked during calibration");
            // A status of 0 makes vexide restart its own timeout on every poll, so a missing IMU never returns.
            let calibrate = imu.calibrate();
            match select(pin!(calibrate), pin!(sleep(Duration::from_secs(5)))).await {
                Either::Left((Ok(()), _)) => {}
                _ => println!("Calibration failed!"),
            }
        }

        spawn(Drivetrain::run_vel_ticking(Rc::clone(&self.drivetrain))).detach();
    }

    pub fn start_localization(&mut self) {
        let localization = Rc::clone(&self.localization);

        localization.try_lock().expect("Localization locked while starting localization").odometry.reset();

        spawn( async move {
            loop {
                localization.try_lock().expect("Localization locked while running tick").run_tick();
                sleep(SLEEP_INTERVAL).await;
            }
        }).detach();
    }

    pub fn get_pose(&self) -> Pose {
        *self.pose.try_lock().expect("Pose unexpectedly locked while reading!")
    }

    pub fn set_pose(&self, new_pose: Pose) {
        *self.pose.try_lock().expect("Pose unexpectedly locked while writing!") = new_pose;
        // pose uses CCW+, IMU heading() is CW+, and localization reads -imu.heading().
        self.imu.try_lock().expect("IMU locked while resetting heading").set_heading(-new_pose.heading).ok();
        self.localization.try_lock().expect("Localization locked while resetting heading").cancel_odometry_tick.set(true);
    }

    /// Drives each side at a fixed voltage for `time`, then stops. Can be ended early with `end_motion`.
    pub async fn drive_raw_v(&self, left_v: f64, right_v: f64, time: Duration) {
        let start = Instant::now();
        self.end_requested.set(false);
        self.driving.set(true);
        
        {
            let mut dt = self.drivetrain.try_lock().expect("Dt locked while driving raw v!");
            dt.left_side.spin_v(left_v);
            dt.right_side.spin_v(right_v);
        }

        while start.elapsed() < time && !self.end_requested.get() {
            sleep(SLEEP_INTERVAL).await;
        }

        self.driving.set(false);
        self.drivetrain.try_lock().expect("Dt locked while stopping raw v!").stop();
    }

    pub fn drive_indefinite(&self, left_v: f64, right_v: f64) {
        let mut dt = self.drivetrain.try_lock().expect("Dt locked while driving raw v!");
        dt.left_side.spin_v(left_v);
        dt.right_side.spin_v(right_v);
    }

    /// Snaps the pose axis facing `wall` to `dsr`'s reading, retrying briefly on bad readings.
    /// Returns whether the reset was applied.
    #[allow(unused)]
    pub async fn distance_reset(&self, dsr: &Dsr, wall: Wall) -> bool {
        for _ in 0..10 {
            if let Some(reset) = dsr.get_pose(self.get_pose(), wall) {
                *self.pose.try_lock().expect("Pose locked during distance reset") = reset;
                return true;
            }
            sleep(SLEEP_INTERVAL).await;
        }
        false
    }

    pub fn goal_reset(&self, goal_position: (f64, f64)) {
        let new_pose = self.goal_reset.reset(self.get_pose(), Pose::new(goal_position.0, goal_position.1, Angle::ZERO));
        *self.pose.try_lock().expect("bruh what") = new_pose;
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
