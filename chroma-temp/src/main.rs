mod subsystems;
mod robot;
mod localization;
mod helpers;

extern crate alloc;

use vexide::{prelude::*, time};
use vexide::sync::Mutex;
use alloc::rc::Rc;
use subsystems::*;
use robot::Robot;
use localization::odometry::*;
use helpers::*;
use std::fs::*;
use std::io::{Result, Write};
use std::time::Duration;

use crate::localization::localization::Localization;

const SLEEP_INTERVAL: Duration = Duration::new(0.0 as u64, 10000000);

// Dear future self...
// I'm sorry.

#[vexide::main]
async fn main(peripherals: Peripherals) {

    let mut velocity_file = File::options()
        .write(true)
        .create(true)
        .append(true)
        .open("velocity_file.txt")
        .expect("failed to create test_file!");

    // for _ in 0..2048 {
    //     write!(test_file, "i").expect("can't write?");
    // }
    

    /* DRIVETRAIN */
    let left_motor_a = Motor::new(peripherals.port_12, Gearset::Blue, Direction::Forward);
    let left_motor_b = Motor::new(peripherals.port_13, Gearset::Blue, Direction::Reverse);
    let left_motor_c = Motor::new(peripherals.port_14, Gearset::Blue, Direction::Reverse);

    let right_motor_a = Motor::new(peripherals.port_17, Gearset::Blue, Direction::Forward);
    let right_motor_b = Motor::new(peripherals.port_18, Gearset::Blue, Direction::Forward);
    let right_motor_c = Motor::new(peripherals.port_19, Gearset::Blue, Direction::Reverse);

    // placeholder velocity controller gains — tune these
    let velo_kp = 0.5;
    let velo_ff = 1.0;

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

    let odometry: Odometry<Rc<Mutex<drivetrain::Drivetrain>>, NullPod> = Odometry::new(Rc::clone(&drivetrain), NullPod::new());
    let imu = InertialSensor::new(peripherals.port_20);
    let localization = Localization::new(odometry, imu);

    let controller = peripherals.primary_controller;

    let robot = Robot::new(Rc::clone(&drivetrain), controller);

    // Continuously re-estimate each drive side's velocity from position deltas
    // (motor.velocity() is too noisy). Locks the drivetrain only briefly each
    // tick and releases before awaiting, so it never blocks driving/odometry.
    let velocity_dt = Rc::clone(&drivetrain);
    let _velocity_task = spawn(async move {
        let mut tick = 0;
        loop {
            {
                let mut dt = velocity_dt.try_lock().expect("dt locked in velocity task");
                dt.tick_vel();
                let avg = (dt.left_side.get_velocity() + dt.right_side.get_velocity()) / 2.0;
                writeln!(velocity_file, "{}", avg).expect("can't write to vel file!");

                if tick % 10 == 0 {
                    velocity_file.flush().ok();
                    tick = 0
                }

                tick += 1;
            }
            sleep(SLEEP_INTERVAL).await;
        }
    });

    let _localization_task = spawn(localization.run());
    robot.compete().await
}

#[cfg(test)]
mod tests {
    #[test]
    fn it_adds_two() {
        assert_eq!(2 + 2, 4);
    }
}