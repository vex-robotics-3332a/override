use alloc::boxed::Box;
use alloc::format;
use core::future::Future;
use core::pin::Pin;
use core::time::Duration;
use std::time::Instant;
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
// fn left_3(robot: &mut Robot) -> LocalBoxFuture<'_, ()> {
//     Box::pin(autos::left_3::run(robot))
// }

// fn right_3(robot: &mut Robot) -> LocalBoxFuture<'_, ()> {
//     Box::pin(autos::right_3::run(robot))
// }

// fn left_1(robot: &mut Robot) -> LocalBoxFuture<'_, ()> {
//     Box::pin(autos::left_1::run(robot))
// }

// fn right_1(robot: &mut Robot) -> LocalBoxFuture<'_, ()> {
//     Box::pin(autos::right_1::run(robot))
// }
fn test(robot: &mut Robot) -> LocalBoxFuture<'_, ()> {
        Box::pin(autos::test::run(robot))
}

fn skills(robot: &mut Robot) -> LocalBoxFuture<'_, ()> {
    Box::pin(autos::skills::run(robot))
}

pub const AUTOS: &[AutoRoutine] = &[
    AutoRoutine { name: "Test", run: test },
    AutoRoutine { name: "Skills", run: skills },
];

pub struct AutoSelector {
    routines: &'static [AutoRoutine],
    index: usize,
    /// "Red" or "Blue", matching the names `Toggle::get_color` returns.
    alliance: &'static str,
    auto_dirty: bool,
    alliance_dirty: bool,
    screen_cleared: bool,
    last_write: Option<Instant>,
}

impl AutoSelector {
    /// The controller silently drops screen writes sent closer together than ~50ms.
    const WRITE_INTERVAL: Duration = Duration::from_millis(60);

    pub fn new(routines: &'static [AutoRoutine]) -> Self {
        assert!(!routines.is_empty(), "AutoSelector needs at least one routine");
        Self {
            routines,
            index: 0,
            alliance: "Red",
            auto_dirty: true,
            alliance_dirty: true,
            screen_cleared: false,
            last_write: None,
        }
    }

    pub fn selected(&self) -> &'static AutoRoutine {
        &self.routines[self.index]
    }

    pub fn alliance(&self) -> &'static str {
        self.alliance
    }

    pub fn toggle_alliance(&mut self) {
        self.alliance = if self.alliance == "Red" { "Blue" } else { "Red" };
        self.alliance_dirty = true;
    }

    pub fn next(&mut self) {
        self.index = (self.index + 1) % self.routines.len();
        self.auto_dirty = true;
    }

    pub fn prev(&mut self) {
        self.index = (self.index + self.routines.len() - 1) % self.routines.len();
        self.auto_dirty = true;
    }

    pub fn select(&mut self, name: &str) -> bool {
        match self.routines.iter().position(|routine| routine.name == name) {
            Some(index) => {
                self.index = index;
                self.auto_dirty = true;
                true
            }
            None => false,
        }
    }

    /// Left/right arrows cycle the selection, up swaps alliance; the controller screen is redrawn whenever either changes.
    /// Sends at most one screen write per tick, spaced by `WRITE_INTERVAL`.
    pub async fn tick(&mut self, controller: &mut Controller, state: &ControllerState) {
        if state.button_left.is_now_pressed() {
            self.prev();
        }
        if state.button_right.is_now_pressed() {
            self.next();
        }
        if state.button_up.is_now_pressed() {
            self.toggle_alliance();
        }

        if self.screen_cleared && !self.auto_dirty && !self.alliance_dirty {
            return;
        }
        if self.last_write.is_some_and(|last| last.elapsed() < Self::WRITE_INTERVAL) {
            return;
        }
        self.last_write = Some(Instant::now());

        // Writes fail while the controller is disconnected; leave the flags set so a later tick retries.
        // try_set_text is avoided because vexide 0.8 passes its column to the SDK without the -1 offset.
        if !self.screen_cleared {
            self.screen_cleared = controller.clear_screen().await.is_ok();
        } else if self.auto_dirty {
            let line = Self::padded(format!("Auto: {}", self.selected().name));
            self.auto_dirty = controller.set_text(line, 1, 1).await.is_err();
        } else if self.alliance_dirty {
            let line = Self::padded(format!("Alliance: {}", self.alliance));
            self.alliance_dirty = controller.set_text(line, 2, 1).await.is_err();
        }
    }

    /// Padded to the full line width so a shorter string overwrites a longer one.
    fn padded(mut line: String) -> String {
        line.truncate(Controller::MAX_COLUMNS);
        format!("{line:<width$}", width = Controller::MAX_COLUMNS)
    }
}
