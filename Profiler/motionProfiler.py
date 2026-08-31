from math import atan2, hypot, floor, log, log10, pi, sin, cos
import json
import os

from settings import get_robot_settings


def dist2d(a, b):
    return hypot(a[0] - b[0], a[1] - b[1])


def wrap_angle(a):
    #normalize an angle to the range [-pi, pi]
    return atan2(sin(a), cos(a))



class Robot:
    def __init__(self, max_speed, max_accel, track_width, accel_curvature_gain: float = 1.0):
        self.max_speed = max_speed  # in/s — free speed / first-order asymptote
        self.track_width = track_width  # in
        self.max_acceleration = max_accel  # in/s^2 — stall accel a0
        self.accel_curvature_gain = float(accel_curvature_gain)

    @property
    def tau(self) -> float:
        """First-order time constant τ = v_max / a0 (seconds)."""
        a0 = max(float(self.max_acceleration), 1e-9)
        return float(self.max_speed) / a0


def robot_from_settings() -> Robot:
    """Build a Robot from Profiler/settings.json."""
    specs = get_robot_settings()
    return Robot(
        specs["max_speed"],
        specs["max_accel"],
        specs["track_width"],
        specs.get("accel_curvature_gain", 1.0),
    )


class CubicBezier:
    def __init__(self, points: list[tuple[float, float]]):
        self.points = [tuple(float(c) for c in p) for p in points]

    def point_at_t(self, t):
        p0, p1, p2, p3 = self.points
        mt = 1 - t
        b0, b1, b2, b3 = mt**3, 3 * mt**2 * t, 3 * mt * t**2, t**3
        return (
            b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0],
            b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1],
        )

    def derivative_at_t(self, t):
        p0, p1, p2, p3 = self.points
        mt = 1 - t
        c0, c1, c2 = 3 * mt**2, 6 * mt * t, 3 * t**2
        return (
            c0 * (p1[0] - p0[0]) + c1 * (p2[0] - p1[0]) + c2 * (p3[0] - p2[0]),
            c0 * (p1[1] - p0[1]) + c1 * (p2[1] - p1[1]) + c2 * (p3[1] - p2[1]),
        )

    def second_derivative_at_t(self, t):
        p0, p1, p2, p3 = self.points
        mt = 1 - t
        a, b = 6 * mt, 6 * t
        return (
            a * (p2[0] - 2 * p1[0] + p0[0]) + b * (p3[0] - 2 * p2[0] + p1[0]),
            a * (p2[1] - 2 * p1[1] + p0[1]) + b * (p3[1] - 2 * p2[1] + p1[1]),
        )

    def tangent_angle_at_t(self, t):
        d = self.derivative_at_t(t)
        return atan2(d[1], d[0])

    def curvature_at_t(self, t):
        d = self.derivative_at_t(t)
        dd = self.second_derivative_at_t(t)
        denom = (d[0] ** 2 + d[1] ** 2) ** 1.5
        if denom == 0:
            return 0.0
        return (d[0] * dd[1] - d[1] * dd[0]) / denom
    
    def length(self):
        total_length = 0
        sample_size = 1000
        for i in range(sample_size):
            t = i/sample_size
            point = self.point_at_t(t)
            next_point = self.point_at_t(t + 1 / sample_size)
            total_length += dist2d(point, next_point)
        return total_length

