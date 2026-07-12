use std::f64::consts::PI;
use alloc::rc::Rc;
use vexide::math::Angle;
use vexide::prelude::*;
use vexide::smart::motor::BrakeMode::{self, Brake};
use vexide::sync::Mutex;
use vexide::time::LowResolutionTime;
use std::fs::write;
use crate::SLEEP_INTERVAL;
use crate::localization::odometry::Encoder;

//imperial units pls

pub struct DriveSide {
    pub motors: Vec<Motor>,
    pub wheel_diameter: f64,
    pub ratio: f64,
    
    // Which motor is used for velo control & tracking

    pub priority: usize,
    pub velocity_controller: VelocityController,

    pub velocity: f64,
    pub raw_vel: f64,
    pub brake_mode: BrakeMode,
    pub voltage_percentage: f64,
    pub ema_tau: f64,

    prev_position: Angle,
    prev_stamp: LowResolutionTime,
}

impl DriveSide {
    pub fn new(
        motors: Vec<Motor>,
        wheel_diameter: f64, 
        ratio: f64, 
        priority: usize,
        velo_controller_kp: f64,
        velo_controller_ka: f64,
        velo_controller_ff: f64,
        ema_tau: f64
    ) -> Self {
        let gearset = motors[priority].gearset().unwrap_or(Gearset::Blue);
        Self {
            motors,
            wheel_diameter,
            ratio,
            priority,
            velocity_controller: VelocityController::new(
                gearset,
                velo_controller_kp,
                velo_controller_ka,
                velo_controller_ff
            ),
            velocity: 0.0,
            raw_vel: 0.0,
            brake_mode: BrakeMode::Coast,
            prev_position: Angle::ZERO,
            prev_stamp: LowResolutionTime::EPOCH,
            voltage_percentage: 0.0,
            ema_tau
        }
    }

    // pub fn tick_vel(&mut self) {
    //     let mut velofile = File::options()
    //         .create(true) // Create file if it doesn't exist
    //         .append(true) // Enable append mode
    //         .open("velocity.txt").unwrap();

            
    //     let max_rpm = self.velocity_controller.gearset.max_rpm();
    //     let motor = &self.motors[self.priority];
    //     let now = motor.timestamp().unwrap_or(LowResolutionTime::now()).elapsed().as_secs_f64() / 60.0; 
    //     let dt_min = now - self.last_update;
    //     let current = self.motors[self.priority].position().unwrap_or(Angle::ZERO);
    //     if dt_min > 0.0 {
    //         let rpm_velocity = (current - self.prev_position).as_turns() / 0.000166667;
    //         self.velocity = rpm_velocity / max_rpm * 100.0
    //         //0.000166667 mins -> 10ms
    //     }
    //     writeln!(velofile, "{}", self.velocity).ok();
    //     self.prev_position = current;
    //     self.last_update = now;
    // }
    pub fn tick_vel(&mut self) {
        let max_rpm = self.velocity_controller.gearset.max_rpm();
        let motor = &self.motors[self.priority];

        // The motor only refreshes position on its own ~10ms packet cadence, so
        // differentiate against the motor's own timestamp. This keeps Δposition
        // and Δt on the same clock and avoids aliasing against our sample loop.
        let Ok(stamp) = motor.timestamp() else { return };
        let current_position = motor.position().unwrap_or(Angle::ZERO);

        // Seed on the first packet so we don't divide a full accumulated
        // position by a huge Δt.
        if self.prev_stamp == LowResolutionTime::EPOCH {
            self.prev_stamp = stamp;
            self.prev_position = current_position;
            return;
        }

        let dt = stamp - self.prev_stamp;
        if dt.is_zero() {
            return; // no new packet since last tick, keep previous velocity
        }
        let rpm_velocity = (current_position - self.prev_position).as_turns() / (dt.as_secs_f64() / 60.0);
        let raw = rpm_velocity / max_rpm * 100.0;

        let alpha = 1.0 - (-dt.as_secs_f64() / self.ema_tau).exp();
        self.velocity += alpha * (raw - self.velocity);

        //TEMPORARY, FOR TUNING EMA
        self.raw_vel = raw;

        self.prev_position = current_position;
        self.prev_stamp = stamp;
    }

