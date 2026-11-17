"""Modifier keys currently held down.

Flet's page keyboard event skips bare modifier presses, and click events carry
no modifier state, so shift-click relies on a KeyboardListener feeding this.
"""

_held: set[str] = set()


def key_down(key: str) -> None:
    _held.add(key or "")


def key_up(key: str) -> None:
    _held.discard(key or "")


def _held_with_prefix(prefix: str) -> bool:
    return any(key.startswith(prefix) for key in _held)


def shift_held() -> bool:
    return _held_with_prefix("Shift")


def alt_held() -> bool:
    """⌥ on macOS."""
    return _held_with_prefix("Alt")


def command_held() -> bool:
    """⌘ on macOS, Ctrl elsewhere."""
    return _held_with_prefix("Meta") or _held_with_prefix("Control")
