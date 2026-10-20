// Includes cascade (winch), arm, carriage, and claw.
use crate::motion_control::pid::PIDController;
use crate::SLEEP_INTERVAL;
use alloc::rc::Rc;
use core::cell::Cell;
use core::fmt;
use vexide::{math::Angle, prelude::*, smart::motor::BrakeMode, sync::Mutex};

/// Which lift macro is running, if any. `None` means idle.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum RunningMacro {
    Intake,
    Score,
    Test,
}

impl fmt::Display for RunningMacro {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            RunningMacro::Intake => "intake",
            RunningMacro::Score => "score",
            RunningMacro::Test => "test",
        })
    }
}

pub struct Lift {
    pub cascade: Rc<Mutex<Cascade>>,
    pub wrist: Rc<Mutex<Wrist>>,
    pub end_affector: Rc<Mutex<EndAffector>>,
    /// Latest readings. Cloned out of the subsystems before they are wrapped in mutexes,
    /// so debug can read them without locking.
    pub cascade_degrees: Rc<Cell<f64>>,
    pub wrist_degrees: Rc<Cell<f64>>,
    pub wrist_motor_degrees: Rc<Cell<f64>>,
    pub wrist_command_volts: Rc<Cell<f64>>,
    pub wrist_reading_ok: Rc<Cell<bool>>,
    /// `None` when idle. Set before a macro starts; cleared when it finishes.
    running_macro: Rc<Cell<Option<RunningMacro>>>,
}

impl Lift {
    pub fn new(cascade: Cascade, wrist: Wrist, end_affector: EndAffector) -> Self {
        let cascade_degrees = Rc::clone(&cascade.measured_degrees);
        let wrist_degrees = Rc::clone(&wrist.measured_degrees);
        let wrist_motor_degrees = Rc::clone(&wrist.motor_degrees);
        let wrist_command_volts = Rc::clone(&wrist.command_volts);
        let wrist_reading_ok = Rc::clone(&wrist.reading_ok);
        Self {
            cascade: Rc::new(Mutex::new(cascade)),
            wrist: Rc::new(Mutex::new(wrist)),
            end_affector: Rc::new(Mutex::new(end_affector)),
            cascade_degrees,
            wrist_degrees,
            wrist_motor_degrees,
            wrist_command_volts,
            wrist_reading_ok,
            running_macro: Rc::new(Cell::new(None)),
        }
    }

    pub fn current_macro(&self) -> Option<RunningMacro> {
        self.running_macro.get()
    }

    pub fn macro_state(&self) -> Rc<Cell<Option<RunningMacro>>> {
        Rc::clone(&self.running_macro)
    }

    /// Returns `false` if a macro is already stored (request dropped).
    fn try_spawn_macro<F>(&self, which: RunningMacro, routine: F) -> bool
    where
        F: Future<Output = ()> + 'static,
    {
        if self.running_macro.get().is_some() {
            return false;
        }
        self.running_macro.set(Some(which));
        let running_macro = Rc::clone(&self.running_macro);
        spawn(async move {
            let _clear = ClearRunningMacro(running_macro);
            routine.await;
        })
        .detach();
        true
    }

    async fn wait_until_idle_then_set(&self, which: RunningMacro) {
        loop {
            if self.running_macro.get().is_none() {
                self.running_macro.set(Some(which));
                return;
            }
            sleep(SLEEP_INTERVAL).await;
        }
    }

    //basic access functions

    pub fn cascade_extend(&self) {
        with_locked(&self.cascade, |c| c.extend());
    }

    pub fn cascade_retract(&self) {
        with_locked(&self.cascade, |c| c.retract());
    }

    pub fn cascade_stop(&self) {
        with_locked(&self.cascade, |c| c.stop());
    }

    pub fn wrist_up(&self) {
        with_locked(&self.wrist, |w| w.up());
    }

    pub fn wrist_down(&self) {
        with_locked(&self.wrist, |w| w.down());
    }

    pub fn wrist_stop(&self) {
        with_locked(&self.wrist, |w| w.stop());
    }

    pub fn end_affector_intake(&self) {
        with_locked(&self.end_affector, |e| e.intake());
    }

    pub fn end_affector_outtake(&self) {
        with_locked(&self.end_affector, |e| e.outtake());
    }

    pub fn end_affector_stop(&self) {
        with_locked(&self.end_affector, |e| e.stop());
    }

    //actual macros

    /// Fire-and-forget from sync code (teleop bindings). Drops if a macro is already running.
    pub fn spawn_test_macro(&self) -> bool {
        self.try_spawn_macro(
            RunningMacro::Test,
            test_macro(
                Rc::clone(&self.cascade),
                Rc::clone(&self.wrist),
                Rc::clone(&self.end_affector),
            ),
        )
    }

    pub fn spawn_score_position(&self) -> bool {
        self.try_spawn_macro(
            RunningMacro::Score,
            score_position(
                Rc::clone(&self.cascade),
                Rc::clone(&self.wrist),
                Rc::clone(&self.end_affector),
            ),
        )
    }

