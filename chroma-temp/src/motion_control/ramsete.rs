use vexide::prelude::*;
use std::{time::Instant, fs::*};
use std::io::{Result, Write};
use serde::Deserialize;
use crate::subsystems::drivetrain::Drivetrain;
use crate::chassis::Chassis;
use crate::SLEEP_INTERVAL;

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

#[derive(Deserialize)]
pub struct Point {
    pub p: (f32, f32, f32),
    pub lv: f32,
    pub av: f32,
    pub t: i32
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
    
    pub fn get_point(&mut self) -> &Point {
        let elapsed = self.time.unwrap().elapsed().as_millis() as i32;
        let mut previous_time_diff = elapsed - self.points[self.current_index].t;

        for i in self.current_index..(self.points.len() - 1) {
            let time_diff = (elapsed - self.points[i].t).abs();
            
            if time_diff > previous_time_diff {
                self.current_index = i;
                return &self.points[i];
            }

            previous_time_diff = time_diff;
        }
        return &self.points[self.points.len() - 1];
    }

    pub fn has_ended(&self) -> bool {
        let elapsed = self.time.unwrap().elapsed().as_millis() as i32;
        elapsed > self.total_time
    }
}

impl Chassis {
    pub async fn drive_along(&mut self, profile: &mut MotionProfile) {
        // running fully raw (no correction) to see if the profile is roughly correct
        // TODO: ADD DESATURATION
        let clock = Instant::now();
        let mut desired_v_file = File::create("desired_velocity.txt").expect("put yo sd card in bro");
        let mut actual_v_file = File::create("actual_velocity.txt").expect("put yo sd card in bro");;
        writeln!(desired_v_file, "timestamp,desired velocity").expect("wtf");
        writeln!(actual_v_file, "timestamp,actual velocity").expect("wtf");

        profile.start();
        loop {
            let time = clock.elapsed().as_millis();
            let mut dt = self.drivetrain.try_lock().expect("Dt lock failed while driving");
            let point = profile.get_point();

            let lv = point.lv as f64;
            //testing straight line (velo controller tests)
            // let av = point.av as f64;
            
            // let left_velocity = dt.to_pct_speed(lv - av * dt.track_width / 2.0);
            // let right_velocity = dt.to_pct_speed(lv + av * dt.track_width / 2.0);
            let left_velocity = dt.to_pct_speed(lv);
            let right_velocity = dt.to_pct_speed(lv);

            let (left_velocity, right_velocity) = dt.desaturate(left_velocity, right_velocity);

            dt.left_side.spin(left_velocity);
            dt.right_side.spin(right_velocity);

            writeln!(desired_v_file, "{},{}", time, dt.to_pct_speed(lv)).expect("wtf");
            writeln!(actual_v_file, "{},{}", time, (dt.left_side.velocity + dt.right_side.velocity) / 2.0).expect("wtf");

            if profile.has_ended() {
                break;
            }

            drop(dt);

            desired_v_file.flush().ok();
            actual_v_file.flush().ok();

            sleep(SLEEP_INTERVAL).await;
        }
        let mut dt = self.drivetrain.try_lock().expect("Dt lock failed while driving");
        dt.stop();
        drop(dt);
    }
}











// testing time mapping
pub async fn test_time_mapping() {
    let mut output = File::create("profile_testing_file.txt").unwrap();
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

pub async fn test_velocity_grepping() {
    let mut output = File::create("profile_testing_file.txt").unwrap();
    let profile_string = read_to_string("vcTuning1.json").unwrap();
    let motion_profile: MotionProfile = serde_json::from_str(&profile_string).unwrap();

    writeln!(output, "timestamp,velocity").ok();
    for i in 0..(motion_profile.points.len() - 1) {
        writeln!(output, "{}, {}", motion_profile.points[i].t, motion_profile.points[i].lv).expect("RAHH");
    }
}


