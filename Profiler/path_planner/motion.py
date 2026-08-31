from math import atan2, cos, degrees, hypot, radians, sin

FIELD_SIZE_IN = 144.0
_DEFAULT_HANDLE_IN = 6.0


def clamp_field_coord(value: float) -> float:
    return max(0.0, min(FIELD_SIZE_IN, value))


def clamp_field_point(x: float, y: float) -> tuple[float, float]:
    return (clamp_field_coord(x), clamp_field_coord(y))


def _unit(dx: float, dy: float) -> tuple[float, float]:
    length = hypot(dx, dy)
    if length < 1e-9:
        return (1.0, 0.0)
    return (dx / length, dy / length)


def _handle_length(origin, handle) -> float:
    return hypot(handle[0] - origin[0], handle[1] - origin[1])


def _heading_unit(heading_deg: float) -> tuple[float, float]:
    return _unit(cos(radians(heading_deg)), sin(radians(heading_deg)))


def apply_g1_from_pose(curve: "Curve", pose: tuple[float, float, float]) -> None:
    """Lock curve.P0 to pose and align P1 with pose heading (G1 from a prior pose).

    Reversed curves travel with opposite chassis heading, so their geometric
    entry tangent is opposite the pose heading.
    """
    x, y, heading_deg = pose
    join = clamp_field_point(x, y)
    curve.points[0] = join
    ux, uy = _heading_unit(heading_deg)
    if curve.reversed:
        ux, uy = -ux, -uy
    handle_len = _handle_length(join, curve.points[1])
    if handle_len < 1e-6:
        handle_len = _DEFAULT_HANDLE_IN
    curve.points[1] = clamp_field_point(join[0] + ux * handle_len, join[1] + uy * handle_len)


def apply_g1_forward(prev: "Curve", nxt: "Curve") -> None:
    """Keep prev fixed; set nxt.P0/P1 for G1 (and G0) at the join.

    When exactly one side is reversed, the geometric tangents must oppose so
    robot heading stays continuous across the junction.
    """
    join = prev.points[3]
    nxt.points[0] = clamp_field_point(*join)
    ux, uy = _unit(join[0] - prev.points[2][0], join[1] - prev.points[2][1])
    if prev.reversed != nxt.reversed:
        ux, uy = -ux, -uy
    handle_len = _handle_length(nxt.points[0], nxt.points[1])
    if handle_len < 1e-6:
        handle_len = _DEFAULT_HANDLE_IN
    nxt.points[1] = clamp_field_point(join[0] + ux * handle_len, join[1] + uy * handle_len)


def apply_g1_backward(prev: "Curve", nxt: "Curve") -> None:
    """Keep nxt fixed; set prev.P3/P2 for G1 (and G0) at the join.

    When exactly one side is reversed, the geometric tangents must oppose so
    robot heading stays continuous across the junction.
    """
    join = nxt.points[0]
    prev.points[3] = clamp_field_point(*join)
    ux, uy = _unit(nxt.points[1][0] - join[0], nxt.points[1][1] - join[1])
    if prev.reversed != nxt.reversed:
        ux, uy = -ux, -uy
    handle_len = _handle_length(prev.points[3], prev.points[2])
    if handle_len < 1e-6:
        handle_len = _DEFAULT_HANDLE_IN
    prev.points[2] = clamp_field_point(join[0] - ux * handle_len, join[1] - uy * handle_len)


