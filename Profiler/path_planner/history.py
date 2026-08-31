"""Per-routine undo/redo history backed by action-list JSON snapshots."""

from __future__ import annotations

import copy
import json
from typing import Any

HISTORY_VERSION = 1
DEFAULT_MAX_DEPTH = 75


def _freeze(doc: dict[str, Any]) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


class ActionHistory:
    """Undo/redo stack for one routine's action list.

    - ``current`` is the live actions JSON document
    - ``undo`` / ``redo`` hold prior/future action documents
    """

    def __init__(
        self,
        current: dict[str, Any] | None = None,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ):
        from serialize import empty_actions_doc

        self.max_depth = max(1, int(max_depth))
        self.current: dict[str, Any] = copy.deepcopy(current or empty_actions_doc())
        self.undo: list[dict[str, Any]] = []
        self.redo: list[dict[str, Any]] = []

    @property
    def can_undo(self) -> bool:
        return bool(self.undo)

    @property
    def can_redo(self) -> bool:
        return bool(self.redo)

    def seed(self, doc: dict[str, Any]) -> None:
        """Set current state without creating an undo entry."""
        self.current = copy.deepcopy(doc)
        self.undo.clear()
        self.redo.clear()

    def push(self, doc: dict[str, Any]) -> bool:
        """Commit a new current document. Returns False if unchanged."""
        if _freeze(doc) == _freeze(self.current):
            return False
        self.undo.append(self.current)
        if len(self.undo) > self.max_depth:
            self.undo = self.undo[-self.max_depth :]
        self.current = copy.deepcopy(doc)
        self.redo.clear()
        return True

    def replace_current(self, doc: dict[str, Any]) -> None:
        """Update current without touching undo/redo (e.g. after restore)."""
        self.current = copy.deepcopy(doc)

    def undo_once(self) -> dict[str, Any] | None:
        if not self.undo:
            return None
        self.redo.append(self.current)
        self.current = self.undo.pop()
        return copy.deepcopy(self.current)

    def redo_once(self) -> dict[str, Any] | None:
        if not self.redo:
            return None
        self.undo.append(self.current)
        self.current = self.redo.pop()
        return copy.deepcopy(self.current)

    def to_json(self) -> dict[str, Any]:
        """History document stored separately from the routines list JSON."""
        return {
            "version": HISTORY_VERSION,
            "current": copy.deepcopy(self.current),
            "undo": copy.deepcopy(self.undo),
            "redo": copy.deepcopy(self.redo),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any] | None, *, max_depth: int = DEFAULT_MAX_DEPTH) -> "ActionHistory":
        from serialize import empty_actions_doc

        hist = cls(max_depth=max_depth)
        if not data:
            hist.seed(empty_actions_doc())
            return hist
        hist.current = copy.deepcopy(data.get("current") or empty_actions_doc())
        hist.undo = [copy.deepcopy(d) for d in data.get("undo", [])]
        hist.redo = [copy.deepcopy(d) for d in data.get("redo", [])]
        if len(hist.undo) > hist.max_depth:
            hist.undo = hist.undo[-hist.max_depth :]
        return hist
