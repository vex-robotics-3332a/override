// Includes cascade (winch), arm, carriage, and claw.
use crate::motion_control::pid::PIDController;
use crate::SLEEP_INTERVAL;
use alloc::rc::Rc;
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
    pub state: Rc<Mutex<LiftState>>
}

impl Lift {
    pub fn new(cascade: Cascade, wrist: Wrist, end_affector: EndAffector) -> Self {
        Self { 
            cascade: Rc::new(Mutex::new(cascade)),
            wrist: Rc::new(Mutex::new(wrist)),
            end_affector: Rc::new(Mutex::new(end_affector)),
            state: Rc::new(Mutex::new(LiftState::None))
        }
    }

    //basic manipulators
    pub fn macro_running(&self) -> bool {
        *self.state.try_lock().expect("Lift state locked while reading") != LiftState::None
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

    pub fn drop_stack(&self) {
        self.spawn_macro(LiftState::Drop, |lift| async move {
            lift.end_affector_intake();
            lift.move_to(lift.cascade_height() - Angle::from_degrees(40.0), Angle::ZERO).await;
            lift.end_affector_outtake();
            lift.move_to(lift.cascade_height(), Angle::from_degrees(40.0)).await;
        });
    }

    pub fn intake_position(&self) {
        self.spawn_macro(LiftState::Intake, |lift| async move {
            lift.end_affector_intake();
            lift.move_to(Angle::from_degrees(143.0), Angle::from_degrees(-72.6)).await;
        });
    }

    pub fn score_position(&self) {
        self.spawn_macro(LiftState::Score, |lift| async move {
            lift.end_affector_intake();
            lift.move_to(Angle::from_degrees(238.0), Angle::from_degrees(0.0)).await;
        });
    }

    pub fn test_macro_up(&self) {
        self.spawn_macro(LiftState::Score, |lift| async move {
            lift.move_to(Angle::from_degrees(200.0), Angle::from_degrees(-20.0)).await;
        });
    }

    pub fn test_macro_down(&self) {
        self.spawn_macro(LiftState::Score, |lift| async move {
            lift.move_to(Angle::from_degrees(200.0), Angle::from_degrees(20.0)).await;
        });
    }

    /// Runs `routine` in a detached task. Ignored if another macro is already running.
    fn spawn_macro<F, Fut>(&self, next: LiftState, routine: F)
    where
        F: FnOnce(Lift) -> Fut,
        Fut: Future<Output = ()> + 'static,
    {
        {
            let mut state = self.state.try_lock().expect("Lift state locked while starting macro");
            if *state != LiftState::None {
                return;
            }
            *state = next;
        }

        let state = Rc::clone(&self.state);
        let routine = routine(self.clone());

        spawn(async move {
            routine.await;
            *state.try_lock().expect("Lift state locked while ending macro") = LiftState::None;
        }).detach();
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
            }

            sleep(SLEEP_INTERVAL).await;
        }
    }
}

pub struct Cascade {
    pub motors: Vec<Motor>,
    pub pid: PIDController,
    pub stopping_mode: BrakeMode,
    pub exit_range: f64
}

impl Cascade {
    pub fn new(motors: Vec<Motor>, pid: PIDController, stopping_mode: BrakeMode) -> Self {
        Self { motors, pid, stopping_mode, exit_range: 6.0 }
    }

    //i hope these worklol

    pub fn extend(&mut self) {
        self.motors.iter_mut().for_each(|motor| motor.set_voltage(motor.max_voltage()).unwrap_or_default());
    }

    pub fn retract(&mut self) {
        self.motors.iter_mut().for_each(|motor| motor.set_voltage(-motor.max_voltage()).unwrap_or_default());
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
        let power = self.pid.get_power(error, dt);
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