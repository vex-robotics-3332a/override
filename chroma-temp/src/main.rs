mod subsystems;
mod robot;
mod localization;
mod helpers;
mod motion_control;
mod chassis;

extern crate alloc;

use vexide::{prelude::*, time};
use vexide::sync::Mutex;
use alloc::rc::Rc;
use subsystems::*;
use robot::Robot;
use localization::odometry::*;
use motion_control::ramsete::*;
use helpers::*;
use std::fs::*;
use std::io::{Result, Write};
use std::time::*;

use crate::chassis::Chassis;
use crate::localization::localization::Localization;
use crate::subsystems::drivetrain::Drivetrain;

const SLEEP_INTERVAL: Duration = Duration::new(0.0 as u64, 10000000);

// Dear future self...
// I'm sorry.

#[vexide::main]
async fn main(peripherals: Peripherals) {
    /* DRIVETRAIN */
    let left_motor_a = Motor::new(peripherals.port_12, Gearset::Blue, Direction::Forward);
    let left_motor_b = Motor::new(peripherals.port_13, Gearset::Blue, Direction::Reverse);
    let left_motor_c = Motor::new(peripherals.port_14, Gearset::Blue, Direction::Reverse);

    let right_motor_a = Motor::new(peripherals.port_17, Gearset::Blue, Direction::Forward);
    let right_motor_b = Motor::new(peripherals.port_18, Gearset::Blue, Direction::Forward);
    let right_motor_c = Motor::new(peripherals.port_19, Gearset::Blue, Direction::Reverse);

    // Velo controller values
    let velo_kp = 1.0;
    let velo_ff = 0.9;

    let left_side = drivetrain::DriveSide::new(
        vec![left_motor_a, left_motor_b, left_motor_c],
        3.25,
        36.0 / 48.0,
        2,
        velo_kp,
        velo_ff,
    );

    let right_side = drivetrain::DriveSide::new(
        vec![right_motor_a, right_motor_b, right_motor_c],
        3.25,
        36.0 / 48.0,
        2,
        velo_kp,
        velo_ff,
    );

    let drivetrain = Rc::new(Mutex::new(drivetrain::Drivetrain::new(
        left_side,
        right_side,
        11.5,
    )));

    /* LOCALIZATION */
    let odometry: Odometry = Odometry::new(
        Box::new(Rc::clone(&drivetrain)),
        Box::new(NullPod::new()));
    let imu = InertialSensor::new(peripherals.port_20);
    let localization = Localization::new(odometry, imu);

    /* misc */
    let controller = peripherals.primary_controller;
    let robot = Robot::new(Rc::clone(&drivetrain), controller);

    /* Task spawning */
    let mut chassis = Chassis::new(
        Rc::clone(&drivetrain),
        localization
    );

    chassis.init();
    


    // robot.compete().await
}

#[cfg(test)]
mod tests {
    #[test]
    fn it_adds_two() {
        assert_eq!(2 + 2, 4);
    }
}