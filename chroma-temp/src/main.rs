/*
Todos or something:
- Telemetry system
- subsys integration
- Auto routines selector/system
 */

mod subsystems;
mod autos;
mod auto_selector;
mod robot;
mod localization;
mod helpers;
mod motion_control;
mod chassis;
mod telemetry;
mod configs;

extern crate alloc;

use vexide::{peripherals, prelude::*, time, math::*};
use vexide::smart::{*, motor::*};
use vexide::sync::Mutex;
use alloc::rc::Rc;
use subsystems::{lift::*, drivetrain::{Drivetrain, DriveSide}, intake::Intake};
use robot::Robot;
use localization::odometry::*;
use localization::dsr::*;
use motion_control::ramsete::*;
use helpers::*;
use std::fs::*;
use std::io::{Result, Write};
use std::time::*;
use motion_control::pid::PIDController;
use subsystems::toggles::Toggle;
use crate::auto_selector::{AutoSelector, AUTOS};
use crate::chassis::Chassis;
use crate::localization::localization::Localization;
use crate::configs::*;
use crate::telemetry::TelemetryFile;

const SLEEP_INTERVAL: Duration = Duration::new(0.0 as u64, 10000000);
const test: Duration = Duration::from_millis(10);
const MM_PER_INCH: f64 = 25.4;
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
        2.75,
        36.0 / 48.0,
        1,
        velo_kp,
        velo_ka,
        velo_ff
    );

    let right_side = DriveSide::new(
        vec![right_motor_a, right_motor_b],
        2.75,
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

    let vertical_encoder = TrackingPod::new(
        RotationSensor::new(peripherals.port_20, Direction::Forward),
        2.0,
        -0.5,
        false,
        1.0
    );
    let odometry: Odometry = Odometry::new(
        Box::new(vertical_encoder),
        Box::new(NullPod::new()));

    let imu = InertialSensor::new(peripherals.port_14);

    let left_dsr = Dsr::new(
        DistanceSensor::new(peripherals.port_3),
        Pose::new(-0.25, 5.567, Angle::from_degrees(90.0)),
        0.5
    );
    let front_dsr = Dsr::new(
        DistanceSensor::new(peripherals.port_4),
        Pose::new(4.5555, 1.630, Angle::from_degrees(0.0)),
        -0.25
    );
    let right_dsr = Dsr::new(
        DistanceSensor::new(peripherals.port_7),
        Pose::new(-0.25, -5.567, Angle::from_degrees(270.0)),
        0.4
    );
    let back_dsr = Dsr::new(    
        DistanceSensor::new(peripherals.port_8),
        Pose::new(-3.436, -4.263, Angle::from_degrees(180.0)),
        -0.25
    );
    

    let localization = Localization::new(odometry, imu);
    let goal_reset = GoalReset::new(3.225, 5.054);
    let chassis = Chassis::new(
        Rc::clone(&drivetrain),
        localization,
        ChassisConfig {
            ramsete_config: RamseteConfig {
                beta: 0.019,
                zeta: 0.7,
            },
            exit_range: 0.5,
        },
        goal_reset,
        PIDController::new(
            2.0,
            0.0,
            0.16,
            0.0,
            f64::INFINITY,
            12.0,
            -12.0,
            f64::INFINITY
        )
    );


    /* misc */
    let controller = peripherals.primary_controller;

    let lift_motor_a = Motor::new(peripherals.port_1, Gearset::Green, Direction::Forward);
    let lift_motor_b = Motor::new(peripherals.port_10, Gearset::Green, Direction::Reverse);
    let lift_motor_c = Motor::new(peripherals.port_9, Gearset::Green, Direction::Forward);

    /* lift */
    let lift_encoder = RotationSensor::new(peripherals.port_2, Direction::Reverse);
    let cascade = Cascade::new(
        vec![lift_motor_a, lift_motor_b, lift_motor_c], 
        PIDController::new(
            0.1, 
            0.0, 
            0.001, 
            0.0,
            f64::INFINITY, 
            12.0, 
            1.0, 
            f64::INFINITY), 
        lift_encoder,
        BrakeMode::Hold,
        0.0);
    let claw = Claw::new(AdiDigitalOut::new(peripherals.adi_a));
    let back_sensor = BackSensor::new(DistanceSensor::new(peripherals.port_17));
    let lift = Lift::new(cascade, claw, back_sensor);
    let dsrs = Dsrs { left: left_dsr, front: front_dsr, right: right_dsr, back: back_dsr};
    let toggle = Toggle::new(Motor::new(peripherals.port_5, Gearset::Green, Direction::Forward), OpticalSensor::new(peripherals.port_6), "Red");

    let mut auto_selector = AutoSelector::new(AUTOS);
    auto_selector.select("Skills");

    let mut robot = Robot::new(chassis, controller, lift, dsrs, auto_selector, toggle);
    
    /* Task Spawning */
    println!("hiii!");
    robot.lift.reset_encoder();
    robot.chassis.init().await;
    robot.chassis.start_localization();
    robot.chassis.set_brake_mode(BrakeMode::Hold);
    // robot.chassis.set_pose(Pose::new(0.0, 0.0, Angle::from_degrees(0.0)));

    // robot.autonomous().await;

    // println!("Starting pose logging");
    // let pose = Rc::clone(&robot.chassis.pose);
    // spawn(async move {
    //     loop {
    //         let pose = *pose.try_lock().expect("pose locked while logging");
    //         println!("x: {:.2}, y: {:.2}, h: {:.2}", pose.x, pose.y, pose.heading.as_degrees());
    //         sleep(Duration::from_millis(100)).await;
    //     }
    // }).detach();

    // let lift = robot.lift.clone();
    // spawn(async move {
    //     loop {
    //         println!("casc h: {:.2}", lift.cascade_height().as_degrees());
    //         sleep(Duration::from_millis(300)).await;
    //     }
    // }).detach();
    // robot.chassis.turn(100.0, 270.0, 1000.0).await;
    

    // let start = Instant::now();
    // autos::driver_2::run(&mut robot).await;
    // println!("Total time: {:.2}", start.elapsed().as_secs_f64());
    // sleep(Duration::from_millis(1000)).await;

    robot.compete().await;
}

#[cfg(test)]
mod tests {
    #[test]
    fn it_adds_two() {
        assert_eq!(2 + 2, 4);
    }
}
