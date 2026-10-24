// Includes cascade (winch), arm, carriage, and claw.
use crate::motion_control::pid::PIDController;
use crate::telemetry::TelemetryFile;
use crate::{telemetry, SLEEP_INTERVAL};
use alloc::rc::Rc;
use std::time::Instant;
use core::cell::RefCell;
use core::pin::pin;
use vexide::task::Task;
use std::time::Duration;
use futures_util::future::{select, Either};
use vexide::{math::Angle, prelude::*, smart::motor::BrakeMode, sync::Mutex};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum LiftState {
    Intake,
    Score,
    Drop,
    None,
}

impl LiftState {
    pub fn repr(&self) -> &str {
        match self {
            LiftState::Intake => "Intake",
            LiftState::Score => "Score",
            LiftState::Drop => "Drop",
            LiftState::None => "None",
        }
    }
}

#[derive(Clone)]
pub struct Lift {
    pub cascade: Rc<Mutex<Cascade>>,
    pub wrist: Rc<Mutex<Wrist>>,
    pub end_affector: Rc<Mutex<EndAffector>>,
    pub state: Rc<Mutex<LiftState>>,
    current_macro: Rc<RefCell<Option<Task<()>>>>,
    wrist_log: Rc<TelemetryFile>,
    clock: Instant,
}

