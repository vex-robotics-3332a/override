"""JSON schemas for routines and action lists (load/save / undo snapshots)."""

from __future__ import annotations

from typing import Any

from motion import Curve, Motion, clamp_field_coord

ACTIONS_VERSION = 1
ROUTINES_VERSION = 1
ROUTINE_FILE_VERSION = 1


def curve_to_json(curve: Curve) -> dict[str, Any]:
    return {
        "points": [[float(x), float(y)] for x, y in curve.points],
        "max_speed": float(curve.max_speed),
        "reversed": bool(curve.reversed),
    }


def curve_from_json(data: dict[str, Any]) -> Curve:
    points = [(float(p[0]), float(p[1])) for p in data["points"]]
    return Curve(
        points=points,
        max_speed=float(data.get("max_speed", 100.0)),
        reversed=bool(data.get("reversed", False)),
    )


def motion_to_json(motion: Motion) -> dict[str, Any]:
    return {
        "g1_continuity": bool(motion.g1_continuity),
        "curves": [curve_to_json(c) for c in motion.curves],
    }


def motion_from_json(data: dict[str, Any]) -> Motion:
    curves = [curve_from_json(c) for c in data.get("curves", [])]
    if not curves:
        curves = [Curve()]
    return Motion(
        curves=curves,
        g1_continuity=bool(data.get("g1_continuity", True)),
    )


def action_to_json(action, *, include_profile_time: bool = True) -> dict[str, Any]:
    """Serialize one action with a ``type`` discriminant and typed attributes.

    Disk / parse format omits motion profile ``time`` (recomputed on load).
    Undo snapshots may keep it via ``include_profile_time=True``.
    """
    # Import locally to avoid a serialize ↔ actions import cycle at module load.
    from actions import MotionAction, StartAction

    if isinstance(action, StartAction):
        return {
            "type": "start",
            "name": action.name,
            "x": float(action.x),
            "y": float(action.y),
            "heading": float(action.heading),
            "time": float(action.time),
        }
    if isinstance(action, MotionAction):
        payload = {
            "type": "motion",
            "id": int(action.motion_id),
            **motion_to_json(action.motion),
        }
        if include_profile_time:
            payload["time"] = float(action.time)
        return payload
    raise TypeError(f"Unsupported action type: {type(action)!r}")


def action_from_json(data: dict[str, Any]):
    from actions import MotionAction, StartAction

    kind = data.get("type")
    if kind == "start":
        start = StartAction(
            x=clamp_field_coord(float(data.get("x", 24.0))),
            y=clamp_field_coord(float(data.get("y", 24.0))),
            heading=float(data.get("heading", 0.0)),
            name=str(data.get("name", "Start")),
        )
        start.time = float(data.get("time", 0.0))
        return start
    if kind == "motion":
        if "id" not in data:
            raise ValueError("Motion action requires numeric 'id'")
        motion = motion_from_json(data)
        action = MotionAction(motion, int(data["id"]))
        # Prefer recomputed profile time; keep serialized time only as fallback.
        if action.time <= 0 and "time" in data:
            action.time = float(data["time"])
        return action
    raise ValueError(f"Unknown action type: {kind!r}")


def actions_to_json(actions: list, *, include_profile_time: bool = True) -> dict[str, Any]:
    """Serialize an action list document (current state of one routine)."""
    return {
        "version": ACTIONS_VERSION,
        "actions": [
            action_to_json(a, include_profile_time=include_profile_time) for a in actions
        ],
    }


def actions_from_json(data: dict[str, Any] | None) -> list:
    if not data:
        from actions import StartAction

        return [StartAction()]
    raw = data.get("actions")
    if not isinstance(raw, list) or not raw:
        from actions import StartAction

        return [StartAction()]
    actions = [action_from_json(item) for item in raw]
    # Guarantee a Start action at index 0.
    from actions import MotionAction, StartAction

    if not actions or not isinstance(actions[0], StartAction):
        actions.insert(0, StartAction())
    # Normalize motion ids to 1-based list order.
    motion_i = 0
    for action in actions:
        if isinstance(action, MotionAction):
            motion_i += 1
            action.set_motion_id(motion_i)
    return actions


def empty_actions_doc() -> dict[str, Any]:
    from actions import StartAction

    return actions_to_json([StartAction()], include_profile_time=False)


def routine_to_disk_json(routine) -> dict[str, Any]:
    """On-disk routine file: name/path + typed actions for later parsing.

    ``name`` is the file stem (no spaces/periods); it must match ``file_id``.
    """
    if routine.action_list is not None:
        actions = routine.action_list.actions
    else:
        actions = actions_from_json(routine.history.current)
    name = routine.file_id
    return {
        "version": ROUTINE_FILE_VERSION,
        "name": name,
        "path": routine.path,
        "actions": [
            action_to_json(a, include_profile_time=False) for a in actions
        ],
    }


def routine_from_disk_json(data: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Return (name, path, actions_doc) from a routine file document.

    Callers should treat the on-disk filename stem as the canonical name.
    """
    path = str(data.get("path") or "")
    actions_doc = {
        "version": ACTIONS_VERSION,
        "actions": list(data.get("actions") or []),
    }
    # Name is provided by the filename; ignore any mismatched embedded value.
    name = str(data.get("name") or "")
    return name, path, actions_doc


def routine_to_json(routine) -> dict[str, Any]:
    """One routine entry: metadata + current actions document."""
    return routine_to_disk_json(routine)


def routines_to_json(routines: list, selected_index: int = 0) -> dict[str, Any]:
    """Top-level routines document (separate from per-routine history)."""
    return {
        "version": ROUTINES_VERSION,
        "selected": int(selected_index),
        "routines": [routine_to_json(r) for r in routines],
    }
