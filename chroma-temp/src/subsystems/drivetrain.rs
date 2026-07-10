use std::f64::consts::PI;
use std::time::Instant;
use alloc::rc::Rc;
use vexide::math::Angle;
use vexide::prelude::*;
use vexide::sync::Mutex;
use vexide::time::LowResolutionTime;
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
    prev_position: Angle,
    last_update: f64,
    clock: Instant,

    pub voltage_percentage: f64
}

impl DriveSide {
    pub fn new(
        motors: Vec<Motor>,
        wheel_diameter: f64, 
        ratio: f64, 
        priority: usize,
        velo_controller_kp: f64,
        velo_controller_ff: f64
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
                velo_controller_ff
            ),
            velocity: 0.0,
            prev_position: Angle::ZERO,
            last_update: 0.0,
            clock: Instant::now(),
            voltage_percentage: 0.0 
        }
    }

    pub fn tick_vel(&mut self) {
        let max_rpm = self.velocity_controller.gearset.max_rpm();
        let motor = &self.motors[self.priority];
        let now = motor.timestamp().unwrap_or(LowResolutionTime::now()).elapsed().as_secs_f64() / 60.0; 
        let dt_min = now - self.last_update;
        let current = self.motors[self.priority].position().unwrap_or(Angle::ZERO);
        if dt_min > 0.0 {
            let rpm_velocity = (current - self.prev_position).as_turns() / 0.000166667;
            self.velocity = rpm_velocity / max_rpm * 100.0
            //0.000166667 mins -> 10ms
        }
        self.prev_position = current;
        self.last_update = now;
    }

    pub fn spin(&mut self, pct: f64) {
        let power = self.velocity_controller.get_power(pct, self.velocity);
        self.spin_v(power);
    }

    pub fn spin_v(&mut self, v: f64) {
        for motor in &mut self.motors {
            motor.set_voltage(v).ok();
        }
        self.voltage_percentage = v / 12.0 * 100.0;
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
    pub ff: f64,
}

impl VelocityController {
    pub fn new(gearset: Gearset, kp: f64, ff: f64) -> Self {
        Self { gearset, kp, ff }
    }

    pub fn get_power(&self, pct: f64, vel: f64) -> f64 {
        let error_v = (pct - vel) / 100.0 * 12.0;

        pct / 100.0 * 12.0 * self.ff + error_v * self.kp
    }
}

pub struct Drivetrain {
    pub left_side: DriveSide,
    pub right_side: DriveSide,

    pub track_width: f64,


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
        }
    }

    pub fn to_pct_speed(&self, ins: f64) -> f64 {
        // in/s speed
        let max = self.left_side.wheel_diameter * PI * self.left_side.velocity_controller.gearset.max_rpm() / 60.0;
        ins / max * 100.0
    }

    pub fn spin_left(&mut self, pct: f64) {
        self.left_side.spin(pct);
    }

    pub fn spin_right(&mut self, pct: f64) {
        self.right_side.spin(pct);
    }

    pub fn spin_left_v(&mut self, v: f64) {
        self.left_side.spin_v(v);
    }

    pub fn spin_right_v(&mut self, v: f64) {
        self.right_side.spin_v(v);
    }

    fn get_travel(&self) -> f64 {
        (self.left_side.get_travel() + self.right_side.get_travel()) / 2.0 
    }

    fn reset_travel(&mut self) {
        self.left_side.reset_travel();
        self.right_side.reset_travel();
    }

    fn get_offset(&self) -> f64 {
        0.0
    }

    pub fn tick_vel(&mut self) {
        self.left_side.tick_vel();
        self.right_side.tick_vel();
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
        self.try_lock().expect("Dt locked while getting travel").get_travel()
    }

    fn reset_travel(&mut self) {
        self.try_lock().expect("Dt locked while resetting travel").reset_travel()
    }

    fn get_offset(&self) -> f64 {
        0.0
    }
}
