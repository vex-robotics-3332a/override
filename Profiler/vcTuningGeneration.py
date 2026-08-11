# Generates a profile which can be used to tune robot's velocity controller
# Uses the same format as normal profiles as to be able to be parsed into
# the robot but only has timestamp and linear velocity values

from dataclasses import dataclass
from math import floor, log10
import json
import os


def round_sig(x, sig=2):
    if x == 0:
        return 0
    return round(x, -int(floor(log10(abs(x)))) + (sig - 1))


@dataclass
class Point:
    x: float
    y: float


@dataclass
class CubicBezier:
    """A cubic Bezier defined by 4 control points.

    For a speed curve, x = time (seconds) and y = fraction of max speed (0-1).
    The endpoints p0 and p3 are the start/end of the curve; p1 and p2 shape it.

    x(t) must be monotonically increasing for y_at_x() to work, which holds
    as long as p0.x <= p1.x <= p2.x <= p3.x.
    """

    p0: Point
    p1: Point
    p2: Point
    p3: Point

    def _eval(self, a, b, c, d, t):
        # De Casteljau / expanded cubic Bernstein form for one axis.
        mt = 1.0 - t
        return (mt * mt * mt * a
                + 3 * mt * mt * t * b
                + 3 * mt * t * t * c
                + t * t * t * d)

    def x_of_t(self, t):
        return self._eval(self.p0.x, self.p1.x, self.p2.x, self.p3.x, t)

    def y_of_t(self, t):
        return self._eval(self.p0.y, self.p1.y, self.p2.y, self.p3.y, t)

    def t_for_x(self, x, tol=1e-7, max_iter=60):
        """Find the parameter t such that x_of_t(t) ~= x via bisection.

        Relies on x being monotonically increasing in t.
        """
        # Clamp to the curve's x-domain.
        if x <= self.p0.x:
            return 0.0
        if x >= self.p3.x:
            return 1.0

        lo, hi = 0.0, 1.0
        for _ in range(max_iter):
            mid = 0.5 * (lo + hi)
            xm = self.x_of_t(mid)
            if abs(xm - x) < tol:
                return mid
            if xm < x:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def y_at_x(self, x):
        """Approximate y (speed fraction) for a given x (time)."""
        return self.y_of_t(self.t_for_x(x))


class VelocityTuningProfile:
    """Builds a profile from a Bezier speed curve, matching the motionProfiler
    JSON format so it can be parsed by the robot.

    Only the timestamp (t, in ms) and linear velocity (lv, as a 0-100 percentage
    of max speed) carry meaning;
    the position (p) and angular velocity (av) fields are zero-filled to keep
    the schema identical to a normal motion profile.
    """

    def __init__(self, name, curve: CubicBezier, resolution=0.01):
        self.name = name
        self.curve = curve
        self.resolution = resolution  # seconds between samples
        self.profile = []

    def generate_sample_list(self):
        if len(self.profile) > 0:
            raise Exception("Sample list has already been generated!")

        t_start = self.curve.p0.x
        t_end = self.curve.p3.x
        steps = int(round((t_end - t_start) / self.resolution))

        for i in range(steps + 1):
            time_s = min(t_start + i * self.resolution, t_end)
            fraction = self.curve.y_at_x(time_s)
            self.profile.append({
                "p": (0.0, 0.0, 0.0),
                "lv": fraction * 100.0,  # percentage of max speed (0-100)
                "av": 0.0,
                "t": int(time_s * 1000),  # ms, matching motionProfiler
            })

        return self.profile

    def generate_profile(self, output_path):
        self.generate_sample_list()

        # round to significant figures, matching motionProfiler output.
        # t is left unrounded (already an int ms) so 10ms resolution is preserved;
        # round_sig would collapse it to 100ms steps once t >= 1000.
        for point in self.profile:
            point["p"] = tuple(round_sig(c) for c in point["p"])
            point["lv"] = round_sig(point["lv"])
            point["av"] = round_sig(point["av"])

        file_path = output_path + "/" + self.name + ".json"
        if os.path.isfile(file_path):
            print(f"Profile {self.name}.json already exists! Are you sure you want to overwrite it? (y/n)")
            response = input()
            if response != "y":
                print("Profile creation canceled!")
                return

        with open(file_path, "w") as output_file:
            json.dump({"points": self.profile}, output_file)


if __name__ == "__main__":
    # Example: ramp from 0% at t=0s to 100% at t=2s with an ease-in/out shape.
    curve = CubicBezier(
        p0=Point(0.0, 0.0),
        p1=Point(1, 0.0),
        p2=Point(1.0, 1.0),
        p3=Point(2.0, 1.0),
    )

    profile = VelocityTuningProfile("vcTuning1", curve, resolution=0.01)
    profile.generate_profile("/Volumes/ROBOT_SD")
