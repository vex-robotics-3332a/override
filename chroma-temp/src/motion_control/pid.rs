//basic PID controller
pub struct PIDController {
    pub kp: f64,
    pub ki: f64,
    pub kd: f64,
    pub feedforward: f64,
    pub slew: f64,
    pub max_output: f64,
    pub min_output: f64,
    pub max_integral: f64,

    integral: f64,
    previous_error: f64,
    previous_output: f64,
    /// False right after `reset`. The first sample has no real previous error, so D is skipped.
    derivative_ready: bool,
}

impl PIDController {
    pub fn new(
        kp: f64,
        ki: f64,
        kd: f64,
        feedforward: f64,
        slew: f64,

        max_output: f64,
        min_output: f64,
        max_integral: f64,
    ) -> Self {
        Self {
            kp,
            ki,
            kd,
            feedforward,
            slew,
            max_output,
            min_output,
            max_integral,
            integral: 0.0,
            previous_error: 0.0,
            previous_output: 0.0,
            derivative_ready: false,
        }
    }

    pub fn reset(&mut self) {
        self.integral = 0.0;
        self.previous_error = 0.0;
        self.previous_output = 0.0;
        self.derivative_ready = false;
    }

    pub fn get_power(&mut self, error: f64, dt: f64) -> f64 {
        let proportional = self.kp * error;
        self.integral += error * dt;
        self.integral = self.integral.clamp(-self.max_integral, self.max_integral);

        // previous_error is 0 after reset, so the first sample would treat the whole error as the change.
        let derivative = if self.derivative_ready && dt > 0.0 {
            self.kd * (error - self.previous_error) / dt
        } else {
            0.0
        };
        self.previous_error = error;
        self.derivative_ready = true;

        let mut output = proportional + self.ki * self.integral + derivative;
        if output > 0.0 {
            output = output.clamp(self.min_output, self.max_output);
        } else {
            output = output.clamp(-self.max_output, -self.min_output);
        }

        let delta = (output - self.previous_output).clamp(-self.slew, self.slew);
        output = self.previous_output + delta;

        self.previous_output = output;
        output + self.feedforward * error
    }

    pub fn set_inital_power(&mut self, power: f64) {
        self.previous_output = power;
    }
}
