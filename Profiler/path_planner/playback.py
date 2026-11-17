"""Build and sample a routine-wide timed path from motion profiles."""

from __future__ import annotations

from bisect import bisect_right
from math import degrees, hypot

# Insert interpolated samples when profile gaps exceed this (ms).
_DENSE_STEP_MS = 16.0


def _lerp_heading(h0: float, h1: float, u: float) -> float:
    dh = ((h1 - h0 + 180) % 360) - 180
    return h0 + dh * u


def _lerp_sample(a, b, u: float):
    t0, x0, y0, h0 = a
    t1, x1, y1, h1 = b
    return (
        t0 + (t1 - t0) * u,
        x0 + (x1 - x0) * u,
        y0 + (y1 - y0) * u,
        _lerp_heading(h0, h1, u),
    )


class Timeline:
    """Sorted (t_ms, x, y, heading_deg) samples for the full routine."""

    def __init__(
        self,
        samples: list[tuple[float, float, float, float]] | None = None,
        spans: dict[str, tuple[float, float]] | None = None,
    ):
        raw = samples or [(0.0, 0.0, 0.0, 0.0)]
        self.samples = _normalize_samples(raw)
        self._times = [s[0] for s in self.samples]
        # action.key -> (start_ms, end_ms) for each motion.
        self.spans = spans or {}

    def span_of(self, action) -> tuple[float, float] | None:
        return self.spans.get(getattr(action, "key", None))

    @property
    def duration_ms(self) -> float:
        return float(self.samples[-1][0]) if self.samples else 0.0

    def pose_at(self, t_ms: float) -> tuple[float, float, float]:
        t_ms = max(0.0, min(float(t_ms), self.duration_ms))
        i = bisect_right(self._times, t_ms) - 1
        if i < 0:
            _, x, y, h = self.samples[0]
            return (x, y, h)
        if i >= len(self.samples) - 1:
            _, x, y, h = self.samples[-1]
            return (x, y, h)

        t0, x0, y0, h0 = self.samples[i]
        t1, x1, y1, h1 = self.samples[i + 1]
        if t1 <= t0:
            return (x0, y0, h0)
        u = (t_ms - t0) / (t1 - t0)
        return (x0 + (x1 - x0) * u, y0 + (y1 - y0) * u, _lerp_heading(h0, h1, u))


def _normalize_samples(
    samples: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Drop duplicate timestamps, keep time ordered, densify large gaps."""
    if not samples:
        return [(0.0, 0.0, 0.0, 0.0)]

    # Keep the last sample for each unique timestamp (later supersedes earlier).
    by_t: dict[float, tuple[float, float, float, float]] = {}
    for sample in samples:
        by_t[float(sample[0])] = sample
    ordered = [by_t[t] for t in sorted(by_t)]

    dense: list[tuple[float, float, float, float]] = [ordered[0]]
    for nxt in ordered[1:]:
        prev = dense[-1]
        dt = nxt[0] - prev[0]
        if dt > _DENSE_STEP_MS:
            steps = int(dt // _DENSE_STEP_MS)
            for s in range(1, steps + 1):
                u = min(1.0, (s * _DENSE_STEP_MS) / dt)
                if u >= 1.0:
                    break
                dense.append(_lerp_sample(prev, nxt, u))
        dense.append(nxt)
    return dense


def _hold(samples, t_offset: float, pose, dwell_ms: float) -> float:
    """Stay at ``pose`` for ``dwell_ms``. Returns the updated time offset."""
    x, y, heading = pose
    if not samples or samples[-1][0] != t_offset:
        samples.append((t_offset, x, y, heading))
    dwell = max(0.0, float(dwell_ms))
    if dwell > 0:
        t_offset += dwell
        samples.append((t_offset, x, y, heading))
    return t_offset


def _polyline_length(points) -> float:
    return sum(hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def curve_boundaries(motion) -> list[float]:
    """Profile time (ms, from motion start) at each curve join, plus 0 and the end.

    Profile samples don't record which curve they belong to, so joins are
    placed where the distance driven matches the curves' cumulative arc length.
    """
    profile = motion.profile or []
    duration = float(motion.time or (profile[-1]["t"] if profile else 0.0))
    lengths = [_polyline_length(curve.sample(48)) for curve in motion.curves]
    total = sum(lengths)
    if len(lengths) <= 1 or total <= 0:
        return [0.0, duration]

    targets = []
    acc = 0.0
    for length in lengths[:-1]:
        acc += length
        targets.append(acc / total)

    if len(profile) < 2:
        return [0.0, *(f * duration for f in targets), duration]

    times = [float(p["t"]) for p in profile]
    dist = [0.0]
    for a, b in zip(profile, profile[1:]):
        dist.append(dist[-1] + hypot(b["p"][0] - a["p"][0], b["p"][1] - a["p"][1]))
    driven = dist[-1]
    if driven <= 0:
        return [0.0, *(f * duration for f in targets), duration]

    out = [0.0]
    for f in targets:
        s = f * driven
        i = max(0, min(bisect_right(dist, s) - 1, len(dist) - 2))
        seg = dist[i + 1] - dist[i]
        u = (s - dist[i]) / seg if seg > 0 else 0.0
        out.append(times[i] + (times[i + 1] - times[i]) * u)
    out.append(duration)
    return out


def build_routine_timeline(actions) -> Timeline:
    """Stitch start dwell, code-action waits, and motion profiles into one path."""
    from actions import MotionAction, StartAction

    samples: list[tuple[float, float, float, float]] = []
    spans: dict[str, tuple[float, float]] = {}
    t_offset = 0.0
    pose = (24.0, 24.0, 0.0)

    for action in actions:
        if isinstance(action, StartAction):
            pose = action.end_pose()
            t_offset = _hold(samples, t_offset, pose, action.time)
            continue

        if isinstance(action, MotionAction):
            profile = action.motion.profile or []
            if not profile:
                if not action.motion.curves:
                    continue
                x0, y0 = action.motion.curves[0].start_point()
                heading0 = degrees(action.motion.curves[0].end_heading())
                samples.append((t_offset, x0, y0, heading0))
                end = action.end_pose()
                elapsed = float(action.motion.time or 0.0)
                if end is not None:
                    pose = end
                    samples.append((t_offset + elapsed, end[0], end[1], end[2]))
                spans[action.key] = (t_offset, t_offset + elapsed)
                t_offset += elapsed
                continue

            for point in profile:
                x, y, theta = point["p"]
                samples.append(
                    (t_offset + float(point["t"]), float(x), float(y), degrees(float(theta)))
                )
            end = action.end_pose()
            if end is not None:
                pose = end
            last_t = float(profile[-1]["t"])
            start = t_offset
            t_offset += float(action.motion.time) if action.motion.time else last_t
            spans[action.key] = (start, t_offset)
            continue

        # Code actions (and anything else without its own path) hold position.
        t_offset = _hold(samples, t_offset, pose, getattr(action, "time", 0.0) or 0.0)

    if not samples:
        samples = [(0.0, 24.0, 24.0, 0.0)]

    return Timeline(samples, spans)
