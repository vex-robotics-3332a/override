use vexide::sync::Mutex;
use alloc::rc::Rc;
use vexide::prelude::*;
use crate::{localization::localization::Localization, subsystems::drivetrain::Drivetrain};

pub struct Chassis {
    pub drivetrain: Rc<Mutex<Drivetrain>>,
    pub localization: Localization,
}