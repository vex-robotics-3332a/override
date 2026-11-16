// Includes cascade (winch) and pneumatic claw.
use crate::motion_control::pid::PIDController;
use crate::telemetry::TelemetryFile;
use crate::{telemetry, SLEEP_INTERVAL};
use alloc::rc::Rc;
use core::cell::RefCell;
use core::pin::pin;
use futures_util::future::{select, Either};
use std::time::Duration;
use vexide::task::Task;
use vexide::{math::Angle, prelude::*, smart::motor::BrakeMode, sync::Mutex};

/// One variant per macro, plus `None` when idle.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum LiftState {
    None,
}

impl LiftState {
    pub fn repr(&self) -> &str {
        match self {
            LiftState::None => "None",
        }
    }
}

#[derive(Clone)]
pub struct Lift {
    pub cascade: Rc<Mutex<Cascade>>,
    pub claw: Rc<Mutex<Claw>>,
    pub state: Rc<Mutex<LiftState>>,
    current_macro: Rc<RefCell<Option<Task<()>>>>,
}

impl Lift {
    pub fn new(cascade: Cascade, claw: Claw) -> Self {
        Self {
            cascade: Rc::new(Mutex::new(cascade)),
            claw: Rc::new(Mutex::new(claw)),
            state: Rc::new(Mutex::new(LiftState::None)),
            current_macro: Rc::new(RefCell::new(None)),
        }
    }

    //basic manipulators
    pub fn macro_running(&self) -> bool {
        *self.state.try_lock().expect("Lift state locked while reading") != LiftState::None
    }

    pub async fn wait_for_macro(&self) {
        while self.macro_running() {
            sleep(SLEEP_INTERVAL).await;
        }
    }

    pub fn cascade_extend(&self) {
        self.cascade.try_lock().expect("Cascade locked during extend").extend();
    }

    pub fn cascade_retract(&self) {
        self.cascade.try_lock().expect("Cascade locked during retract").retract();
    }

    pub fn cascade_stop(&self) {
        self.cascade.try_lock().expect("Cascade locked during stop").stop();
    }

    pub fn cascade_height(&self) -> Angle {
        self.cascade.try_lock().expect("Cascade locked during height").get_height()
    }

    pub fn claw_open(&self) {
        self.claw.try_lock().expect("Claw locked during open").open();
    }

    pub fn claw_close(&self) {
        self.claw.try_lock().expect("Claw locked during close").close();
    }

    pub fn claw_toggle(&self) {
        self.claw.try_lock().expect("Claw locked during toggle").toggle();
    }

    pub fn claw_is_open(&self) -> bool {
        self.claw.try_lock().expect("Claw locked during read").is_open()
    }

    /// Runs `routine` in a background task, cancelling any macro already running.
    /// If `timeout` elapses first, the routine is cancelled and the cascade is stopped.
    ///
    /// ```ignore
    /// pub fn example(&self) {
    ///     self.spawn_macro(LiftState::Example, Duration::from_millis(2000), |lift| async move {
    ///         lift.claw_close();
    ///         lift.move_to(Angle::from_degrees(200.0)).await;
    ///         lift.claw_open();
    ///     });
    /// }
    /// ```
    #[allow(dead_code)]
    fn spawn_macro<F, Fut>(&self, next: LiftState, timeout: Duration, routine: F)
    where
        F: FnOnce(Lift) -> Fut,
        Fut: Future<Output = ()> + 'static,
    {
        *self.state.try_lock().expect("Lift state locked while starting macro") = next;

        let lift = self.clone();
        let routine = routine(self.clone());

        let task = spawn(async move {
            if let Either::Right(_) = select(pin!(routine), pin!(sleep(timeout))).await {
                lift.cascade_stop();
            }
            *lift.state.try_lock().expect("Lift state locked while ending macro") = LiftState::None;
        });

        // Dropping the previous task cancels it at its next await.
        *self.current_macro.borrow_mut() = Some(task);
    }

    /// Runs the cascade PID until it settles.
    pub async fn move_to(&self, height: Angle) {
        while self.cascade.try_lock().expect("Cascade locked during move").tick_move_height(height) {
            sleep(SLEEP_INTERVAL).await;
        }
    }
}

pub struct Cascade {
    pub motors: Vec<Motor>,
    pub encoder: RotationSensor,
    pub pid: PIDController,
    pub stopping_mode: BrakeMode,
    pub exit_range: f64,
    pub max_lower: f64,
}

impl Cascade {
    pub fn new(motors: Vec<Motor>, pid: PIDController, encoder: RotationSensor, stopping_mode: BrakeMode) -> Self {
        Self { motors, encoder, pid, stopping_mode, exit_range: 2.0, max_lower: 8.0 }
    }

    pub fn extend(&mut self) {
        self.motors.iter_mut().for_each(|motor| motor.set_voltage(motor.max_voltage()).unwrap_or_default());
    }

    pub fn retract(&mut self) {
        self.motors.iter_mut().for_each(|motor| motor.set_voltage(-motor.max_voltage()/1.5).unwrap_or_default());
    }

    pub fn stop(&mut self) {
        self.motors.iter_mut().for_each(|motor| motor.brake(self.stopping_mode).unwrap_or_default());
    }

    pub fn get_height(&self) -> Angle {
        self.encoder.position().unwrap_or_default()
    }

    pub fn reset_encoder(&mut self) {
        self.encoder.set_position(Angle::ZERO).ok();
    }

    /// Returns `true` while still moving; stops the motors and returns `false` once within `exit_range`.
    pub fn tick_move_height(&mut self, target_height: Angle) -> bool {
        let dt = SLEEP_INTERVAL.as_millis() as f64;
        let error = (target_height - self.get_height()).as_degrees();
        let mut power = self.pid.get_power(error, dt);
        if error < 0.0 {
            power = power.clamp(-self.max_lower, self.max_lower);
        }

        if error.abs() < self.exit_range {
            self.stop();
            return false;
        }

        self.motors.iter_mut().for_each(|motor| motor.set_voltage(power).unwrap_or_default());
        true
    }
}

/// Pneumatic claw. Solenoid high = open.
pub struct Claw {
    pub solenoid: AdiDigitalOut,
}

impl Claw {
    pub fn new(solenoid: AdiDigitalOut) -> Self {
        Self { solenoid }
    }

    pub fn open(&mut self) {
        self.solenoid.set_high().ok();
    }

    pub fn close(&mut self) {
        self.solenoid.set_low().ok();
    }

    pub fn toggle(&mut self) {
        self.solenoid.toggle().ok();
    }

    pub fn is_open(&self) -> bool {
        self.solenoid.is_high().unwrap_or(false)
    }
}
