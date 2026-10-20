use vexide::prelude::*;
pub struct Intake {
    pub motor: Motor,

    pub is_on: bool,
}

impl Intake {
    pub fn new(motor: Motor) -> Self {
        Self { motor, is_on: false }
    }

    pub fn spin(&mut self, pct: f64) {
        self.motor.set_voltage(pct / 100.0 * 12.0).ok();
    }

    pub fn stop(&mut self) {
        self.motor.set_voltage(0.0).ok();
    }

    pub fn toggle(&mut self) {
        self.is_on = !self.is_on;
        if self.is_on {
            self.spin(100.0);
        } else {
            self.stop();
        }
    }
}