class MotionProfile:
    def __init__(
        self,
        robot: Robot,
        name: str,
        curves: list[CubicBezier],
        resolution: float = 0.1,
        max_speed: float | list[float] = 100,
        reverse: bool = False,
    ):
        self.robot = robot
        self.points = []
        self.profile = []
        self.curves = curves
        self.resolution = resolution
        self.name = name
        self.total_length = sum(curve.length() for curve in curves)
        if isinstance(max_speed, (list, tuple)):
            if len(max_speed) != len(curves):
                raise ValueError("max_speed list must match number of curves")
            self.max_speeds = [float(s) for s in max_speed]
        else:
            self.max_speeds = [float(max_speed)] * max(len(curves), 1)
        # Legacy single-value accessors use the most permissive curve speed.
        self.max_speed = max(self.max_speeds) if self.max_speeds else float(max_speed)
        self.reverse = reverse
    
    def get_max_angular(self, curvature):
        return self.robot.max_speed/(abs(curvature) * self.robot.track_width/2 + 1)

    def accel_cap(self, curvature: float) -> float:
        """Stall accel a0 scaled by curvature: a0 / (1 + gain*|κ|*track/2)."""
        gain = max(0.0, float(getattr(self.robot, "accel_curvature_gain", 0.0)))
        return self.robot.max_acceleration / (
            1.0 + gain * abs(curvature) * self.robot.track_width / 2.0
        )

    def tau_for_accel(self, a0: float) -> float:
        """τ = v_max / a0 for the local (curvature-scaled) stall accel."""
        return self.robot.max_speed / max(float(a0), 1e-9)

    def _speed_cap(self, max_speed_pct: float) -> float:
        return self.robot.max_speed * max_speed_pct / 100

    def _accel_distance(self, v0: float, v1: float, a0: float) -> float:
        """Path length to accelerate v0→v1 under a = a0·(1 − v/v_max), v1 ≥ v0."""
        V = float(self.robot.max_speed)
        if v1 <= v0 or V <= 1e-12:
            return 0.0
        if a0 <= 1e-12:
            return float("inf")
        tau = self.tau_for_accel(a0)
        eps = 1e-9 * V
        v0c = min(max(v0, 0.0), V - eps)
        v1c = min(max(v1, 0.0), V - eps)
        if v1c <= v0c:
            return 0.0
        return tau * (V * log((V - v0c) / (V - v1c)) - (v1c - v0c))

    def _decel_distance(self, v0: float, v1: float, a0: float) -> float:
        """Path length to brake v0→v1 under a = −a0·(1 + v/v_max), v1 ≤ v0."""
        V = float(self.robot.max_speed)
        if v0 <= v1 or V <= 1e-12:
            return 0.0
        if a0 <= 1e-12:
            return float("inf")
        tau = self.tau_for_accel(a0)
        v0c = max(v0, 0.0)
        v1c = max(v1, 0.0)
        if v0c <= v1c:
            return 0.0
        # Δs = τ[(v0−v1) − V ln((V+v0)/(V+v1))]
        return tau * ((v0c - v1c) - V * log((V + v0c) / (V + v1c)))

    def max_speed_after_distance(self, v0: float, ds: float, a0: float) -> float:
        """Max speed reachable after traveling ds while accelerating (first-order)."""
        V = float(self.robot.max_speed)
        v0 = min(max(float(v0), 0.0), V)
        if ds <= 1e-12 or a0 <= 1e-12:
            return v0
        v_hi = V * (1.0 - 1e-9)
        if self._accel_distance(v0, v_hi, a0) <= ds:
            return V
        lo, hi = v0, v_hi
        for _ in range(48):
            mid = 0.5 * (lo + hi)
            if self._accel_distance(v0, mid, a0) <= ds:
                lo = mid
            else:
                hi = mid
        return lo

    def max_speed_before_distance(self, v1: float, ds: float, a0: float) -> float:
        """Max speed allowed ds before reaching v1 while braking (first-order reverse)."""
        V = float(self.robot.max_speed)
        v1 = min(max(float(v1), 0.0), V)
        if ds <= 1e-12 or a0 <= 1e-12:
            return v1
        # As ds→∞, approachable from arbitrary high speed; cap search at a generous bound.
        v_hi = max(V * 4.0, v1 + 1.0)
        if self._decel_distance(v_hi, v1, a0) <= ds:
            # Still not enough braking distance from v_hi — allow up to v_hi
            # (speed caps will clip). Binary-search upward until distance fits.
            while self._decel_distance(v_hi, v1, a0) <= ds and v_hi < V * 20:
                v_hi *= 1.5
        lo, hi = v1, v_hi
        for _ in range(48):
            mid = 0.5 * (lo + hi)
            if self._decel_distance(mid, v1, a0) <= ds:
                lo = mid
            else:
                hi = mid
        return lo

    def _travel_time(self, v0: float, v1: float, ds: float, a0: float = 0.0) -> float:
        """Seconds to traverse path length ds at the profiled endpoint speeds.

        Always uses path length / average speed. Closed-form accel/brake times are
        wrong here: when curvature (not dynamics) changes speed, Δv is small but
        Δs is still ~resolution, and brake formulas collapse dt→0 so many samples
        share one timestamp (playback teleports / jumps).
        """
        if ds <= 1e-12:
            return 0.0
        v_avg = 0.5 * (abs(v0) + abs(v1))
        if v_avg < 1e-9:
            # Both near rest: fall back to stall accel so the first step isn't infinite.
            a = max(float(a0), float(self.robot.max_acceleration), 1e-9)
            # s ≈ 0.5 a t^2  →  t ≈ sqrt(2s/a)
            return (2.0 * ds / a) ** 0.5
        return ds / v_avg

    def generate_sample_list(self):
        if len(self.profile) > 0: raise Exception("Sample list has already been generated!")
        if not self.curves:
            return self.points

        resolution_over_sample = 2

        oversample_resolution = self.resolution / resolution_over_sample
        oversample_points = []
        for curve_index, curve in enumerate(self.curves):
            length = curve.length()
            if length <= 1e-9:
                point = curve.point_at_t(0)
                oversample_points.append({
                    "point": (point[0], point[1], curve.tangent_angle_at_t(0)),
                    "curvature": curve.curvature_at_t(0),
                    "max_speed": self.max_speeds[curve_index],
                })
                continue
            t_step = oversample_resolution / length
            t = 0.0
            while t < 1:
                point = curve.point_at_t(t)
                curvature = curve.curvature_at_t(t)
                oversample_points.append({
                    "point": (point[0], point[1], curve.tangent_angle_at_t(t)),
                    "curvature": curvature,
                    "max_speed": self.max_speeds[curve_index],
                })
                t += t_step
        
        #removal of oversampled points
        last_kept_point = oversample_points[0]

        for point_index in range(len(oversample_points)):
            point = oversample_points[point_index]["point"]
            if dist2d(point, last_kept_point["point"]) > self.resolution:
                self.points.append(last_kept_point)
                last_kept_point = oversample_points[point_index]
        self.points.append(last_kept_point)

        #add the end point
        end_curve = self.curves[-1]
        end_point = end_curve.point_at_t(1)
        self.points.append({
            "point": (end_point[0], end_point[1], end_curve.tangent_angle_at_t(1)),
            "curvature": end_curve.curvature_at_t(1),
            "max_speed": self.max_speeds[-1],
        })

        return self.points

    def build_profile(self):
        """Generate the velocity profile in memory without writing a file."""
        self.points = []
        self.profile = []
        self.generate_sample_list()

        for i, point in enumerate(self.points):
            speed_pct = point.get("max_speed", self.max_speed)
            self.profile.append({
                "p": point["point"],
                "lv": self._speed_cap(speed_pct),
                "av": self.points[i]["curvature"] * self.robot.max_speed,
                "t": 0,
            })

        previous_speed = 0.0
        for i, point in enumerate(self.profile):
            curvature = self.points[i]["curvature"]
            speed_pct = self.points[i].get("max_speed", self.max_speed)
            max_forward_velocity_from_curve = self.get_max_angular(curvature)
            distance_to_previous_point = (
                0.0 if i == 0 else dist2d(point["p"], self.profile[i - 1]["p"])
            )
            if i == 0:
                step_curvature = abs(curvature)
            else:
                step_curvature = max(abs(curvature), abs(self.points[i - 1]["curvature"]))
            a0 = self.accel_cap(step_curvature)
            max_forward_velocity_from_previous_point = self.max_speed_after_distance(
                previous_speed, distance_to_previous_point, a0
            )
            point["lv"] = min(
                max_forward_velocity_from_curve,
                max_forward_velocity_from_previous_point,
                self._speed_cap(speed_pct),
            )
            previous_speed = point["lv"]
            point["av"] = curvature * point["lv"]

        previous_speed = 0.0
        next_point = None
        for i in range(len(self.profile) - 1, -1, -1):
            point = self.profile[i]
            speed_pct = self.points[i].get("max_speed", self.max_speed)
            distance_to_next_point = (
                0.0 if next_point is None else dist2d(point["p"], next_point["p"])
            )
            if next_point is None:
                step_curvature = abs(self.points[i]["curvature"])
            else:
                step_curvature = max(
                    abs(self.points[i]["curvature"]),
                    abs(self.points[i + 1]["curvature"]),
                )
            a0 = self.accel_cap(step_curvature)
            max_backward_velocity_from_next_point = self.max_speed_before_distance(
                previous_speed, distance_to_next_point, a0
            )
            point["lv"] = min(
                max_backward_velocity_from_next_point,
                point["lv"],
                self._speed_cap(speed_pct),
            )
            previous_speed = point["lv"]
            point["av"] = self.points[i]["curvature"] * point["lv"]
            next_point = point

        t_ms = 0.0
        for i, point in enumerate(self.profile):
            if i == 0:
                point["t"] = 0.0
                continue
            distance_to_previous_point = dist2d(point["p"], self.profile[i - 1]["p"])
            step_curvature = max(
                abs(self.points[i]["curvature"]),
                abs(self.points[i - 1]["curvature"]),
            )
            a0 = self.accel_cap(step_curvature)
            dt = self._travel_time(
                abs(self.profile[i - 1]["lv"]),
                abs(point["lv"]),
                distance_to_previous_point,
                a0,
            )
            t_ms += 1000.0 * dt
            point["t"] = t_ms

        if self.reverse:
            for point in self.profile:
                x, y, theta = point["p"]
                point["p"] = (x, y, wrap_angle(theta + pi))
                point["lv"] = -point["lv"]

        for point in self.profile:
            point["p"] = tuple(round(c, 2) for c in point["p"])
            point["lv"] = round(point["lv"], 2)
            point["av"] = round(point["av"], 2)
            point["t"] = round(point["t"])

        return self.profile

    @property
    def duration_ms(self) -> float:
        return float(self.profile[-1]["t"]) if self.profile else 0.0
    
    def generate_profile(self, output_path):
        self.build_profile()

        output_dir = os.path.expanduser(output_path)
        os.makedirs(output_dir, exist_ok=True)
        out_file = os.path.join(output_dir, self.name + ".json")

        if os.path.isfile(out_file):
            print(f"Profile {self.name}.json already exists! Are you sure you want to overwrite it? (y/n)")
            response = input()
            if response != "y":
                print("Profile creation canceled!")
                return

        with open(out_file, "w") as output_file:
            json.dump({"points": self.profile}, output_file)


