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
    let _localization_task = spawn(localization.run());
    let _velocity_task = spawn(Drivetrain::run_vel_ticking(Rc::clone(&drivetrain)));

    // let mut velo_tuning_profile = MotionProfile::new("vcTuning1.json");
    
    // velo_tuning_profile.start();

    // let mut desired_velocity_file = File::create("desired_velocity.txt").unwrap();
    // let mut actual_velocity_file = File::create("actual_velocity.txt").unwrap();
    // let mut voltage_file = File::create("voltage.txt").unwrap();

    // writeln!(desired_velocity_file, "timestamp,desired").ok();
    // writeln!(actual_velocity_file, "timestamp,actual").ok();
    // writeln!(voltage_file, "timestamp,voltage").ok();

    // let timer = Instant::now();
    // loop {
    //     let time = timer.elapsed().as_millis() as f64;
    //     let mut dt = velocity_dt.try_lock().expect("how");
    //     dt.tick_vel();
    //     let point = velo_tuning_profile.get_point();
    //     dt.spin_left(point.lv as f64);
    //     dt.spin_right(point.lv as f64);

    //     writeln!(desired_velocity_file, "{}, {}", time, point.lv).ok();
    //     writeln!(actual_velocity_file, "{}, {}", time, (dt.left_side.velocity + dt.right_side.velocity) / 2.0).ok();
    //     writeln!(voltage_file, "{}, {}", time, (dt.left_side.voltage_percentage + dt.right_side.voltage_percentage) / 2.0).ok();
    //     drop(dt);
    //     if velo_tuning_profile.has_ended() { return ; }

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