    pub fn spawn_intake_position(&self) -> bool {
        self.try_spawn_macro(
            RunningMacro::Intake,
            intake_position(
                Rc::clone(&self.cascade),
                Rc::clone(&self.wrist),
                Rc::clone(&self.end_affector),
            ),
        )
    }

    /// Run on the current task; waits until `running_macro` is idle, then sets Intake.
    pub async fn intake_position(&self) {
        self.wait_until_idle_then_set(RunningMacro::Intake).await;
        let _clear = ClearRunningMacro(Rc::clone(&self.running_macro));
        intake_position(
            Rc::clone(&self.cascade),
            Rc::clone(&self.wrist),
            Rc::clone(&self.end_affector),
        )
        .await;
    }

    pub fn macro_running(&self) -> bool {
        self.running_macro.get().is_some()
    }
}

struct ClearRunningMacro(Rc<Cell<Option<RunningMacro>>>);

impl Drop for ClearRunningMacro {
    fn drop(&mut self) {
        self.0.set(None);
    }
}

/// Always lock cascade then wrist (same order everywhere). Locks are dropped before `sleep`
/// so other tasks can read positions between ticks.
async fn move_cascade_and_wrist(
    cascade: &Rc<Mutex<Cascade>>,
    wrist: &Rc<Mutex<Wrist>>,
    height: Angle,
    wrist_angle: Angle,
) {
    {
        let mut cascade = cascade.lock().await;
        let mut wrist = wrist.lock().await;
        cascade.height = height;
        cascade.pid.reset();
        wrist.pid.reset();
    }

    let dt = SLEEP_INTERVAL.as_secs_f64();
    loop {
        let both_done = {
            let mut cascade = cascade.lock().await;
            let mut wrist = wrist.lock().await;
            tick_together(&mut cascade, &mut wrist, height, wrist_angle, dt)
        };
        if both_done {
            return;
        }
        sleep(SLEEP_INTERVAL).await;
    }
}

pub async fn score_position(
    cascade: Rc<Mutex<Cascade>>,
    wrist: Rc<Mutex<Wrist>>,
    end_affector: Rc<Mutex<EndAffector>>,
) {
    move_cascade_and_wrist(
        &cascade,
        &wrist,
        Angle::from_degrees(238.0),
        Angle::from_degrees(0.0),
    )
    .await;
    let mut end_affector = end_affector.lock().await;
    end_affector.intake();
}

pub async fn intake_position(
    cascade: Rc<Mutex<Cascade>>,
    wrist: Rc<Mutex<Wrist>>,
    end_affector: Rc<Mutex<EndAffector>>,
) {
    {
        let mut end_affector = end_affector.lock().await;
        end_affector.intake();
    }
    move_cascade_and_wrist(
        &cascade,
        &wrist,
        Angle::from_degrees(150.0),
        Angle::from_degrees(-72.5),
    )
    .await;
}

pub async fn test_macro(
    cascade: Rc<Mutex<Cascade>>,
    wrist: Rc<Mutex<Wrist>>,
    end_affector: Rc<Mutex<EndAffector>>,
) {
    {
        let mut end_affector = end_affector.lock().await;
        end_affector.intake();
    }
    move_cascade_and_wrist(
        &cascade,
        &wrist,
        Angle::from_degrees(1000.0),
        Angle::from_degrees(0.0),
    )
    .await;

    {
        let cascade = cascade.lock().await;
        println!("cascade height up: {}", cascade.get_height().as_degrees());
    }

    move_cascade_and_wrist(
        &cascade,
        &wrist,
        Angle::from_degrees(300.0),
        Angle::from_degrees(0.0),
    )
    .await;

    {
        let cascade = cascade.lock().await;
        println!("cascade height down: {}", cascade.get_height().as_degrees());
    }
}

/// One PID tick for both axes. Caller must not hold the locks across `.await`.
fn tick_together(
    cascade: &mut Cascade,
    wrist: &mut Wrist,
    height: Angle,
    wrist_angle: Angle,
    dt: f64,
) -> bool {
    let cascade_done = {
        let error = (height - cascade.get_height()).as_degrees();
        if error.abs() < cascade.min_bound {
            cascade.stop();
            true
        } else {
            let power = cascade.pid.get_power(error, dt);
            cascade.spin_v(power);
            false
        }
    };
    let wrist_done = {
        // `wrist_angle` is end-effector angle; feedback matches get_wrist_angle (motor * -ratio).
        let measured = wrist.get_wrist_angle();
        let error = (wrist_angle - measured).as_degrees();
        if error.abs() < wrist.min_bound {
            wrist.command_volts.set(0.0);
            wrist.stop();
            true
        } else {
            let end_effector_command = wrist.pid.get_power(error, dt);
            let motor_voltage = if wrist.ratio.abs() > f64::EPSILON {
                -end_effector_command / wrist.ratio
            } else {
                0.0
            };
            wrist.command_volts.set(motor_voltage);
            wrist.motor.set_voltage(motor_voltage).ok();
            false
        }
    };
    cascade_done && wrist_done
}

