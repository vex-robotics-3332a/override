"""Build and sample a routine-wide timed path from motion profiles."""

from __future__ import annotations

from bisect import bisect_right
from math import degrees

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

    def __init__(self, samples: list[tuple[float, float, float, float]] | None = None):
        raw = samples or [(0.0, 0.0, 0.0, 0.0)]
        self.samples = _normalize_samples(raw)
        self._times = [s[0] for s in self.samples]

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


def build_routine_timeline(actions) -> Timeline:
    """Stitch Start dwell + each motion's continuous profile into one path."""
    from actions import MotionAction, StartAction

    samples: list[tuple[float, float, float, float]] = []
    t_offset = 0.0

    start = next((a for a in actions if isinstance(a, StartAction)), None)
    if start is not None:
        x, y, heading = start.end_pose()
        samples.append((0.0, x, y, heading))
        if start.time > 0:
            t_offset = float(start.time)
            samples.append((t_offset, x, y, heading))

    for action in actions:
        if not isinstance(action, MotionAction):
            continue

        profile = action.motion.profile or []
        if not profile:
            # Fall back to geometric endpoints if unprofiled.
            if not action.motion.curves:
                continue
            x0, y0 = action.motion.curves[0].start_point()
            end = action.motion.end_pose()
            heading0 = degrees(action.motion.curves[0].end_heading())
            samples.append((t_offset, x0, y0, heading0))
            if end is not None:
                samples.append((t_offset + float(action.motion.time or 0.0), end[0], end[1], end[2]))
            t_offset += float(action.motion.time or 0.0)
            continue

        for point in profile:
            x, y, theta = point["p"]
            samples.append(
                (t_offset + float(point["t"]), float(x), float(y), degrees(float(theta)))
            )
        last_t = float(profile[-1]["t"])
        t_offset += float(action.motion.time) if action.motion.time else last_t

    if not samples:
        samples = [(0.0, 24.0, 24.0, 0.0)]

    return Timeline(samples)
