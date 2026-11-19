// Includes cascade (winch) and pneumatic claw.
use crate::motion_control::pid::PIDController;
use crate::telemetry::TelemetryFile;
use crate::{telemetry, SLEEP_INTERVAL};
use alloc::rc::Rc;
use vexide::smart::distance::DistanceObject;
use core::cell::RefCell;
use core::pin::pin;
use futures_util::future::{select, Either};
use std::time::Duration;
use vexide::task::Task;
use vexide::{math::Angle, prelude::*, smart::motor::BrakeMode, sync::Mutex};
use crate::MM_PER_INCH;
use std::time::Instant;

#[derive(Clone)]
pub struct Lift {
    pub cascade: Rc<Mutex<Cascade>>,
    pub claw: Rc<Mutex<Claw>>,
    pub back_sensor: Rc<Mutex<BackSensor>>,
    current_macro: Rc<RefCell<Option<Task<()>>>>,
}

impl Lift {
    pub fn new(cascade: Cascade, claw: Claw, back_sensor: BackSensor) -> Self {
        Self {
            cascade: Rc::new(Mutex::new(cascade)),
            claw: Rc::new(Mutex::new(claw)),
            back_sensor: Rc::new(Mutex::new(back_sensor)),
            current_macro: Rc::new(RefCell::new(None)),
        }
    }

    pub async fn slam_score(&self) {
        self.cascade_to_height(200.0);
        sleep(Duration::from_millis(70)).await;
        self.matchload();
    }

    pub fn reset_encoder(&self) {
        self.cascade.try_lock().expect("Cascade locked during reset encoder").reset_encoder();
    }
    //back sensor stuff
    pub fn at_alliance_goal(&self) -> bool {
        self.back_sensor.try_lock().expect("Back sensor locked during at alliance goal").is_close(1.8)
    }

    pub fn at_neutral_goal(&self) -> bool {
        self.back_sensor.try_lock().expect("Back sensor locked during at neutral goal").is_close(0.4)
    }


    //basic manipulators
    pub fn macro_running(&self) -> bool {
        self.current_macro.borrow().as_ref().is_some_and(|task| !task.is_finished())
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
        if self.cascade_height().as_degrees() < 280.0 {
            self.spawn_macro(Duration::from_millis(350), |lift| async move {
                //if lift is at ~20 deg picking up say just a pin, this ensures it doesn't jerk all the way up to pin height
                sleep(Duration::from_millis(125)).await;
                lift.move_to(Angle::from_degrees(lift.cascade_height().as_degrees() + 100.0)).await;
            });
        }
    }

    pub fn claw_toggle(&self) {
        if self.claw_is_open() {
            self.claw_close();
        } else {
            self.claw_open();
        }
    }

    pub fn claw_is_open(&self) -> bool {
        self.claw.try_lock().expect("Claw locked during read").is_open()
    }

    pub fn matchload(&self) {
        self.spawn_macro(Duration::from_millis(1000), |lift| async move {
            lift.claw_open();
            lift.move_to(Angle::from_degrees(280.0)).await;
            println!("matchload pos {}", lift.cascade_height().as_degrees());
        });
    }

    pub fn cascade_to_height(&self, height: f64) {
        let height_angle = Angle::from_degrees(height);
        self.spawn_macro(Duration::from_millis(2000), |lift| async move {
            lift.move_to(height_angle).await;
            println!("casc h: {}", lift.cascade_height().as_degrees());
        });
    }

    
    pub async fn auto_clamp_task(&self, timeout: u64) {
        let start = Instant::now();
        let distance = 2.8;
        while self.back_sensor.try_lock().expect("Back sensor locked during auto clamp task").distance().is_none_or(|d| d > distance) {
            if start.elapsed() > Duration::from_millis(timeout) {
                break;
            }
            sleep(SLEEP_INTERVAL).await;
        }
        self.claw_close();
    }

    pub fn auto_clamp(&self) {
        let distance = 2.8;
        match self.back_sensor.try_lock().expect("Back sensor locked during auto clamp").distance() {
            Some(d) if d < distance => self.claw_close(),
            Some(_) => self.claw_open(),
            None => {}
        }
    }

    /// Runs `routine` in a background task, cancelling any macro already running.
    /// If `timeout` elapses first, the routine is cancelled and the cascade is stopped.
    ///
    /// ```ignore
    /// pub fn example(&self) {
    ///     self.spawn_macro(Duration::from_millis(2000), |lift| async move {
    ///         lift.claw_close();
    ///         lift.move_to(Angle::from_degrees(200.0)).await;
    ///         lift.claw_open();
    ///     });
    /// }
    /// ```
    fn spawn_macro<F, Fut>(&self, timeout: Duration, routine: F)
    where
        F: FnOnce(Lift) -> Fut,
        Fut: Future<Output = ()> + 'static,
    {
        let lift = self.clone();
        let routine = routine(self.clone());

        let task = spawn(async move {
            match select(pin!(routine), pin!(sleep(timeout))).await {
                Either::Left(_) => {}
                Either::Right(_) => {
                    println!("Macro timed out");
                    lift.cascade_stop();
                }
            }
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
    pub neutral_voltage: f64,
}

impl Cascade {
    pub fn new(motors: Vec<Motor>, pid: PIDController, encoder: RotationSensor, stopping_mode: BrakeMode, neutral_voltage: f64) -> Self {
        Self { motors, encoder, pid, stopping_mode, exit_range: 6.0, max_lower: 12.0, neutral_voltage }
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
        // A disconnected encoder would read 0 and drive the lift until the macro times out.
        let Ok(height) = self.encoder.position() else {
            self.stop();
            return false;
        };
        let dt = SLEEP_INTERVAL.as_millis() as f64;
        let error = (target_height - height).as_degrees();
        let mut power = self.pid.get_power(error, dt) + self.neutral_voltage;
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

pub struct BackSensor {
    pub sensor: DistanceSensor,
}

impl BackSensor {
    pub fn new(sensor: DistanceSensor) -> Self {
        Self { sensor }
    }

    /// Distance in inches, or `None` if nothing is detected or the reading is invalid (exactly 0).
    pub fn distance(&self) -> Option<f64> {
        let object = self.sensor.object().ok()??;
        (object.distance != 0).then(|| object.distance as f64 / MM_PER_INCH)
    }

    pub fn is_close(&self, distance: f64) -> bool {
        self.distance().is_some_and(|d| d < distance)
    }
}
