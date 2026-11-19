use crate::motion_control::pid::PIDController;
use crate::telemetry::TelemetryFile;
use crate::{telemetry, SLEEP_INTERVAL};
use alloc::rc::Rc;
use core::cell::RefCell;
use core::pin::pin;
use futures_util::future::{select, Either};
use std::time::{Duration, Instant};
use vexide::task::Task;
use vexide::{math::Angle, prelude::*, smart::motor::BrakeMode, sync::Mutex};

pub struct Toggle {
    pub motor: Motor,
    pub sensor: OpticalSensor,
    //is this scuffed? yes
    //can I be bothered to make an enum instead? no
    pub alliance: &'static str,
    pub on: bool,
}

impl Toggle {
    pub fn new(motor: Motor, mut sensor: OpticalSensor, alliance: &'static str) -> Self {
        sensor.set_led_brightness(1.0).ok();
        sensor.set_integration_time(Duration::from_millis(5)).ok();
        Self { motor, sensor, alliance, on: false }
    }

    pub fn get_color(&self) -> &str {
        let hue = self.sensor.hue().ok().unwrap_or_default();

        let color = match hue as u32 {
            0..=30 => "Red",
            31..=90 => "Yellow",
            91..=150 => "Green",
            151..=210 => "Blue",
            211..=270 => "Blue",
            271..=330 => "Magenta",
            _ => "Red", // 331-359 wraps back to red
        };
        color
    }

    pub fn on(&mut self) {
        self.motor.set_voltage(-12.0).ok();
        self.on = true;
    }

    pub fn off(&mut self) {
        self.motor.set_voltage(0.0).ok();
        self.on = false;
    }

    pub fn toggle(&mut self) {
        if self.on {
            self.off();
        } else {
            self.on();
        }
    }

    pub fn auto_toggle(&mut self) {
        if self.get_color() != self.alliance {
            self.on();
        } else {
            self.off();
        }
    }

    pub async fn auto_toggle_loop(&mut self, timeout: Duration) {
        let start = Instant::now();
        loop {

            if self.get_color() != self.alliance && start.elapsed() < timeout {
                self.on();
            } else {
                break;
            }
            sleep(SLEEP_INTERVAL).await;
        }
        self.off();
    }
}