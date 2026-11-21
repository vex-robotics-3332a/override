use vexide::math::Angle;
use vexide::prelude::*;
use std::process::exit;
use std::unreachable;
use std::{time::Instant, fs::read_to_string};
use std::io::Write;
use serde::Deserialize;
use crate::helpers::*;
use crate::chassis::Chassis;
use crate::telemetry::TelemetryFile;
use crate::{telemetry, SLEEP_INTERVAL};

impl Chassis {
    pub async fn turn_with_conditions(&mut self, speed_pct: f64, angle: f64, exit_speed_pct: f64, stop: bool, timeout: f64, locked_side: &str) {
        // println!("turning to {:.2}", angle);
        self.turn_pid.reset();
        let start = Instant::now();
        loop {
            let speed = 12.0 * speed_pct/100.0;
            let exit_speed = 12.0 * exit_speed_pct/100.0;

            {
                let pose = *self.pose.try_lock().expect("who locked ts during turn");
                let mut dt = self.drivetrain.try_lock().expect("Dt lock failed while driving");
                let error = angle_difference(pose.heading, Angle::from_degrees(angle));
                let mut output_power = self.turn_pid.get_power(error.as_degrees(), SLEEP_INTERVAL.as_secs_f64());
                output_power = output_power.abs().clamp(exit_speed, speed) * output_power.signum();
                if error.as_degrees().abs() < 3.0 {
                    // println!("exited w/ heading {:.2}", pose.heading.as_degrees());
                    break;
                }

                if start.elapsed().as_millis() as f64 > timeout {
                    // println!("timed out w/ heading {:.2}", pose.heading.as_degrees());
                    break;
                }

                if locked_side == "none" {
                    dt.left_side.spin_v(-output_power);
                    dt.right_side.spin_v(output_power);
                }
                else if locked_side == "left" {
                    dt.left_side.stop();
                    dt.right_side.spin_v(output_power * 2.0);
                }
                else if locked_side == "right" {
                    dt.left_side.spin_v(-output_power * 2.0);
                    dt.right_side.stop();
                }
                else {
                    unreachable!();
                }
            }
            sleep(SLEEP_INTERVAL).await;
        }

        if stop {
            let mut dt = self.drivetrain.try_lock().expect("Dt lock failed while driving");
            dt.stop();
        }
    }

    pub async fn turn(&mut self, speed_pct: f64, angle: f64, timeout: f64) {
        self.turn_with_conditions(speed_pct, angle, 0.0, true, timeout, "none").await;
    }

    pub async fn swing_turn(&mut self, speed_pct: f64, angle: f64, timeout: f64, locked_side: &str) {
        self.turn_with_conditions(speed_pct, angle, 0.0, true, timeout, locked_side).await;
    }
}