impl Lift {
    pub fn new(cascade: Cascade, wrist: Wrist, end_affector: EndAffector) -> Self {
        Self { 
            cascade: Rc::new(Mutex::new(cascade)),
            wrist: Rc::new(Mutex::new(wrist)),
            end_affector: Rc::new(Mutex::new(end_affector)),
            state: Rc::new(Mutex::new(LiftState::None)),
            current_macro: Rc::new(RefCell::new(None)),
            wrist_log: Rc::new(TelemetryFile::new_csv(
                "wrist.txt",
                &["timestamp", "wrist angle"],
                TelemetryFile::DEFAULT_BUFFER_SIZE,
                TelemetryFile::DEFAULT_ITER_TIMEOUT,
            )),
            clock: Instant::now(),
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

    pub fn wrist_up(&self) {
        self.wrist.try_lock().expect("Wrist locked during up").up();
    }

    pub fn wrist_down(&self) {
        self.wrist.try_lock().expect("Wrist locked during down").down();
    }

    pub fn wrist_stop(&self) {
        self.wrist.try_lock().expect("Wrist locked during stop").stop();
    }

    pub fn end_affector_intake(&self) {
        self.end_affector.try_lock().expect("End affector locked during intake").intake();
    }

    pub fn end_affector_outtake(&self) {
        self.end_affector.try_lock().expect("End affector locked during outtake").outtake();
    }

    pub fn end_affector_stop(&self) {
        self.end_affector.try_lock().expect("End affector locked during stop").stop();
    }

    pub fn cascade_height(&self) -> Angle {
        self.cascade.try_lock().expect("Cascade locked during height").get_height()
    }

    pub fn wrist_angle(&self) -> Angle {
        self.wrist.try_lock().expect("Wrist locked during angle").get_wrist_angle()
    }
    
    pub fn wrist_down_soft(&self) {
        self.wrist.try_lock().expect("Wrist locked during down soft").down_soft();
    }

    pub fn drop_stack(&self) {
        self.spawn_macro(LiftState::Drop, Duration::from_millis(3000), |lift| async move {
            lift.end_affector_intake();
            lift.move_to(lift.cascade_height() - Angle::from_degrees(60.0), Angle::ZERO).await;
            lift.end_affector_outtake();
            sleep(Duration::from_millis(100)).await;
            lift.move_to(lift.cascade_height() + Angle::from_degrees(80.0), Angle::from_degrees(60.0)).await;
        });
    }

    pub fn intake_position(&self) {
        self.spawn_macro(LiftState::Intake, Duration::from_millis(2000), |lift| async move {
            lift.end_affector_intake();
            lift.move_to(Angle::from_degrees(136.0), Angle::from_degrees(-75.0)).await;
            lift.wrist_down_soft();
            sleep(Duration::from_millis(200)).await;
            lift.set_wrist_position(Angle::from_degrees(-80.0));
        });
    }

    pub fn score_position(&self) {
        self.spawn_macro(LiftState::Score, Duration::from_millis(2000), |lift| async move {
            lift.end_affector_intake();
            lift.move_to(Angle::from_degrees(238.0), Angle::from_degrees(0.0)).await;
        });
    }

    pub fn test_macro_up(&self) {
        self.spawn_macro(LiftState::Score, Duration::from_millis(2000), |lift| async move {
            lift.move_to(Angle::from_degrees(200.0), Angle::from_degrees(-20.0)).await;
        });
    }

    pub fn test_macro_down(&self) {
        self.spawn_macro(LiftState::Score, Duration::from_millis(2000), |lift| async move {
            lift.move_to(Angle::from_degrees(200.0), Angle::from_degrees(20.0)).await;
        });
    }

    pub fn set_wrist_position(&self, position: Angle) {
        self.wrist.try_lock().expect("Wrist locked during set position").set_position(position);
    }

    pub fn score_at(&self, angle: f64, timeout: u64) {
        self.spawn_macro(LiftState::Score, Duration::from_millis(timeout), |lift| async move {
            lift.end_affector_intake();
            lift.move_to(Angle::from_degrees(angle), Angle::from_degrees(0.0)).await;
        });
    }

    pub fn pin_grab(&self) {
        self.spawn_macro(LiftState::Score, Duration::from_millis(2000), |lift| async move {
            lift.end_affector_intake();
            lift.move_to(Angle::from_degrees(5.0), Angle::from_degrees(-10.0)).await;
        });
    }

    /// Runs `routine` in a background task, cancelling any macro already running.
    /// If `timeout` elapses first, the routine is cancelled and the cascade and wrist are stopped.
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
                lift.wrist_stop();
            }
            *lift.state.try_lock().expect("Lift state locked while ending macro") = LiftState::None;
        });

        // Dropping the previous task cancels it at its next await.
        *self.current_macro.borrow_mut() = Some(task);
    }

    /// Runs the cascade and wrist PIDs together until both settle.
    pub async fn move_to(&self, cascade_height: Angle, wrist_angle: Angle) {
        let mut cascade_moving = true;
        let mut wrist_moving = true;

        while cascade_moving || wrist_moving {
            {
                let mut cascade = self.cascade.try_lock().expect("Cascade locked during move");
                let mut wrist = self.wrist.try_lock().expect("Wrist locked during move");

                if cascade_moving {
                    cascade_moving = cascade.tick_move_height(cascade_height).await;
                }
                if wrist_moving {
                    wrist_moving = wrist.tick_move_to_angle(wrist_angle).await;
                }

                telemetry!(
                    self.wrist_log,
                    self.clock.elapsed().as_millis(),
                    wrist.get_wrist_angle().as_degrees(),
                    wrist_angle.as_degrees()
                );
            }

            sleep(SLEEP_INTERVAL).await;
        }
        println!("Cascade position: {:.1} deg", self.cascade_height().as_degrees());
        println!("Wrist angle: {:.1} deg", self.wrist_angle().as_degrees());
    }
}

pub struct Cascade {
    pub motors: Vec<Motor>,
    pub pid: PIDController,
    pub stopping_mode: BrakeMode,
    pub exit_range: f64,
    pub max_lower: f64
}

impl Cascade {
    pub fn new(motors: Vec<Motor>, pid: PIDController, stopping_mode: BrakeMode) -> Self {
        Self { motors, pid, stopping_mode, exit_range: 2.0, max_lower: 8.0 }
    }

    //i hope these worklol

