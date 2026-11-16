use std::{f64::consts::PI};

use vexide::{math::Angle, prelude::*};
use crate::helpers::*;

pub trait Encoder {
    fn get_travel(&self) -> f64;
    fn reset_travel(&mut self);
    fn get_offset(&self) -> f64;
}

pub struct TrackingPod {
    pub rotation: RotationSensor,
    pub diameter: f64,
    /// Linear pods: distance to the right of the tracking center. Horizontal pods: distance forward of it.
    pub offset: f64,
    pub reversed: bool,
    // why would you use this???
    pub ratio: f64,

    previous_travel: f64
}

impl TrackingPod {
    pub fn new(
        rotation: RotationSensor, 
        diameter: f64,
        offset: f64,
        reversed: bool,
        ratio: f64
    ) -> Self {
        Self {
            rotation,
            diameter,
            offset,
            reversed,
            ratio,
            previous_travel: 0.0
        }
    }
}

impl Encoder for TrackingPod {
    fn get_travel(&self) -> f64 {
        let position = self.rotation.position().unwrap_or_default().as_turns();
        let travel = position * 2.0 * PI * self.diameter * 0.5 * self.ratio;
        if self.reversed { -travel } else { travel }
    }

    fn reset_travel(&mut self) {
        self.rotation.reset_position().ok();
    }

    fn get_offset(&self) -> f64 {
        self.offset
    }
}

pub struct NullPod {

}

impl NullPod {
    pub fn new() -> Self {
        Self {}
    }
}

impl Encoder for NullPod {
    fn get_travel(&self) -> f64 {
        0.0
    }

    fn reset_travel(&mut self) {
        
    }

    fn get_offset(&self) -> f64 {
        0.0
    }
}

//boxes cuz generics are ugly
pub struct Odometry {
    pub linear_encoder: Box<dyn Encoder>,
    pub horizontal_encoder: Box<dyn Encoder>,

    previous_linear_travel: f64,
    previous_horizontal_travel: f64,
}

impl Odometry {
    pub fn new(
        // as per Daniel from 5069Y's suggestion you are a bum if you have
        // more than one linear tracker
        linear_encoder: Box<dyn Encoder>,
        horizontal_encoder: Box<dyn Encoder>,
    ) -> Self {
        Self {
            linear_encoder,
            horizontal_encoder,
            previous_horizontal_travel: 0.0,
            previous_linear_travel: 0.0,
        }
    }

    fn calculate_chord_length(encoder_offset: f64, encoder_gain: f64, heading_change: Angle) -> f64 {
        if heading_change.as_radians() == 0.0 {
            encoder_gain
        }
        else {
            let arc_radius = encoder_gain/heading_change.as_radians() - encoder_offset;
            2.0 * arc_radius * (heading_change.as_radians() / 2.0).sin()
        }
    }

    pub fn update(&mut self, start_theta: Angle, end_theta: Angle) -> CartesianVector {
        let linear_gain = self.linear_encoder.get_travel() - self.previous_linear_travel;
        let horizontal_gain = self.horizontal_encoder.get_travel() - self.previous_horizontal_travel;
        let heading_gain = angle_difference(end_theta, start_theta);
        let chord_angle = average_angle(start_theta, end_theta);

        self.previous_linear_travel += linear_gain;
        self.previous_horizontal_travel += horizontal_gain;

        let gain_vector = CartesianVector::new(
            Self::calculate_chord_length(self.linear_encoder.get_offset(), linear_gain, heading_gain),
            Self::calculate_chord_length(self.horizontal_encoder.get_offset(), horizontal_gain, heading_gain)
        );

        gain_vector.rotate(chord_angle)
    }
}