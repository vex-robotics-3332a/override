mod subsystems;
mod robot;
mod localization;
mod helpers;
mod motion_control;
mod chassis;

extern crate alloc;

use vexide::{prelude::*, time};
use vexide::smart::{*, motor::*};
use vexide::sync::Mutex;
use alloc::rc::Rc;
use subsystems::*;
use robot::Robot;
use localization::odometry::*;
use motion_control::ramsete::*;
use helpers::*;
use std::fs::*;
use std::io::{Result, Write};
use std::os::raw;
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
    let velo_kp = 0.8;
    let velo_ka = 0.025;
    let velo_ff = 0.95;
    let velo_tau = 0.015;

    let left_side = drivetrain::DriveSide::new(
        vec![left_motor_a, left_motor_b, left_motor_c],
        3.25,
        36.0 / 48.0,
        2,
        velo_kp,
        velo_ka,
        velo_ff,
        velo_tau
    );

    let right_side = drivetrain::DriveSide::new(
        vec![right_motor_a, right_motor_b, right_motor_c],
        3.25,
        36.0 / 48.0,
        2,
        velo_kp,
        velo_ka,
        velo_ff,
        velo_tau
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

    chassis.init().await;
    chassis.set_brake_mode(BrakeMode::Brake);

    let mut test_profile = MotionProfile::new("profile1.json");
    chassis.drive_along(&mut test_profile, 0.7, 0.05).await;
    // let mut ema_file = File::create("ema_vel.txt").unwrap();
    // let mut raw_file = File::create("raw_vel.txt").unwrap();
    // writeln!(ema_file, "filtered vel").ok();
    // writeln!(raw_file, "raw vel").ok();
    // let clock  = Instant::now();
    // loop {
    //     let mut vdt = chassis.drivetrain.try_lock().expect("locked!!");
    //     vdt.left_side.spin_v(12.0);
    //     vdt.right_side.spin_v(12.0);
    //     writeln!(ema_file, "{}", (vdt.left_side.velocity + vdt.right_side.velocity) / 2.0).ok();
    //     writeln!(raw_file, "{}", (vdt.left_side.raw_vel + vdt.right_side.raw_vel) / 2.0).ok();
    //     raw_file.flush().ok();
    //     ema_file.flush().ok();
    //     drop(vdt);
    //     if clock.elapsed().as_secs_f64() > 3.0 {
    //         break;
    //     }
    //     sleep(SLEEP_INTERVAL).await;
    // }

    robot.compete().await
}

#[cfg(test)]
mod tests {
    #[test]
    fn it_adds_two() {
        assert_eq!(2 + 2, 4);
    }
}