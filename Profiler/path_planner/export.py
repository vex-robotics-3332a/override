"""Export a routine: Rust macro via parser + motion profiles to SD (or local fallback)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from parser import export_routine_macro
from serialize import routine_to_disk_json
from storage import resolve_sd_routine_dir

# Local fallback when the configured SD card path is missing / unwritable.
LOCAL_PROFILES_DIR = Path(__file__).resolve().parent / "exported_profiles"


@dataclass
class ExportResult:
    ok: bool
    messages: list[str] = field(default_factory=list)
    macro_path: Path | None = None
    profile_dir: Path | None = None
    profile_paths: list[Path] = field(default_factory=list)
    used_sd_fallback: bool = False

    @property
    def summary(self) -> str:
        return "\n".join(self.messages)


def is_writable_directory(path: Path) -> bool:
    """True if path exists (or can be created) and is writable."""
    try:
        path = path.expanduser()
        if not path.exists():
            # Only auto-create if the parent exists (avoid inventing mount points).
            if not path.parent.exists():
                return False
            path.mkdir(parents=True, exist_ok=True)
        probe = path / ".profiler_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def choose_profile_root(sd_card_path: str, routine_name: str) -> tuple[Path | None, bool, str]:
    """Pick SD routine dir, or a local fallback.

    Returns (directory, used_fallback, status_message).
    """
    configured = (sd_card_path or "").strip()
    if not configured:
        fallback = LOCAL_PROFILES_DIR / routine_name
        fallback.mkdir(parents=True, exist_ok=True)
        return (
            fallback,
            True,
            f"SD card path is not set; writing profiles to {fallback}",
        )

    sd_dir = resolve_sd_routine_dir(configured, routine_name)
    if sd_dir is not None and is_writable_directory(sd_dir):
        return sd_dir, False, f"Writing profiles to SD: {sd_dir}"

    fallback = LOCAL_PROFILES_DIR / routine_name
    fallback.mkdir(parents=True, exist_ok=True)
    reason = "unreachable or not writable" if configured else "not set"
    return (
        fallback,
        True,
        f"SD card path is {reason} ({configured or 'empty'}); "
        f"writing profiles to local fallback {fallback}",
    )


def write_motion_profiles(routine, profile_dir: Path) -> list[Path]:
    """Write each motion's profile JSON into profile_dir as motionN.json."""
    from actions import MotionAction

    written: list[Path] = []
    profile_dir.mkdir(parents=True, exist_ok=True)
    if routine.action_list is None:
        return written

    for action in routine.action_list.motion_actions():
        if not isinstance(action, MotionAction):
            continue
        # Ensure a fresh profile before export.
        action.recompute_profile()
        profile = action.motion.profile or []
        out = profile_dir / f"motion{action.motion_id}.json"
        out.write_text(json.dumps({"points": profile}, indent=2) + "\n", encoding="utf-8")
        written.append(out)
    return written


def export_routine(
    routine,
    *,
    code_root: str,
    sd_card_path: str,
) -> ExportResult:
    """Save-ready export: parser macro + motion profiles.

    Caller should persist the routine JSON before calling this.
    """
    result = ExportResult(ok=True)
    doc: dict[str, Any] = routine_to_disk_json(routine)

    # 1) Rust macro via parser (code root).
    try:
        macro_path = export_routine_macro(doc, code_root=code_root)
        if isinstance(macro_path, Path):
            result.macro_path = macro_path
            result.messages.append(f"Wrote routine macro: {macro_path}")
        else:
            # Older parser returned None; resolve for messaging.
            from storage import resolve_routine_path

            resolved = resolve_routine_path(code_root, doc.get("path", ""))
            result.macro_path = resolved
            result.messages.append(
                f"Wrote routine macro: {resolved}" if resolved else "Wrote routine macro."
            )
    except Exception as exc:
        result.ok = False
        result.messages.append(f"Macro export failed: {exc}")
        # Still attempt profiles so partial export is useful.

    # 2) Motion profiles → SD card (or local fallback).
    profile_dir, used_fallback, status = choose_profile_root(sd_card_path, routine.name)
    result.used_sd_fallback = used_fallback
    result.messages.append(status)
    if profile_dir is None:
        result.ok = False
        result.messages.append("Could not determine a profile output directory.")
        return result

    try:
        paths = write_motion_profiles(routine, profile_dir)
        result.profile_dir = profile_dir
        result.profile_paths = paths
        if paths:
            result.messages.append(f"Wrote {len(paths)} motion profile(s).")
        else:
            result.messages.append("No motion actions to profile.")
    except OSError as exc:
        result.ok = False
        result.messages.append(f"Profile write failed: {exc}")

    return result
