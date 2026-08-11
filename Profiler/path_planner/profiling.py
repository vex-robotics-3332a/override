import sys
from pathlib import Path

from motion import Curve, Motion
from robot import get_robot_specs

_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from motionProfiler import compute_motion_profile


def profile_motion(motion: Motion, name: str = "motion") -> float:
    """Build one continuous profile across all curves in the motion."""
    if not motion.curves:
        motion.profile = []
        motion.time = 0.0
        return 0.0

    curves = [curve.points for curve in motion.curves]
    max_speeds = [curve.max_speed for curve in motion.curves]
    # Whole-motion reverse only when every curve is reversed.
    reverse = all(curve.reversed for curve in motion.curves)

    profile, duration_ms = compute_motion_profile(
        curves,
        max_speeds=max_speeds,
        reverse=reverse,
        name=name,
        robot=get_robot_specs().to_profiler_robot(),
    )
    motion.profile = profile
    motion.time = duration_ms

    # Curves no longer own independent stop-start profiles.
    for curve in motion.curves:
        curve.profile = None
        curve.time = 0.0

    return duration_ms
