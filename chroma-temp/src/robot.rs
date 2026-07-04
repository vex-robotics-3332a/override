use std::time::Duration;

use vexide::prelude::*;
use vexide::sync::Mutex;
use alloc::rc::Rc;
use crate::subsystems::drivetrain::Drivetrain;

pub struct Robot {
    pub drivetrain: Rc<Mutex<Drivetrain>>,
    pub controller: Controller,
}

impl Robot {
    pub fn new(drivetrain: Rc<Mutex<Drivetrain>>, controller: Controller) -> Self {
        Self { drivetrain: Rc::clone(&drivetrain), controller }
    }

    pub fn op_control(&mut self) {
        if let Ok(state) = self.controller.state() {
            let left_stick = state.left_stick;
            let right_stick = state.right_stick;

            //hardcoded for now will make a config file later 6/10/2026

            let linear_power = left_stick.y() * 12.0;
            let angular_power = right_stick.x() * 12.0;

            let mut dt = self.drivetrain.try_lock().expect("Dt locked while op_control");
            dt.spin_left_v(linear_power + angular_power);
            dt.spin_right_v(linear_power - angular_power);
        }
    }
}

impl Compete for Robot {
    async fn autonomous(&mut self) {
        println!("Autonomous!");
    }
    async fn driver(&mut self) {
        loop {
            self.op_control();
            sleep(Duration::new(0.0 as u64,10000000.0 as u32)).await;
        }
    }
}