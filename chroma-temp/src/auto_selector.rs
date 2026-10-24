use alloc::boxed::Box;
use alloc::format;
use core::future::Future;
use core::pin::Pin;
use vexide::controller::{Controller, ControllerState};

use crate::autos;
use crate::robot::Robot;

pub type LocalBoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + 'a>>;

pub type AutoFn = for<'a> fn(&'a mut Robot) -> LocalBoxFuture<'a, ()>;

pub struct AutoRoutine {
    pub name: &'static str,
    pub run: AutoFn,    
}

// Closures can't express the higher-ranked lifetime on the boxed future, so each routine needs a named wrapper.
fn left_3(robot: &mut Robot) -> LocalBoxFuture<'_, ()> {
    Box::pin(autos::left_3::run(robot))
}

fn right_3(robot: &mut Robot) -> LocalBoxFuture<'_, ()> {
    Box::pin(autos::right_3::run(robot))
}

fn left_1(robot: &mut Robot) -> LocalBoxFuture<'_, ()> {
    Box::pin(autos::left_1::run(robot))
}

fn right_1(robot: &mut Robot) -> LocalBoxFuture<'_, ()> {
    Box::pin(autos::right_1::run(robot))
}

pub const AUTOS: &[AutoRoutine] = &[
    AutoRoutine { name: "Left 3", run: left_3 },
    AutoRoutine { name: "Right 3", run: right_3 },
    AutoRoutine { name: "Left 1", run: left_1 },
    AutoRoutine { name: "Right 1", run: right_1 },
];

pub struct AutoSelector {
    routines: &'static [AutoRoutine],
    index: usize,
    display_dirty: bool,
    screen_cleared: bool,
}

impl AutoSelector {
    pub fn new(routines: &'static [AutoRoutine]) -> Self {
        assert!(!routines.is_empty(), "AutoSelector needs at least one routine");
        Self { routines, index: 0, display_dirty: true, screen_cleared: false }
    }

    pub fn selected(&self) -> &'static AutoRoutine {
        &self.routines[self.index]
    }

    pub fn next(&mut self) {
        self.index = (self.index + 1) % self.routines.len();
        self.display_dirty = true;
    }

    pub fn prev(&mut self) {
        self.index = (self.index + self.routines.len() - 1) % self.routines.len();
        self.display_dirty = true;
    }

    pub fn select(&mut self, name: &str) -> bool {
        match self.routines.iter().position(|routine| routine.name == name) {
            Some(index) => {
                self.index = index;
                self.display_dirty = true;
                true
            }
            None => false,
        }
    }

    /// Left/right arrows cycle the selection; the controller screen is redrawn whenever it changes.
    /// Only awaits (up to ~50ms over VEXnet) on ticks where the screen actually needs redrawing.
    pub async fn tick(&mut self, controller: &mut Controller, state: &ControllerState) {
        if state.button_left.is_now_pressed() {
            self.prev();
        }
        if state.button_right.is_now_pressed() {
            self.next();
        }

        if !self.display_dirty {
            return;
        }

        // Writes fail while the controller is disconnected; leave the flags set so a later tick retries.
        // try_set_text is avoided because vexide 0.8 passes its column to the SDK without the -1 offset.
        if !self.screen_cleared {
            if controller.clear_screen().await.is_err() {
                return;
            }
            self.screen_cleared = true;
        }

        // Padded to the full line width so a shorter name overwrites a longer one.
        let mut line = format!("Auto: {}", self.selected().name);
        line.truncate(Controller::MAX_COLUMNS);
        let line = format!("{line:<width$}", width = Controller::MAX_COLUMNS);
        if controller.set_text(line, 1, 1).await.is_ok() {
            self.display_dirty = false;
        }
    }
}