    pub fn spin(&mut self, pct: f64, accel: f64) {
        let power = self.velocity_controller.get_power(pct, self.velocity, accel);
        self.spin_v(power);
    }

    pub fn spin_v(&mut self, v: f64) {
        for motor in &mut self.motors {
            motor.set_voltage(v).ok();
        }
        self.voltage_percentage = v / 12.0 * 100.0;
    }

    pub fn stop(&mut self) {
        for motor in &mut self.motors {
            motor.brake(self.brake_mode).ok();
        }
    }

    fn get_travel(&self) -> f64 {
        let pos = self.motors[self.priority].position().unwrap_or_default().as_turns();
        pos * 2.0 * PI * self.wheel_diameter * 0.5 * self.ratio
    }

    fn reset_travel(&mut self) {
        self.motors.iter_mut().for_each(|motor| {motor.reset_position().ok(); });
    }
}

pub struct VelocityController {
    pub gearset: Gearset,
    
    pub kp: f64,
    pub ka: f64,
    pub ff: f64,
}

impl VelocityController {
    pub fn new(gearset: Gearset, kp: f64, ka: f64, ff: f64) -> Self {
        Self { gearset, kp, ka, ff }
    }

    /// `pct`   desired velocity, percent of max RPM
    /// `vel`   measured velocity, percent of max RPM
    /// `accel` desired acceleration, percent of max RPM per second
    pub fn get_power(&self, pct: f64, vel: f64, accel: f64) -> f64 {
        let error_v = (pct - vel) / 100.0 * 12.0;

        // velocity feedforward + acceleration feedforward + proportional trim
        let power = pct / 100.0 * 12.0 * self.ff
            + accel * self.ka
            + error_v * self.kp;

        power.clamp(-12.0, 12.0)
    }
}

pub struct Drivetrain {
    pub left_side: DriveSide,
    pub right_side: DriveSide,

    pub track_width: f64,
    pub brake_mode: BrakeMode
}

impl Drivetrain {
    pub fn new(
        left_side: DriveSide,
        right_side: DriveSide,
        track_width: f64
    ) -> Self {
        Self {
            left_side,
            right_side,
            track_width,
            brake_mode: BrakeMode::Coast
        }
    }

    pub fn to_pct_speed(&self, ins: f64) -> f64 {
        // in/s speed
        let max = self.left_side.wheel_diameter * PI * self.left_side.velocity_controller.gearset.max_rpm() * self.left_side.ratio / 60.0;
        ins / max * 100.0
    }

    pub fn set_brake_mode(&mut self, brake_mode: BrakeMode) {
        self.left_side.brake_mode = brake_mode;
        self.right_side.brake_mode = brake_mode;
    }

    pub fn stop(&mut self) {
        self.left_side.stop();
        self.right_side.stop();
    }

    pub fn tick_vel(&mut self) {
        self.left_side.tick_vel();
        self.right_side.tick_vel();
    }

    pub fn desaturate(&self, left_pct: f64, right_pct: f64) -> (f64, f64) {
        let scale = left_pct.abs().max(right_pct.abs()).max(100.0) / 100.0;
        (left_pct / scale, right_pct / scale)
    }

    pub async fn run_vel_ticking(dt: Rc<Mutex<Drivetrain>>) {
        loop {
            let mut vel_dt = dt.try_lock().expect("Dt locked while ticking vel!");
            vel_dt.tick_vel();
            drop(vel_dt);
            sleep(SLEEP_INTERVAL).await;
        }
    }

    
}

impl Encoder for Rc<Mutex<Drivetrain>> {
    fn get_travel(&self) -> f64 {
        let dt = self.try_lock().expect("Dt locked while getting travel");
        (dt.left_side.get_travel() + dt.right_side.get_travel()) / 2.0 
    }

    fn reset_travel(&mut self) {
        let mut dt = self.try_lock().expect("Dt locked while resetting travel");
        dt.left_side.reset_travel();
        dt.right_side.reset_travel();
    }

    fn get_offset(&self) -> f64 {
        0.0
    }
}
