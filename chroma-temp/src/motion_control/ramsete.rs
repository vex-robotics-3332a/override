use vexide::math::Angle;
use vexide::prelude::*;
use std::{time::Instant, fs::read_to_string};
use std::io::Write;
use serde::Deserialize;
use crate::helpers::*;
use crate::chassis::Chassis;
use crate::telemetry::TelemetryFile;
use crate::{telemetry, SLEEP_INTERVAL};

/// Unnormalized sinc: sin(x)/x, with the removable singularity at x = 0
/// handled via its limit (sinc(0) = 1).
fn sinc(x: f64) -> f64 {
    if x.abs() < 1e-9 {
        1.0
    } else {
        x.sin() / x
    }
}

#[derive(Deserialize)]
pub struct MotionProfile {
    pub points: Vec<Point>,

    #[serde(skip)]
    pub time: Option<Instant>,

    #[serde(skip)]
    pub current_index: usize,

    #[serde(skip)]
    pub total_time: i32
}

#[derive(Deserialize, Clone)]
pub struct Point {
    pub p: (f32, f32, f32),
    pub lv: f32,
    pub av: f32,
    pub t: i32,

    // Desired accelerations, filled in by `get_point` from the slope of the
    // current segment (not present in the JSON). in/s^2 and rad/s^2.
    #[serde(skip)]
    pub la: f32,
    #[serde(skip)]
    pub aa: f32,
}

pub struct RamseteConfig {
    pub beta: f64,
    pub zeta: f64
}

impl MotionProfile {
    pub fn new(path: &str) -> Self {
        let profile_string = read_to_string(path).unwrap();
        let mp: MotionProfile = serde_json::from_str(&profile_string).unwrap();
        mp
    }

    pub fn start(&mut self) {
        self.time = Some(Instant::now());
        self.total_time = self.points[self.points.len() - 1].t
    }
    
    pub fn get_point(&mut self) -> Point {
        let elapsed = self.time.unwrap().elapsed().as_millis() as i32;

        while self.current_index + 1 < self.points.len()
            && self.points[self.current_index + 1].t <= elapsed
        {
            self.current_index += 1;
        }

        let i = self.current_index;

        // At or past the final point: hold the last velocity, zero accel.
        if i + 1 >= self.points.len() {
            let mut last = self.points[self.points.len() - 1].clone();
            last.la = 0.0;
            last.aa = 0.0;
            return last;
        }

        let a = &self.points[i];
        let b = &self.points[i + 1];

        // Fraction of the way from `a` to `b`, clamped so a slightly-late tick
        // (or duplicate timestamps) can never over/undershoot.
        let span_ms = (b.t - a.t) as f64;
        let scale = if span_ms > 0.0 {
            (((elapsed - a.t) as f64) / span_ms).clamp(0.0, 1.0)
        } else {
            0.0
        };

        // Because we interpolate velocity linearly across the segment, the
        // desired acceleration is just the segment's constant slope.
        let span_s = span_ms / 1000.0;
        let (la, aa) = if span_s > 0.0 {
            (
                ((b.lv - a.lv) as f64 / span_s) as f32,
                ((b.av - a.av) as f64 / span_s) as f32,
            )
        } else {
            (0.0, 0.0)
        };

        Point {
            p: (
                interp(a.p.0 as f64, b.p.0 as f64, scale) as f32,
                interp(a.p.1 as f64, b.p.1 as f64, scale) as f32,
                interp(a.p.2 as f64, b.p.2 as f64, scale) as f32,
            ),
            lv: interp(a.lv as f64, b.lv as f64, scale) as f32,
            av: interp(a.av as f64, b.av as f64, scale) as f32,
            t: elapsed,
            la,
            aa,
        }
    }

    pub fn has_ended(&self) -> bool {
        let elapsed = self.time.unwrap().elapsed().as_millis() as i32;
        elapsed > self.total_time
    }
}

