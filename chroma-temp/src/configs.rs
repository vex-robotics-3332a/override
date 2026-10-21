use vexide::prelude::*;
use crate::motion_control::pid::PIDController;

pub struct RamseteConfig {
    pub beta: f64,
    pub zeta: f64,
}
pub struct ChassisConfig {
    pub ramsete_config: RamseteConfig,
    /// `drive_along` exits once the robot is this close (in) to the profile's final point.
    pub exit_range: f64,
}

pub struct LiftConfig {
    pub lift_pid: PIDController,
    pub lift_motors: Vec<Motor>,
}

pub struct ArmConfig {
    pub arm_pid: PIDController,
    pub arm_motor: Motor,
}
pub struct ClawConfig {
    pub claw_pid: PIDController,
    pub claw_clamp: AdiDigitalOut,
}