    pub fn extend(&mut self) {
        self.motors.iter_mut().for_each(|motor| motor.set_voltage(motor.max_voltage()).unwrap_or_default());
    }

    pub fn retract(&mut self) {
        self.motors.iter_mut().for_each(|motor| motor.set_voltage(-5.0).unwrap_or_default());
    }

    pub fn stop(&mut self) {
        self.motors.iter_mut().for_each(|motor| motor.brake(self.stopping_mode).unwrap_or_default());
    }

    pub fn get_height(&self) -> Angle {
        let total_angle: Angle = self.motors.iter().fold(Angle::ZERO, |acc, motor| acc + motor.position().unwrap_or_default());
        Angle::from_degrees(total_angle.as_degrees() / 2.0)
    }

    pub async fn tick_move_height(&mut self, target_height: Angle) -> bool {
        let dt = SLEEP_INTERVAL.as_millis() as f64;
        let error = (target_height - self.get_height()).as_degrees();
        let mut power = self.pid.get_power(error, dt);
        if error < 0.0 {
            power = power.clamp(-self.max_lower, self.max_lower);
        }

        self.motors.iter_mut().for_each(|motor| motor.set_voltage(power).unwrap_or_default());

        if error.abs() < self.exit_range {
            self.stop();
            return false;
        }


        true
    }
}

pub struct EndAffector {
    pub motor: Motor,
    pub stopping_mode: BrakeMode,
}

impl EndAffector {
    pub fn new(motor: Motor, stopping_mode: BrakeMode) -> Self {
        Self { motor, stopping_mode }
    }

    pub fn intake(&mut self) {
        self.motor.set_voltage(self.motor.max_voltage()).ok();
    }

    pub fn outtake(&mut self) {
        self.motor.set_voltage(-self.motor.max_voltage()).ok();
    }

    pub fn stop(&mut self) {
        self.motor.brake(self.stopping_mode).ok();
    }

}

pub struct Wrist {
    pub motor: Motor,
    pub ratio: f64,
    pub pid: PIDController,
    pub stopping_mode: BrakeMode,
    pub min_bound: f64,
}

impl Wrist {
    pub fn new(motor: Motor, ratio: f64, pid: PIDController, stopping_mode: BrakeMode) -> Self {
        Self {
            motor,
            ratio,
            pid,
            stopping_mode,
            min_bound: 1.0
        }
    }
    //temporary control functions
    pub fn up(&mut self) {
        self.motor.set_voltage(-self.motor.max_voltage() / 2.0).ok();
    }

    pub fn down(&mut self) {
        self.motor.set_voltage(self.motor.max_voltage() / 2.0).ok();
    }

    pub fn down_soft(&mut self) {
        self.motor.set_voltage(self.motor.max_voltage() / 8.0).ok();
    }

    /// Sets the motor's encoder so `get_wrist_angle` reads `wrist_angle`. Doesn't move the wrist.
    pub fn set_position(&mut self, wrist_angle: Angle) {
        let motor_angle = Angle::from_degrees(wrist_angle.as_degrees() / -self.ratio);
        self.motor.set_position(motor_angle).ok();
    }

    pub fn get_wrist_angle(&self) -> Angle {
        let motor_angle = self.motor.position().ok().unwrap_or_default();
        Angle::from_degrees(motor_angle.as_degrees() * -self.ratio)
    }

    pub fn stop(&mut self) {
        self.motor.brake(self.stopping_mode).ok();
    }

    pub fn reset(&mut self) {
        self.pid.reset();
    }

    pub async fn tick_move_to_angle(&mut self, angle: Angle) -> bool {
        let dt = SLEEP_INTERVAL.as_millis() as f64;
        let error = (angle - self.get_wrist_angle()).as_degrees();
        let power = self.pid.get_power(error, dt);
        self.motor.set_voltage(-power).ok();

        if error.abs() < self.min_bound {
            self.stop();
            return false;
        }

        true
    }
}