
//auto-generated imports
use crate::chassis::Chassis;
use crate::subsystems::*;
use vexide::math::*;
use crate::helpers::*;
use crate::motion_control::ramsete::*;

pub async fn run(chassis: &mut Chassis) {
	chassis.set_pose(Pose::new(17.97224320241692, 18.146148036253763, Angle::from_degrees(0.0)));

	let mut motion1 = MotionProfile::new("test/motion1.json");
	chassis.drive_along(&mut motion1).await;
                
}
