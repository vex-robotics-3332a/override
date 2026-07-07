use vexide::prelude::*;
use std::{time::Instant, fs::*};
use std::io::{Result, Write};
use serde::Deserialize;
use crate::subsystems::drivetrain::Drivetrain;
use crate::SLEEP_INTERVAL;

#[derive(Deserialize)]
struct MotionProfile {
    pub points: Vec<Point>,

    #[serde(skip)]
    pub time: Option<Instant>,

    #[serde(skip)]
    pub current_index: usize,

    #[serde(skip)]
    pub total_time: i32
}

#[derive(Deserialize)]
struct Point {
    pub p: (f32, f32, f32),
    pub lv: f32,
    pub av: f32,
    pub t: i32
}

impl MotionProfile {
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



// testing time mapping
pub async fn test() {
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

