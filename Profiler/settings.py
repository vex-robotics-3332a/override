"""Load / save Profiler settings.json (robot specs, etc.)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

_DEFAULT_ROBOT = {
    "max_speed": 76.38162268,
    "max_accel": 100.0,
    "track_width": 11.5,
    "length": 18.0,
    "width": 18.0,
    # Scales accel with curvature: a = a_max / (1 + gain*|κ|*track/2).
    # 0 = ignore curvature; 1 ≈ wheel force split; >1 adds rotational inertia.
    "accel_curvature_gain": 1.0,
    # Turning center relative to the body's geometric center, in the robot frame
    # (+X forward along heading, +Y to the robot's left), inches.
    "turn_offset_x": 0.0,
    "turn_offset_y": 0.0,
}

_DEFAULT_SETTINGS = {
    "robot": deepcopy(_DEFAULT_ROBOT),
    "code_root": "",
    "sd_card_path": "",
    # Field image: "match" → override_match.png, "skills" → override_skills.png
    "field_background": "match",
}

_FIELD_BACKGROUNDS = {
    "match": "override_match.png",
    "skills": "override_skills.png",
}


def _merge_robot(raw: dict | None) -> dict:
    robot = deepcopy(_DEFAULT_ROBOT)
    if isinstance(raw, dict):
        for key in _DEFAULT_ROBOT:
            if key in raw:
                robot[key] = float(raw[key])
    return robot


def _as_str(value) -> str:
    return value if isinstance(value, str) else ""


def _normalize_field_background(value) -> str:
    key = str(value or "match").strip().lower()
    return key if key in _FIELD_BACKGROUNDS else "match"


def field_image_for_background(background: str | None = None) -> str:
    """Return the asset filename for a field background key."""
    key = _normalize_field_background(
        background if background is not None else get_field_background()
    )
    return _FIELD_BACKGROUNDS[key]


def load_settings() -> dict:
    if not SETTINGS_PATH.is_file():
        return deepcopy(_DEFAULT_SETTINGS)
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return deepcopy(_DEFAULT_SETTINGS)
    if not isinstance(data, dict):
        return deepcopy(_DEFAULT_SETTINGS)
    return {
        **deepcopy(_DEFAULT_SETTINGS),
        **data,
        "robot": _merge_robot(data.get("robot")),
        "code_root": _as_str(data.get("code_root", "")),
        "sd_card_path": _as_str(data.get("sd_card_path", "")),
        "field_background": _normalize_field_background(data.get("field_background")),
    }


def save_settings(data: dict) -> None:
    payload = {
        **deepcopy(_DEFAULT_SETTINGS),
        **data,
        "robot": _merge_robot(data.get("robot")),
        "code_root": _as_str(data.get("code_root", "")),
        "sd_card_path": _as_str(data.get("sd_card_path", "")),
        "field_background": _normalize_field_background(data.get("field_background")),
    }
    with SETTINGS_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def get_robot_settings() -> dict:
    return load_settings()["robot"]


def save_robot_settings(robot: dict) -> None:
    data = load_settings()
    data["robot"] = _merge_robot(robot)
    save_settings(data)


def get_code_root() -> str:
    return str(load_settings().get("code_root") or "")


def save_code_root(code_root: str) -> None:
    data = load_settings()
    data["code_root"] = str(code_root or "")
    save_settings(data)


def get_sd_card_path() -> str:
    return str(load_settings().get("sd_card_path") or "")


def save_sd_card_path(sd_card_path: str) -> None:
    data = load_settings()
    data["sd_card_path"] = str(sd_card_path or "")
    save_settings(data)


def get_field_background() -> str:
    return _normalize_field_background(load_settings().get("field_background"))


def save_field_background(background: str) -> str:
    key = _normalize_field_background(background)
    data = load_settings()
    data["field_background"] = key
    save_settings(data)
    return key