DEFAULT_RESOLUTION = 0.5


def compute_profile(
    points: list[tuple[float, float]],
    max_speed: float = 100.0,
    reverse: bool = False,
    robot: Robot | None = None,
    resolution: float = DEFAULT_RESOLUTION,
    name: str = "motion",
) -> tuple[list[dict], float]:
    """Profile a single cubic bezier. Prefer compute_motion_profile for multi-curve paths."""
    return compute_motion_profile(
        [points],
        max_speeds=[max_speed],
        reverse=reverse,
        robot=robot,
        resolution=resolution,
        name=name,
    )


def compute_motion_profile(
    curves: list[list[tuple[float, float]]],
    max_speeds: list[float] | None = None,
    reverse: bool = False,
    robot: Robot | None = None,
    resolution: float = DEFAULT_RESOLUTION,
    name: str = "motion",
) -> tuple[list[dict], float]:
    """Profile one or more cubic beziers as a single continuous trajectory."""
    if not curves:
        return [], 0.0
    robot = robot or robot_from_settings()
    if max_speeds is None:
        max_speeds = [100.0] * len(curves)
    motion_profile = MotionProfile(
        robot,
        name,
        [CubicBezier(points) for points in curves],
        resolution,
        max_speeds,
        reverse,
    )
    profile = motion_profile.build_profile()
    return profile, motion_profile.duration_ms


if __name__ == "__main__":
    motionProfile = MotionProfile(
        robot_from_settings(),
        "profile1",
        [CubicBezier([(0, 0), (48, 0), (48, 48), (96, 48)])],
        DEFAULT_RESOLUTION,
        100,
    )
    motionProfile.generate_profile("~/code/vex/vid-stuffs/profiles")

    # prev_t = motionProfile.profile[0]["t"]
    # diffs = []

    # for point_i in range(1, len(motionProfile.profile)):
    #     point_time = motionProfile.profile[point_i]["t"]
    #     print(point_time)
    #     diffs.append(point_time - prev_t)
    #     prev_t = point_time

    # print(sum(diffs)/len(diffs))