fn with_locked<T, F>(mutex: &Rc<Mutex<T>>, f: F)
where
    F: FnOnce(&mut T),
{
    if let Some(mut guard) = mutex.try_lock() {
        f(&mut guard);
    }
}

pub struct Cascade {
    pub motors: Vec<Motor>,
    pub pid: PIDController,
    pub priority: usize,
    pub min_bound: f64,
    pub height: Angle,
    pub stopping_mode: BrakeMode,
    /// Latest measured height in degrees. Shared so debug can read it without the mutex.
    pub measured_degrees: Rc<Cell<f64>>,
}

impl Cascade {
    pub fn new(motors: Vec<Motor>, pid: PIDController, stopping_mode: BrakeMode) -> Self {
        Self {
            motors,
            pid,
            priority: 0,
            min_bound: 2.0,
            height: Angle::ZERO,
            stopping_mode: stopping_mode,
            measured_degrees: Rc::new(Cell::new(0.0)),
        }
    }

    pub fn get_height(&self) -> Angle {
        let height = self.motors[self.priority]
            .position()
            .unwrap_or_default();
        self.measured_degrees.set(height.as_degrees());
        height
    }

    fn spin_v(&mut self, volts: f64) {
        for motor in &mut self.motors {
            let _ = motor.set_voltage(volts);
        }
    }

    pub fn set_height(&mut self, height: Angle) {
        self.height = height;
    }

    /// Move the winch to a target encoder angle (used as a sub for height).
    pub async fn move_to_height(&mut self, height: Angle) {
        self.height = height;
        self.pid.reset();

        let dt = SLEEP_INTERVAL.as_secs_f64();
        loop {
            let error = (height - self.get_height()).as_degrees();
            let power = self.pid.get_power(error, dt);
            self.spin_v(power);

            if error.abs() < self.min_bound {
                self.stop();
                return;
            }

            sleep(SLEEP_INTERVAL).await;
        }
    }

    pub fn extend(&mut self) {
        self.spin_v(12.0);
    }

    pub fn retract(&mut self) {
        self.spin_v(-4.0);
    }

    pub fn stop(&mut self) {
        self.motors.iter_mut().for_each(|motor| { motor.brake(self.stopping_mode).ok(); });
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
    /// End-effector angle in degrees. Shared so debug can read it without the mutex.
    pub measured_degrees: Rc<Cell<f64>>,
    /// Raw motor encoder degrees, before the external ratio.
    pub motor_degrees: Rc<Cell<f64>>,
    /// Volts last sent to the wrist motor.
    pub command_volts: Rc<Cell<f64>>,
    /// False when `position()` failed and the published angle is the previous sample.
    pub reading_ok: Rc<Cell<bool>>,
    /// Last successful motor-encoder reading, in motor degrees. A failed `position()` must not
    /// become 0°, or the next macro drives as if the wrist jumped back to the start.
    last_motor_degrees: Cell<Option<f64>>,
}

impl Wrist {
    pub fn new(motor: Motor, ratio: f64, pid: PIDController, stopping_mode: BrakeMode) -> Self {
        Self {
            motor,
            ratio,
            pid,
            stopping_mode,
            min_bound: 1.0,
            measured_degrees: Rc::new(Cell::new(0.0)),
            motor_degrees: Rc::new(Cell::new(0.0)),
            command_volts: Rc::new(Cell::new(0.0)),
            reading_ok: Rc::new(Cell::new(false)),
            last_motor_degrees: Cell::new(None),
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
        let (motor_degrees, fresh) = match self.motor.position() {
            Ok(angle) => {
                let degrees = angle.as_degrees();
                self.last_motor_degrees.set(Some(degrees));
                (degrees, true)
            }
            Err(_) => (self.last_motor_degrees.get().unwrap_or(0.0), false),
        };
        // End-effector angle. `ratio` is output/motor (1:4 → 12/48). The minus matches
        // `up()` driving the motor negative.
        let end_effector = motor_degrees * -self.ratio;
        self.motor_degrees.set(motor_degrees);
        self.measured_degrees.set(end_effector);
        self.reading_ok.set(fresh);
        Angle::from_degrees(end_effector)
    }

    pub fn stop(&mut self) {
        self.motor.brake(self.stopping_mode).ok();
    }

    pub async fn move_to_angle(&mut self, angle: Angle) {
        self.pid.reset();

        let dt = SLEEP_INTERVAL.as_secs_f64();
        loop {
            let error = (angle - self.get_wrist_angle()).as_degrees();
            let power = self.pid.get_power(error, dt);
            self.motor.set_voltage(power).ok();

            if error.abs() < self.min_bound {
                self.stop();
                return;
            }

            sleep(SLEEP_INTERVAL).await;
        }
    }
}