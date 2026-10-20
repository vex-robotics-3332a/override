/*
Todos or something:
- Telemetry system
- subsys integration
- Auto routines selector/system
 */

mod subsystems;
mod autos;
mod robot;
mod localization;
mod helpers;
mod motion_control;
mod chassis;
mod telemetry;
mod configs;

extern crate alloc;

use vexide::{peripherals, prelude::*, time};
use vexide::smart::{*, motor::*};
use vexide::sync::Mutex;
use alloc::rc::Rc;
use subsystems::{lift::*, drivetrain::{Drivetrain, DriveSide}, intake::Intake};
use robot::Robot;
use localization::odometry::*;
use motion_control::ramsete::*;
use helpers::*;
use std::fs::*;
use std::io::{Result, Write};
use std::time::*;
use motion_control::pid::PIDController;

use crate::chassis::Chassis;
use crate::localization::localization::Localization;
use crate::configs::*;

const SLEEP_INTERVAL: Duration = Duration::new(0.0 as u64, 10000000);

// Dear future self...
// I'm sorry.

// FUCK YOU PAST SELF

#[vexide::main]
async fn main(peripherals: Peripherals) {
    /* DRIVETRAIN */
    let left_motor_a = Motor::new(peripherals.port_12, Gearset::Blue, Direction::Reverse);
    let left_motor_b = Motor::new(peripherals.port_13, Gearset::Blue, Direction::Reverse);

    let right_motor_a = Motor::new(peripherals.port_18, Gearset::Blue, Direction::Forward);
    let right_motor_b = Motor::new(peripherals.port_19, Gearset::Blue, Direction::Forward);

    // Velo controller values
    let velo_kp = 1.0;
    let velo_ka = 0.025;
    let velo_ff = 1.0;

    let left_side = DriveSide::new(
        vec![left_motor_a, left_motor_b],
        3.25,
        36.0 / 48.0,
        1,
        velo_kp,
        velo_ka,
        velo_ff
    );

    let right_side = DriveSide::new(
        vec![right_motor_a, right_motor_b],
        3.25,
        36.0 / 48.0,
        1,
        velo_kp,
        velo_ka,
        velo_ff
    );

    let drivetrain = Rc::new(Mutex::new(Drivetrain::new(
        left_side,
        right_side,
        11.5,
    )));

    /* LOCALIZATION */
    let odometry: Odometry = Odometry::new(
        Box::new(Rc::clone(&drivetrain)),
        Box::new(NullPod::new()));
    let imu = InertialSensor::new(peripherals.port_21);
    let localization = Localization::new(odometry, imu);

    let chassis = Chassis::new(
        Rc::clone(&drivetrain),
        localization,
        ChassisConfig {
            ramsete_config: RamseteConfig {
                beta: 0.019,
                zeta: 0.7,
            },
        }
    );


    /* misc */
    let controller = peripherals.primary_controller;
    let lift_motor_a = Motor::new(peripherals.port_20, Gearset::Green, Direction::Forward);
    let lift_motor_b = Motor::new(peripherals.port_11, Gearset::Green, Direction::Reverse);

    /* lift */
    let cascade = Cascade::new(
        vec![lift_motor_a, lift_motor_b], 
        PIDController::new(
            0.8, 
            0.0, 
            0.02, 
            f64::INFINITY, 
            12.0, 
            -8.0, 
            f64::INFINITY), 
        BrakeMode::Hold);
    let end_affector = EndAffector::new(
        Motor::new_exp(peripherals.port_2, Direction::Forward), 
        BrakeMode::Hold);
    let wrist = Wrist::new(
        Motor::new_exp(peripherals.port_3, Direction::Forward), 
        12.0/48.0, 
        PIDController::new(
            0.8, 
            0.0, 
            0.02, 
            f64::INFINITY, 
            8.0, 
            -8.0, 
            f64::INFINITY), 
        BrakeMode::Hold);
    let lift = Lift::new(cascade, wrist, end_affector);
    let intake = Intake::new(Motor::new(peripherals.port_10, Gearset::Blue, Direction::Forward));
    let mut robot = Robot::new(chassis, controller, lift, intake);

    // loop {
    //     // printing this freezes the program
    //     println!("helloworldhelloworldhelloworldhelloworld");
    //     sleep(SLEEP_INTERVAL).await;
    // }
    
    /* Task Spawning */

    robot.chassis.init().await;

    /* TEST CODE BELOW! */

    robot.chassis.set_brake_mode(BrakeMode::Brake);

    // autos::simulate_auto::run(&robot.chassis).await;

    robot.compete().await;
}

#[cfg(test)]
mod tests {
    #[test]
    fn it_adds_two() {
        assert_eq!(2 + 2, 4);
    }
}
