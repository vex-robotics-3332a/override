"""Load robot pose-capture CSV logs for the Visualize tab."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from playback import Timeline

REQUIRED_COLUMNS = ("timestamp", "x", "y", "heading")


@dataclass
class CaptureLoadResult:
    ok: bool
    timeline: Timeline | None = None
    path_points: list[tuple[float, float]] = field(default_factory=list)
    sample_count: int = 0
    error: str = ""
    warnings: list[str] = field(default_factory=list)


def normalize_heading_deg(heading: float) -> float:
    """Wrap degrees into (-180, 180]."""
    return ((float(heading) + 180.0) % 360.0) - 180.0


def load_pose_capture(path: str | Path) -> CaptureLoadResult:
    """Parse a CSV with header ``timestamp,x,y,heading`` (ms + inches + degrees)."""
    file_path = Path(path).expanduser()
    if not str(path).strip():
        return CaptureLoadResult(ok=False, error="No capture file path set.")
    if not file_path.is_file():
        return CaptureLoadResult(ok=False, error=f"File not found: {file_path}")

    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return CaptureLoadResult(ok=False, error=f"Could not read file: {exc}")

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return CaptureLoadResult(ok=False, error="File is empty.")

    header_parts = [p.strip().lower() for p in lines[0].split(",")]
    if header_parts[:4] != list(REQUIRED_COLUMNS):
        return CaptureLoadResult(
            ok=False,
            error=(
                "Invalid header. Expected "
                f"{','.join(REQUIRED_COLUMNS)} — got {lines[0]!r}."
            ),
        )

    samples: list[tuple[float, float, float, float]] = []
    warnings: list[str] = []
    skipped = 0

    for line_no, line in enumerate(lines[1:], start=2):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            skipped += 1
            warnings.append(f"Line {line_no}: expected 4 columns, got {len(parts)}.")
            continue
        try:
            t_ms = float(parts[0])
            x = float(parts[1])
            y = float(parts[2])
            heading = normalize_heading_deg(float(parts[3]))
        except ValueError:
            skipped += 1
            warnings.append(f"Line {line_no}: non-numeric values.")
            continue
        if t_ms < 0:
            skipped += 1
            warnings.append(f"Line {line_no}: negative timestamp.")
            continue
        samples.append((t_ms, x, y, heading))

    if not samples:
        return CaptureLoadResult(
            ok=False,
            error="No valid pose samples found.",
            warnings=warnings[:8],
        )

    # Keep time non-decreasing for timeline sampling.
    samples.sort(key=lambda s: s[0])
    timeline = Timeline(samples)
    path_points = [(x, y) for _, x, y, _ in samples]
    if skipped:
        warnings.insert(0, f"Skipped {skipped} invalid row(s).")

    return CaptureLoadResult(
        ok=True,
        timeline=timeline,
        path_points=path_points,
        sample_count=len(samples),
        warnings=warnings[:8],
    )
