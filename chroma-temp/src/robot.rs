use std::time::Duration;

use crate::SLEEP_INTERVAL;
use vexide::prelude::*;
use crate::chassis::Chassis;

pub struct Robot {
    pub chassis: Chassis,
    pub controller: Controller,
}

impl Robot {
    pub fn new(chassis: Chassis, controller: Controller) -> Self {
        Self { chassis, controller }
    }

    pub async fn op_control(&mut self) {
        loop {
            self.chassis.op_control_tick(&self.controller);
            sleep(SLEEP_INTERVAL).await;
        }
    }
}

impl Compete for Robot {
    async fn autonomous(&mut self) {
        println!("Autonomous!");
    }
    async fn driver(&mut self) {
        self.op_control().await;
    }
}