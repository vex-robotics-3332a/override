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
}

_DEFAULT_SETTINGS = {
    "robot": deepcopy(_DEFAULT_ROBOT),
}


def _merge_robot(raw: dict | None) -> dict:
    robot = deepcopy(_DEFAULT_ROBOT)
    if isinstance(raw, dict):
        for key in _DEFAULT_ROBOT:
            if key in raw:
                robot[key] = float(raw[key])
    return robot


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
    }


def save_settings(data: dict) -> None:
    payload = {
        **deepcopy(_DEFAULT_SETTINGS),
        **data,
        "robot": _merge_robot(data.get("robot")),
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
