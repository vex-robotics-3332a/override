use alloc::boxed::Box;
use core::future::Future;
use core::pin::Pin;

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
}

impl AutoSelector {
    pub fn new(routines: &'static [AutoRoutine]) -> Self {
        assert!(!routines.is_empty(), "AutoSelector needs at least one routine");
        Self { routines, index: 0 }
    }

    pub fn selected(&self) -> &'static AutoRoutine {
        &self.routines[self.index]
    }

    pub fn next(&mut self) {
        self.index = (self.index + 1) % self.routines.len();
    }

    pub fn prev(&mut self) {
        self.index = (self.index + self.routines.len() - 1) % self.routines.len();
    }

    pub fn select(&mut self, name: &str) -> bool {
        match self.routines.iter().position(|routine| routine.name == name) {
            Some(index) => {
                self.index = index;
                true
            }
            None => false,
        }
    }

    pub async fn run(&self, robot: &mut Robot) {
        (self.selected().run)(robot).await;
    }
}
