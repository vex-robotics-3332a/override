// Includes cascade (winch), arm, carriage, and claw.
use crate::motion_control::pid::PIDController;
use crate::SLEEP_INTERVAL;
use alloc::rc::Rc;
use core::cell::Cell;
use core::fmt;
use vexide::{math::Angle, prelude::*, smart::motor::BrakeMode, sync::Mutex};

pub struct Cascade {
    pub motors: Vec<Motor>,
    pub pid: PIDController,
    pub stopping_mode: BrakeMode,
    pub exit_range: f64
}

impl Cascade {
    pub fn new(motors: Vec<Motor>, pid: PIDController, stopping_mode: BrakeMode) -> Self {
        Self { motors, pid, stopping_mode, exit_range: 1.0 }
    }

    //i hope these worklol

    pub fn extend(&mut self) {
        self.motors.iter_mut().for_each(|motor| motor.set_voltage(motor.max_voltage()).unwrap_or_default());
    }

    pub fn retract(&mut self) {
        self.motors.iter_mut().for_each(|motor| motor.set_voltage(-motor.max_voltage()).unwrap_or_default());
    }

    pub fn stop(&mut self) {
        self.motors.iter_mut().for_each(|motor| motor.brake(self.stopping_mode).unwrap_or_default());
    }

    pub async fn move_to_height(&mut self, height: Angle) {
        self.pid.reset();

        let dt = SLEEP_INTERVAL.as_secs_f64(); 
        loop {
            let error = (height - self.get_height()).as_degrees();
            let power = self.pid.get_power(error, dt);
            self.motors.iter_mut().for_each(|motor| motor.set_voltage(power).unwrap_or_default());
            if error.abs() < self.exit_range {
                self.stop();
                return;
            }

            sleep(SLEEP_INTERVAL).await;
        }
}

pub struct EndAffector {
    pub motor: Motor,
    pub stopping_mode: BrakeMode,
}

impl EndAffector {
    pub fn new(motor: Motor, stopping_mode: BrakeMode) -> Self {
        Self { motor, stopping_mode }
    }

    pub fn intake(&mut self) {
        self.motor.set_voltage(self.motor.max_voltage()).ok();
    }

    pub fn outtake(&mut self) {
        self.motor.set_voltage(-self.motor.max_voltage()).ok();
    }

    pub fn stop(&mut self) {
        self.motor.brake(self.stopping_mode).ok();
    }

}

pub struct Wrist {
    pub motor: Motor,
    pub ratio: f64,
    pub pid: PIDController,
    pub stopping_mode: BrakeMode,
    pub min_bound: f64,
}

impl Wrist {
    pub fn new(motor: Motor, ratio: f64, pid: PIDController, stopping_mode: BrakeMode) -> Self {
        Self {
            motor,
            ratio,
            pid,
            stopping_mode,
            min_bound: 1.0
        }
    }
    //temporary control functions
    pub fn up(&mut self) {
        self.motor.set_voltage(-self.motor.max_voltage() / 2.0).ok();
    }

    pub fn down(&mut self) {
        self.motor.set_voltage(self.motor.max_voltage() / 2.0).ok();
    }

    pub fn get_wrist_angle(&self) -> Angle {
        let motor_angle = self.motor.position().ok().unwrap_or_default();
        Angle::from_degrees(motor_angle.as_degrees() * self.ratio)
    }

    pub fn stop(&mut self) {
        self.motor.brake(self.stopping_mode).ok();
    }

    pub async fn move_to_angle(&mut self, angle: Angle) {
        self.pid.reset();

        let dt = SLEEP_INTERVAL.as_secs_f64();
        loop {
            let error = (angle - self.get_wrist_angle()).as_degrees();
            let power = self.pid.get_power(error, dt);
            self.motor.set_voltage(power).ok();

            if error.abs() < self.min_bound {
                self.stop();
                return;
            }

            sleep(SLEEP_INTERVAL).await;
        }
    }
}