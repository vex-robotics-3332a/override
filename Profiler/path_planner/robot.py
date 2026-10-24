"""Shared robot physical / profiling specs for the path planner."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from motionProfiler import Robot
from settings import get_robot_settings, save_robot_settings


@dataclass
class RobotSpecs:
    max_speed: float
    max_accel: float
    track_width: float
    length: float  # body length along heading (X), inches
    width: float  # body width (Y), inches
    accel_curvature_gain: float = 1.0
    # Turning center offset from the body center: +X forward, +Y left, inches.
    turn_offset_x: float = 0.0
    turn_offset_y: float = 0.0

    @classmethod
    def from_settings(cls) -> "RobotSpecs":
        data = get_robot_settings()
        return cls(
            max_speed=float(data["max_speed"]),
            max_accel=float(data["max_accel"]),
            track_width=float(data["track_width"]),
            length=float(data["length"]),
            width=float(data["width"]),
            accel_curvature_gain=float(data.get("accel_curvature_gain", 1.0)),
            turn_offset_x=float(data.get("turn_offset_x", 0.0)),
            turn_offset_y=float(data.get("turn_offset_y", 0.0)),
        )

    def copy(self) -> "RobotSpecs":
        return RobotSpecs(
            max_speed=self.max_speed,
            max_accel=self.max_accel,
            track_width=self.track_width,
            length=self.length,
            width=self.width,
            accel_curvature_gain=self.accel_curvature_gain,
            turn_offset_x=self.turn_offset_x,
            turn_offset_y=self.turn_offset_y,
        )

    def to_dict(self) -> dict:
        return {
            "max_speed": self.max_speed,
            "max_accel": self.max_accel,
            "track_width": self.track_width,
            "length": self.length,
            "width": self.width,
            "accel_curvature_gain": self.accel_curvature_gain,
            "turn_offset_x": self.turn_offset_x,
            "turn_offset_y": self.turn_offset_y,
        }

    def to_profiler_robot(self) -> Robot:
        return Robot(
            self.max_speed,
            self.max_accel,
            self.track_width,
            self.accel_curvature_gain,
        )


# Applied specs used by profiling and canvas rendering.
_active_specs = RobotSpecs.from_settings()


def get_robot_specs() -> RobotSpecs:
    return _active_specs


def set_robot_specs(specs: RobotSpecs, *, persist: bool = True) -> None:
    global _active_specs
    _active_specs = specs.copy()
    if persist:
        save_robot_settings(_active_specs.to_dict())


def reload_robot_specs() -> RobotSpecs:
    global _active_specs
    _active_specs = RobotSpecs.from_settings()
    return _active_specs