class Curve:
    """A single cubic bezier segment within a motion."""

    NUM_POINTS = 4

    def __init__(self, points=None, max_speed: float = 100.0, reversed: bool = False):
        if points is None:
            points = [(0.0, 0.0)] * self.NUM_POINTS
        points = [clamp_field_point(float(p[0]), float(p[1])) for p in points]
        if len(points) != self.NUM_POINTS:
            raise ValueError(
                f"Curve requires exactly {self.NUM_POINTS} points, got {len(points)}"
            )
        self.points = points
        self.max_speed = max_speed
        self.reversed = reversed
        # Deprecated: profiles are stored on Motion for continuous multi-curve driving.
        self.profile: list[dict] | None = None
        self.time: float = 0.0
        self.key = str(id(self))

    def point_at_t(self, t: float) -> tuple[float, float]:
        p0, p1, p2, p3 = self.points
        mt = 1 - t
        b0, b1, b2, b3 = mt**3, 3 * mt**2 * t, 3 * mt * t**2, t**3
        return (
            b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0],
            b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1],
        )

    def sample(self, n: int = 40) -> list[tuple[float, float]]:
        return [self.point_at_t(i / n) for i in range(n + 1)]

    def start_point(self) -> tuple[float, float]:
        return self.points[0]

    def end_point(self) -> tuple[float, float]:
        return self.points[3]

    def geometric_end_heading(self) -> float:
        """Path-tangent heading at P3 (ignores reversed)."""
        p2, p3 = self.points[2], self.points[3]
        dx, dy = p3[0] - p2[0], p3[1] - p2[1]
        if dx == 0 and dy == 0:
            p0 = self.points[0]
            dx, dy = p3[0] - p0[0], p3[1] - p0[1]
        return atan2(dy, dx)

    def end_heading(self) -> float:
        """Robot heading at curve end (flipped 180° when reversed)."""
        heading = self.geometric_end_heading()
        if self.reversed:
            heading += radians(180.0)
        return heading

    def end_pose(self) -> tuple[float, float, float]:
        x, y = self.end_point()
        return (x, y, degrees(self.end_heading()))


class Motion:
    """A motion action composed of one or more bezier curves."""

    def __init__(self, curves: list[Curve] | None = None, g1_continuity: bool = True):
        if curves is None:
            curves = []
        self.curves = curves
        self.g1_continuity = g1_continuity
        self.profile: list[dict] | None = None
        self.time: float = 0.0

    def end_pose(self) -> tuple[float, float, float] | None:
        if not self.curves:
            return None
        return self.curves[-1].end_pose()

    def total_time(self) -> float:
        return float(self.time)

    def enforce_g1(self, previous_pose: tuple[float, float, float] | None = None) -> None:
        """Enforce G1 from previous_pose (optional) through every curve junction."""
        if not self.g1_continuity:
            return
        if previous_pose is not None and self.curves:
            apply_g1_from_pose(self.curves[0], previous_pose)
        for i in range(len(self.curves) - 1):
            apply_g1_forward(self.curves[i], self.curves[i + 1])

    def enforce_g1_after_edit(
        self,
        curve_index: int,
        point_index: int,
        previous_pose: tuple[float, float, float] | None = None,
    ) -> None:
        """Propagate G1 after editing one control point on curve_index."""
        if not self.g1_continuity or not self.curves:
            return

        # First curve stays G1 with the previous action pose (e.g. Start).
        if curve_index == 0 and previous_pose is not None and point_index in (0, 1):
            if point_index == 0:
                apply_g1_from_pose(self.curves[0], previous_pose)
            else:
                # Keep P0 on the prior pose; project P1 onto the pose heading.
                # Reversed: geometric tangent opposes robot heading.
                x, y, heading_deg = previous_pose
                join = clamp_field_point(x, y)
                self.curves[0].points[0] = join
                ux, uy = _heading_unit(heading_deg)
                if self.curves[0].reversed:
                    ux, uy = -ux, -uy
                dragged = self.curves[0].points[1]
                handle_len = max(
                    _DEFAULT_HANDLE_IN * 0.25,
                    (dragged[0] - join[0]) * ux + (dragged[1] - join[1]) * uy,
                )
                self.curves[0].points[1] = clamp_field_point(
                    join[0] + ux * handle_len, join[1] + uy * handle_len
                )
            for i in range(len(self.curves) - 1):
                apply_g1_forward(self.curves[i], self.curves[i + 1])
            return

        if point_index in (0, 1) and curve_index > 0:
            apply_g1_backward(self.curves[curve_index - 1], self.curves[curve_index])
            # Keep the rest of the chain consistent forward of this junction.
            for i in range(curve_index, len(self.curves) - 1):
                apply_g1_forward(self.curves[i], self.curves[i + 1])
            return

        if point_index in (2, 3) and curve_index + 1 < len(self.curves):
            for i in range(curve_index, len(self.curves) - 1):
                apply_g1_forward(self.curves[i], self.curves[i + 1])

    def __repr__(self) -> str:
        return f"Motion(curves={len(self.curves)}, g1={self.g1_continuity})"
