"""JSON schemas for routines and action lists (load/save / undo snapshots)."""

from __future__ import annotations

import math
from typing import Any

from motion import Curve, Motion, clamp_field_coord

INF_TIMEOUT = "inf"


def timeout_to_json(timeout: float | None) -> float | str | None:
    """``None`` (follow profile time), ms, or ``"inf"`` (JSON has no infinity)."""
    if timeout is None:
        return None
    if math.isinf(timeout):
        return INF_TIMEOUT
    return float(timeout)


def timeout_from_json(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("", "auto", "default"):
            return None
        if text in ("inf", "infinity", "∞"):
            return math.inf
        value = text
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None

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
        "start_speed": float(motion.start_speed),
        "end_speed": float(motion.end_speed),
        "curves": [curve_to_json(c) for c in motion.curves],
    }


def motion_from_json(data: dict[str, Any]) -> Motion:
    curves = [curve_from_json(c) for c in data.get("curves", [])]
    if not curves:
        curves = [Curve()]
    return Motion(
        curves=curves,
        g1_continuity=bool(data.get("g1_continuity", True)),
        end_speed=float(data.get("end_speed", 0.0)),
        start_speed=float(data.get("start_speed", 0.0)),
    )


def action_to_json(action, *, include_profile_time: bool = True) -> dict[str, Any]:
    """Serialize one action with a ``type`` discriminant and typed attributes.

    Disk / parse format omits motion profile ``time`` (recomputed on load).
    Undo snapshots may keep it via ``include_profile_time=True``.
    """
    # Import locally to avoid a serialize ↔ actions import cycle at module load.
    from actions import BreakpointAction, CodeAction, MotionAction, StartAction

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
            "code": action.code,
            "timeout": timeout_to_json(action.timeout),
        }
        if include_profile_time:
            payload["time"] = float(action.time)
        return payload
    if isinstance(action, CodeAction):
        return {
            "type": "action",
            "name": action.name,
            "code": action.code,
            "time": float(action.time),
        }
    if isinstance(action, BreakpointAction):
        return {"type": "breakpoint", "name": action.name, "enabled": bool(action.enabled)}
    raise TypeError(f"Unsupported action type: {type(action)!r}")


def action_from_json(data: dict[str, Any]):
    from actions import BreakpointAction, CodeAction, MotionAction, StartAction

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
        action = MotionAction(
            motion,
            int(data["id"]),
            code=str(data.get("code") or ""),
            timeout=timeout_from_json(data.get("timeout")),
        )
        # Prefer recomputed profile time; keep serialized time only as fallback.
        if action.time <= 0 and "time" in data:
            action.time = float(data["time"])
        return action
    if kind == "action":
        return CodeAction(
            code=str(data.get("code") or ""),
            time=max(0.0, float(data.get("time", 0.0))),
            name=str(data.get("name") or "Action"),
        )
    if kind == "breakpoint":
        return BreakpointAction(
            enabled=bool(data.get("enabled", True)),
            name=str(data.get("name") or "Breakpoint"),
        )
    raise ValueError(f"Unknown action type: {kind!r}")


def action_items_to_json(actions: list, *, include_profile_time: bool = True) -> list[dict[str, Any]]:
    """Serialize actions in order; grouped ones carry ``group`` {id, name, collapsed}.

    Group ids are only meaningful within one list (members share an id).
    """
    group_ids: dict[int, int] = {}
    items = []
    for action in actions:
        item = action_to_json(action, include_profile_time=include_profile_time)
        group = getattr(action, "group", None)
        if group is not None:
            gid = group_ids.setdefault(id(group), len(group_ids) + 1)
            item["group"] = {"id": gid, "name": group.name, "collapsed": bool(group.collapsed)}
        items.append(item)
    return items


def action_items_from_json(raw: list[dict[str, Any]]) -> list:
    """Inverse of ``action_items_to_json``: fresh actions with shared group objects."""
    from actions import ActionGroup, StartAction

    groups: dict[Any, ActionGroup] = {}
    actions = []
    for item in raw:
        action = action_from_json(item)
        info = item.get("group")
        if isinstance(info, dict) and not isinstance(action, StartAction):
            gid = info.get("id")
            if gid not in groups:
                groups[gid] = ActionGroup(
                    str(info.get("name") or "Group"),
                    collapsed=bool(info.get("collapsed", False)),
                )
            action.group = groups[gid]
        actions.append(action)
    return actions


def actions_to_json(actions: list, *, include_profile_time: bool = True) -> dict[str, Any]:
    """Serialize an action list document (current state of one routine)."""
    return {
        "version": ACTIONS_VERSION,
        "actions": action_items_to_json(actions, include_profile_time=include_profile_time),
    }


def actions_from_json(data: dict[str, Any] | None) -> list:
    if not data:
        from actions import StartAction

        return [StartAction()]
    raw = data.get("actions")
    if not isinstance(raw, list) or not raw:
        from actions import StartAction

        return [StartAction()]
    actions = action_items_from_json(raw)
    # Guarantee a Start action at index 0.
    from actions import CodeAction, MotionAction, StartAction, normalize_groups

    if not actions or not isinstance(actions[0], StartAction):
        actions.insert(0, StartAction())
    # Normalize motion and code-action ids to 1-based list order.
    motion_i = 0
    code_i = 0
    for action in actions:
        if isinstance(action, MotionAction):
            motion_i += 1
            action.set_motion_id(motion_i)
        elif isinstance(action, CodeAction):
            code_i += 1
            action.set_action_id(code_i)
    normalize_groups(actions)
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
        "actions": action_items_to_json(actions, include_profile_time=False),
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