impl Chassis {
    pub async fn drive_along(&self, profile: &mut MotionProfile) {
        // running fully raw (no correction) to see if the profile is roughly correct

        let clock = Instant::now();

        let ct_e = TelemetryFile::new_csv(
            "ct_e.txt",
            &["timestamp", "cross track error"],
            TelemetryFile::DEFAULT_BUFFER_SIZE,
            TelemetryFile::DEFAULT_ITER_TIMEOUT,
        );
        let theta_e = TelemetryFile::new_csv(
            "theta_e.txt",
            &["timestamp", "heading error"],
            TelemetryFile::DEFAULT_BUFFER_SIZE,
            TelemetryFile::DEFAULT_ITER_TIMEOUT,
        );

        let beta = self.ramsete_config.beta;
        let zeta = self.ramsete_config.zeta;

        profile.start();
        loop {
            let time = clock.elapsed().as_millis();
            let mut dt = self.drivetrain.try_lock().expect("Dt lock failed while driving");
            let pose = self.pose.try_lock().expect("Pose lock failed while driving");
            let point = profile.get_point();

            let lv = point.lv as f64;
            let la = point.la as f64;
            let av = point.av as f64;
            let aa = point.aa as f64;

            //Ramsete part, modifies velocities
            //Represent error as a vector
            let global_vector = CartesianVector::new(point.p.0 as f64 - pose.x, point.p.1 as f64 - pose.y);

            //rotate into local error
            let local_vector = global_vector.rotate(pose.heading * -1.0);
            //heading is seperate because we cannot represent this via the local vector
            let error_heading = angle_difference(pose.heading, Angle::from_radians(point.p.2 as f64));
            
            //holds the error as a pose; robot_pose + error_pose = desired_pose
            let error_pose = Pose::new(local_vector.x, local_vector.y, error_heading);
            
            //correction equations
            let k = 2.0 * zeta * (av.powi(2) + beta * lv.powi(2)).sqrt();
            let e3 = error_pose.heading.as_radians();

            let corrected_lv = lv * error_pose.heading.cos() + k * error_pose.x;
            let corrected_av = av + k * e3 + beta * lv * sinc(e3) * error_pose.y;

            // Differential-drive kinematics: convert body-frame linear/angular
            // velocity (and acceleration) into per-wheel values. av * track_width/2
            let mut left_velocity = dt.to_pct_speed(corrected_lv - corrected_av * dt.track_width / 2.0);
            let mut right_velocity = dt.to_pct_speed(corrected_lv + corrected_av * dt.track_width / 2.0);

            // to_pct_speed is a linear scaling, so it maps in/s^2 -> %/s the same
            // way it maps in/s -> %.
            let left_accel = dt.to_pct_speed(la - aa * dt.track_width / 2.0);
            let right_accel = dt.to_pct_speed(la + aa * dt.track_width / 2.0);

            (left_velocity, right_velocity) = dt.desaturate(left_velocity, right_velocity);
            let (left_accel, right_accel) = dt.desaturate(left_accel, right_accel);

            dt.left_side.spin(left_velocity, left_accel);
            dt.right_side.spin(right_velocity, right_accel);

            telemetry!(ct_e, time, error_pose.y);
            telemetry!(theta_e, time, error_pose.heading.as_degrees());

            if profile.has_ended() {
                break;
            }

            drop(dt);
            drop(pose);

            sleep(SLEEP_INTERVAL).await;
        }
        let mut dt = self.drivetrain.try_lock().expect("Dt lock failed while driving");
        dt.stop();
        drop(dt);
    }
}








// testing time mapping
#[allow(dead_code)]
pub async fn test_time_mapping() {
    let mut output = std::fs::File::create("profile_testing_file.txt").unwrap();
    let profile_string = read_to_string("profile1.json").unwrap();
    let mut  motion_profile: MotionProfile = serde_json::from_str(&profile_string).unwrap();
    let own_timer = Instant::now();
    motion_profile.start();

    loop {
        writeln!(output, "curr: {}, point_time: {}", own_timer.elapsed().as_millis() as i32, motion_profile.get_point().t).ok();
        if motion_profile.has_ended() { break; }
        sleep(SLEEP_INTERVAL).await;
    }
}

#[allow(dead_code)]
pub async fn test_velocity_grepping() {
    let mut output = std::fs::File::create("profile_testing_file.txt").unwrap();
    let profile_string = read_to_string("vcTuning1.json").unwrap();
    let motion_profile: MotionProfile = serde_json::from_str(&profile_string).unwrap();

    writeln!(output, "timestamp,velocity").ok();
    for i in 0..(motion_profile.points.len() - 1) {
        writeln!(output, "{}, {}", motion_profile.points[i].t, motion_profile.points[i].lv).expect("RAHH");
    }
}


