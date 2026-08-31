"""Load / save routine JSON files under path_planner/routines/."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from serialize import routine_from_disk_json, routine_to_disk_json

ROUTINES_DIR = Path(__file__).resolve().parent / "routines"
INDEX_NAME = "_index.json"
AUTOSAVE_DELAY_S = 0.8

# Routine names are file stems: letters, digits, underscore, hyphen only.
ROUTINE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def ensure_routines_dir() -> Path:
    ROUTINES_DIR.mkdir(parents=True, exist_ok=True)
    return ROUTINES_DIR


def is_valid_routine_name(name: str) -> bool:
    return bool(name) and ROUTINE_NAME_RE.fullmatch(name) is not None


def sanitize_routine_name(name: str, *, fallback: str = "routine") -> str:
    """Strip spaces, periods, and other invalid filename characters."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "", (name or "").strip())
    return cleaned or fallback


def unique_routine_name(base: str, existing: set[str], *, fallback: str = "routine") -> str:
    base = sanitize_routine_name(base, fallback=fallback)
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def routine_file_path(file_id: str) -> Path:
    name = sanitize_routine_name(file_id)
    return ensure_routines_dir() / f"{name}.json"


def allocate_file_id(existing: set[str], prefix: str = "routine") -> str:
    prefix = sanitize_routine_name(prefix)
    n = 1
    while True:
        candidate = f"{prefix}_{n}"
        if candidate not in existing and not routine_file_path(candidate).exists():
            return candidate
        n += 1


def save_routine_file(routine) -> Path:
    """Write one routine to routines/<file_id>.json. Returns the path."""
    path = routine_file_path(routine.file_id)
    doc = routine_to_disk_json(routine)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def rename_routine_file(old_id: str, new_id: str) -> Path:
    """Rename routines/<old>.json → routines/<new>.json if needed."""
    old_path = routine_file_path(old_id)
    new_path = routine_file_path(new_id)
    if old_path == new_path:
        return new_path
    if old_path.is_file():
        if new_path.exists():
            raise FileExistsError(f"Routine file already exists: {new_path.name}")
        old_path.rename(new_path)
    return new_path


def delete_routine_file(file_id: str) -> None:
    path = routine_file_path(file_id)
    if path.is_file():
        path.unlink()


def load_routine_doc(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def list_routine_files() -> list[Path]:
    ensure_routines_dir()
    return sorted(
        p
        for p in ROUTINES_DIR.glob("*.json")
        if p.name != INDEX_NAME and not p.name.startswith(".")
    )


def load_index() -> dict[str, Any]:
    path = ensure_routines_dir() / INDEX_NAME
    if not path.is_file():
        return {"version": 1, "selected": None, "next_id": 1}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "selected": None, "next_id": 1}
    if not isinstance(data, dict):
        return {"version": 1, "selected": None, "next_id": 1}
    return data


def save_index(*, selected_file_id: str | None, next_id: int) -> None:
    path = ensure_routines_dir() / INDEX_NAME
    doc = {
        "version": 1,
        "selected": selected_file_id,
        "next_id": int(next_id),
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def parse_routine_file(path: Path) -> tuple[str, str, str, dict[str, Any]] | None:
    """Return (file_id, name, export_path, actions_doc) or None if unreadable.

    Filename stem is the canonical routine name (must be a valid file name).
    """
    doc = load_routine_doc(path)
    if doc is None:
        return None
    file_id = sanitize_routine_name(path.stem)
    if file_id != path.stem:
        # Invalid stem — skip rather than silently rewrite on scan.
        return None
    _ignored_name, export_path, actions_doc = routine_from_disk_json(doc)
    return file_id, file_id, export_path, actions_doc


def fingerprint_routine(routine) -> str:
    """Stable fingerprint of the on-disk document for dirty detection."""
    return json.dumps(routine_to_disk_json(routine), sort_keys=True, separators=(",", ":"))


def resolve_routine_path(code_root: str, routine_path: str) -> Path | None:
    """Resolve a routine export path against the master robot-code directory.

    Absolute ``routine_path`` values are returned as-is. Relative paths are
    joined under ``code_root``. Returns None when the result cannot be formed.
    """
    rel = (routine_path or "").strip()
    if not rel:
        return None
    candidate = Path(rel).expanduser()
    if candidate.is_absolute():
        return candidate
    root = (code_root or "").strip()
    if not root:
        return None
    return (Path(root).expanduser() / candidate).resolve()


def resolve_sd_routine_dir(sd_card_path: str, routine_name: str) -> Path | None:
    """Directory on the SD card for one routine's motion profiles.

    Layout: ``{sd_card_path}/{routine_name}/``
    """
    root = (sd_card_path or "").strip()
    name = (routine_name or "").strip()
    if not root or not name:
        return None
    return (Path(root).expanduser() / name).resolve()


def resolve_sd_motion_profile_path(
    sd_card_path: str,
    routine_name: str,
    motion_id: int,
) -> Path | None:
    """Path for one motion profile JSON on the SD card.

    Layout: ``{sd_card_path}/{routine_name}/motion{id}.json``
    """
    directory = resolve_sd_routine_dir(sd_card_path, routine_name)
    if directory is None:
        return None
    return directory / f"motion{int(motion_id)}.json"
