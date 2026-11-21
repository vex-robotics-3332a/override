use std::time::Duration;

use crate::SLEEP_INTERVAL;
use vexide::prelude::*;
use crate::chassis::Chassis;
use crate::subsystems::lift::Lift;
use crate::subsystems::toggles::Toggle;
use crate::localization::dsr::Dsrs;
use crate::auto_selector::AutoSelector;
use vexide::controller::ControllerState;
pub struct Robot {
    pub chassis: Chassis,
    pub controller: Controller,
    pub lift: Lift,
    pub dsrs: Dsrs,
    pub auto_selector: AutoSelector,
    pub toggle: Toggle,
}

impl Robot {
    pub fn new(chassis: Chassis, controller: Controller, lift: Lift, dsrs: Dsrs, auto_selector: AutoSelector, toggle: Toggle) -> Self {
        Self { chassis, controller, lift, dsrs, auto_selector, toggle }
    }

    pub async fn op_control(&mut self) {
        loop {
            let controller_state = self.controller.state().unwrap_or_default();
            if controller_state.button_down.is_now_pressed() {
                let routine = self.auto_selector.selected();
                let name = routine.name;
                let run = routine.run;
                println!("Autonomous: {}", name);
                run(self).await;
            }
            self.chassis.op_control_tick(&controller_state);
            self.binding(&controller_state);
            self.auto_selector.tick(&mut self.controller, &controller_state).await;
            self.toggle.alliance = self.auto_selector.alliance();
            sleep(SLEEP_INTERVAL).await;
        }
    }

    pub async fn auto_select_loop(&mut self) {
        loop {
            let controller_state = self.controller.state().unwrap_or_default();
            self.auto_selector.tick(&mut self.controller, &controller_state).await;
            self.toggle.alliance = self.auto_selector.alliance();
            sleep(SLEEP_INTERVAL).await;
        }
    }

    pub fn binding(&mut self, controller_state: &ControllerState) {
        let macro_running = self.lift.macro_running();

        // Manual lift control is disabled while a macro holds the subsystems (avoids wrist_stop fighting PID).
        if !macro_running {
            //cascade lift
            if controller_state.button_l1.is_pressed() {
                self.lift.cascade_extend();
            }

            if controller_state.button_l2.is_pressed() {
                self.lift.cascade_retract();
            }

            if !controller_state.button_l1.is_pressed() && !controller_state.button_l2.is_pressed() {
                self.lift.cascade_stop();
            }

            if controller_state.button_r2.is_now_pressed() {
                self.lift.claw_toggle();
            }

        //     //wrist
        //     if controller_state.button_down.is_now_pressed() {
        //         self.lift.wrist_down();
        //     }

        //     if controller_state.button_up.is_now_pressed() {
        //         self.lift.wrist_up();
        //     }

        //     if !controller_state.button_down.is_pressed() && !controller_state.button_up.is_pressed() {
        //         self.lift.wrist_stop();
        //     }

        //     //end affector
        //     if controller_state.button_y.is_now_pressed() {
        //         self.lift.end_affector_stop();
        //     }

        // }

        // // macros
        // if controller_state.button_x.is_now_pressed() {
        //     self.lift.score_position();
        // }

        // if controller_state.button_r2.is_now_pressed() {
        //     self.lift.intake_position();
        // }

        // if controller_state.button_a.is_now_pressed() {
        //     self.lift.drop_stack();
        }

        if controller_state.button_b.is_now_pressed() {
            self.lift.drop();
        }

        if controller_state.button_r1.is_pressed() {
            self.toggle.auto_toggle();
        }
        else {
            self.toggle.off();
        }

        if controller_state.button_a.is_now_pressed() {
            self.lift.matchload();
        }
    }
}

impl Compete for Robot {
    async fn disabled(&mut self) {
        self.auto_select_loop().await;
    }

    async fn autonomous(&mut self) {
        let routine = self.auto_selector.selected();
        println!("Autonomous: {}", routine.name);
        (routine.run)(self).await;
    }
    async fn driver(&mut self) {
        self.op_control().await;
    }
}