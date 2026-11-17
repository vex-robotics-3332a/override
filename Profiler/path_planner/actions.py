import math
import re
from math import cos, radians, sin

import flet as ft

import keys
from history import ActionHistory
from motion import (
    FIELD_SIZE_IN,
    Curve,
    Motion,
    apply_g1_forward,
    apply_g1_from_pose,
    clamp_field_coord,
    clamp_field_point,
)
from playback import Timeline, build_routine_timeline, curve_boundaries
from profiling import profile_motion
from serialize import (
    action_items_from_json,
    action_items_to_json,
    actions_from_json,
    actions_to_json,
    curve_from_json,
    curve_to_json,
    timeout_from_json,
)
from theme import (
    ACCENT,
    ACCENT_BORDER,
    ACCENT_CONTAINER,
    ACCENT_LIGHT,
    MONO_FONT,
    MONO_TEXT_STYLE,
    OUTLINED_GREEN_STYLE,
    RED_LIGHT,
    blue_button,
    green_button,
    red_button,
    title_text,
)


POINT_LABELS = ("P0", "P1", "P2", "P3")

CREATABLE_ACTION_TYPES: dict[str, str] = {
    "Motion": "Cubic bezier path",
    "Action": "Robot code and a wait",
}

# One indent level inserted by Tab / newline in the action code box.
_INDENT = "\t"
_BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}"}

TITLE_SIZE_LIST = 20
TITLE_SIZE_EDITOR = 18
TITLE_SIZE_SECTION = 16
LEFT_PANEL_WIDTH = 280
EASE_ANIM = ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT)


def _is_mounted(control) -> bool:
    try:
        return control.page is not None
    except RuntimeError:
        return False


def _text_field(
    label: str,
    value: str,
    on_change,
    width: float | None = None,
    on_blur=None,
) -> ft.TextField:
    return ft.TextField(
        label=label,
        value=value,
        on_change=on_change,
        on_blur=on_blur,
        width=width,
        text_size=13,
        text_style=MONO_TEXT_STYLE,
        border=ft.InputBorder.OUTLINE,
        border_width=1.5,
        border_color=ft.Colors.OUTLINE,
        focused_border_width=2,
        focused_border_color=ACCENT_BORDER,
    )


def _leading_whitespace(line: str) -> str:
    index = 0
    while index < len(line) and line[index] in " \t":
        index += 1
    return line[:index]


def _indent_levels(whitespace: str) -> int:
    """Count indent levels. A tab or four spaces is one level."""
    levels = 0
    index = 0
    while index < len(whitespace):
        if whitespace[index] == "\t":
            levels += 1
            index += 1
        elif whitespace.startswith("    ", index):
            levels += 1
            index += 4
        else:
            break
    return levels


def _indent_text(levels: int) -> str:
    return _INDENT * max(0, levels)


def _previous_nonempty_line(text: str, line_start: int) -> str | None:
    """Return the previous line that contains non-whitespace, if any."""
    cursor = line_start
    while cursor > 0:
        prev_end = cursor - 1
        prev_start = text.rfind("\n", 0, prev_end) + 1
        line = text[prev_start:prev_end]
        if line.strip():
            return line
        cursor = prev_start
    return None


def _smart_indent(text: str, line_start: int) -> str:
    """Indent a line should have, based on the previous non-empty line."""
    previous = _previous_nonempty_line(text, line_start)
    if previous is None:
        return ""
    levels = _indent_levels(_leading_whitespace(previous))
    if previous.rstrip().endswith(("{", "(", "[")):
        levels += 1
    return _indent_text(levels)


def _line_bounds(text: str, index: int) -> tuple[int, int]:
    index = max(0, min(index, len(text)))
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end < 0:
        end = len(text)
    return start, end


def _diff_cursor(old: str, new: str) -> int | None:
    """Caret after a one-character insert or delete, found by diffing."""
    if abs(len(new) - len(old)) != 1:
        return None
    prefix = 0
    limit = min(len(old), len(new))
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    return prefix + 1 if len(new) > len(old) else prefix


def transform_code_edit(old: str, new: str, cursor: int | None) -> tuple[str, int] | None:
    """Autocomplete brackets and indent after a single-character edit.

    Returns ``(text, caret)`` when the edit should be rewritten, or ``None``
    to keep ``new`` as typed. ``cursor`` is the caret after the edit; when it
    is missing or does not match the edit (a stale selection), the edit
    position is found by diffing instead.
    """
    if cursor is not None:
        cursor = max(0, min(int(cursor), len(new)))
        if _edit_matches(old, new, cursor):
            return _transform_at(old, new, cursor)
    fallback = _diff_cursor(old, new)
    if fallback is None:
        return None
    return _transform_at(old, new, fallback)


def _edit_matches(old: str, new: str, cursor: int) -> bool:
    if len(new) == len(old) + 1:
        return cursor > 0 and new[: cursor - 1] + new[cursor:] == old
    if len(new) == len(old) - 1:
        return cursor <= len(old) - 1 and new[:cursor] + old[cursor] + new[cursor:] == old
    return False


def _transform_at(old: str, new: str, cursor: int) -> tuple[str, int] | None:
    if len(new) == len(old) + 1 and cursor > 0 and new[: cursor - 1] + new[cursor:] == old:
        typed = new[cursor - 1]
        closer = _BRACKET_PAIRS.get(typed)
        if closer is not None:
            return new[:cursor] + closer + new[cursor:], cursor
        if typed in _BRACKET_PAIRS.values() and cursor < len(new) and new[cursor] == typed:
            # The closer was already inserted; step over it instead of doubling it.
            return new[: cursor - 1] + new[cursor:], cursor
        if typed == "\n":
            return _indent_after_newline(new, cursor)
        return None

    if (
        len(new) == len(old) - 1
        and cursor <= len(old) - 1
        and new[:cursor] + old[cursor] + new[cursor:] == old
    ):
        deleted = old[cursor]
        closer = _BRACKET_PAIRS.get(deleted)
        if closer is not None and cursor < len(new) and new[cursor] == closer:
            return new[:cursor] + new[cursor + 1 :], cursor
    return None


def _indent_after_newline(text: str, cursor: int) -> tuple[str, int]:
    """``cursor`` sits just after the newline that was inserted."""
    line_start = text.rfind("\n", 0, cursor - 1) + 1
    previous = text[line_start : cursor - 1]
    levels = _indent_levels(_leading_whitespace(previous))
    opened = previous.rstrip().endswith(("{", "(", "["))
    if opened:
        levels += 1
    indent = _indent_text(levels)
    rest = text[cursor:]
    if opened and rest[:1] in {")", "]", "}"}:
        outer = _indent_text(levels - 1)
        updated = text[:cursor] + indent + "\n" + outer + rest
        return updated, cursor + len(indent)
    updated = text[:cursor] + indent + rest
    return updated, cursor + len(indent)


def apply_tab_edit(text: str, cursor: int, *, shift: bool) -> tuple[str, int]:
    """Insert or remove one indent level at the caret's line."""
    cursor = max(0, min(int(cursor), len(text)))
    line_start, line_end = _line_bounds(text, cursor)
    line = text[line_start:line_end]
    column = cursor - line_start
    whitespace = _leading_whitespace(line)
    body = line[len(whitespace) :]
    levels = _indent_levels(whitespace)

    if shift:
        if levels <= 0:
            return text, cursor
        new_ws = _indent_text(levels - 1)
    elif column <= len(whitespace):
        target = _indent_levels(_smart_indent(text, line_start))
        if levels >= target:
            target = levels + 1
        new_ws = _indent_text(target)
    else:
        updated = text[:cursor] + _INDENT + text[cursor:]
        return updated, cursor + len(_INDENT)

    new_line = new_ws + body
    updated = text[:line_start] + new_line + text[line_end:]
    if column <= len(whitespace):
        new_cursor = line_start + len(new_ws)
    else:
        new_cursor = line_start + len(new_ws) + (column - len(whitespace))
    return updated, new_cursor


def _format_num(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _num_field(
    label: str,
    value: float,
    on_apply,
    width: float = 92,
    clamp_field: bool = False,
    on_commit=None,
) -> ft.TextField:
    """Keep the field blank while editing; treat empty as 0 on blur."""
    commit = on_commit or on_apply

    def handle_change(e):
        raw = (e.control.value or "").strip()
        if raw in ("", "-", ".", "-."):
            return
        try:
            v = float(raw)
        except ValueError:
            return
        if clamp_field:
            v = clamp_field_coord(v)
        on_apply(v)

    def handle_blur(e):
        raw = (e.control.value or "").strip()
        if raw in ("", "-", ".", "-."):
            e.control.value = "0"
            commit(0.0)
        else:
            try:
                v = float(raw)
            except ValueError:
                e.control.value = "0"
                commit(0.0)
            else:
                if clamp_field:
                    v = clamp_field_coord(v)
                e.control.value = _format_num(v)
                commit(v)
        if _is_mounted(e.control):
            e.control.update()

    return ft.TextField(
        label=label,
        value=_format_num(value),
        on_change=handle_change,
        on_blur=handle_blur,
        width=width,
        text_size=13,
        text_style=MONO_TEXT_STYLE,
        border=ft.InputBorder.OUTLINE,
        border_width=1.5,
        border_color=ft.Colors.OUTLINE,
        focused_border_width=2,
        focused_border_color=ACCENT_BORDER,
    )


def _tag_line(label: str, value: str, size: int = 12, muted: bool = False) -> ft.Text:
    value_style = ft.TextStyle(
        size=size,
        color=ft.Colors.ON_SURFACE_VARIANT if muted else None,
    )
    return ft.Text(
        spans=[
            ft.TextSpan(f"{label}: ", style=ft.TextStyle(weight=ft.FontWeight.BOLD, size=size)),
            ft.TextSpan(value, style=value_style),
        ],
    )


def _title_with_divider(text: str, size: int) -> ft.Column:
    return ft.Column(
        spacing=8,
        tight=True,
        controls=[
            title_text(text, size=size),
            ft.Divider(height=1),
        ],
    )


_LIST_CARD_PADDING = ft.Padding.symmetric(horizontal=10, vertical=6)


def _time_label(ms: float) -> ft.Text:
    return ft.Text(f"{ms:.0f} ms", size=12, color=ft.Colors.ON_SURFACE_VARIANT)


def _compact_icon_button(icon, *, color, tooltip: str, on_click, disabled: bool = False) -> ft.IconButton:
    return ft.IconButton(
        icon=icon,
        icon_color=color,
        icon_size=18,
        padding=4,
        visual_density=ft.VisualDensity.COMPACT,
        tooltip=tooltip,
        disabled=disabled,
        on_click=on_click,
    )


def _list_row(action, card: ft.Control, *, reorderable: bool, delete_tooltip: str) -> ft.Row:
    """Drag handle + card + delete button, laid out as one action-list entry."""
    controls: list[ft.Control] = []
    if reorderable:
        controls.append(
            ft.ReorderableDragHandle(
                content=ft.Icon(ft.Icons.DRAG_HANDLE, size=20, color=ft.Colors.ON_SURFACE_VARIANT),
            )
        )
    controls.append(card)
    if action.deletable:
        controls.append(
            _compact_icon_button(
                ft.Icons.DELETE_OUTLINE,
                color=RED_LIGHT,
                tooltip=delete_tooltip,
                on_click=lambda e: action._handle_delete(e),
            )
        )
    return ft.Row(
        key=action.key,
        spacing=4,
        opacity=0.55 if action._dragging else 1.0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=controls,
    )


_GROUP_INDENT = ft.Margin.only(left=20)

# Copied actions or curves, shared across routines: {"kind": "actions" | "curves", "items": [...]}.
_clipboard: dict = {"data": None}


class _TextFocus:
    """The editor text field holding keyboard focus; list shortcuts yield to it."""

    field: ft.TextField | None = None


def _track_text_focus(control) -> None:
    """Wrap on_focus / on_blur of every TextField under ``control`` (idempotent)."""
    if isinstance(control, ft.TextField):
        if getattr(control.on_focus, "_tracks_text_focus", False):
            return
        inner_focus, inner_blur = control.on_focus, control.on_blur

        def on_focus(e, field=control):
            _TextFocus.field = field
            if inner_focus is not None:
                inner_focus(e)

        def on_blur(e, field=control):
            if _TextFocus.field is field:
                _TextFocus.field = None
            if inner_blur is not None:
                inner_blur(e)

        on_focus._tracks_text_focus = True
        control.on_focus, control.on_blur = on_focus, on_blur
        return
    for attr in ("content", "controls"):
        child = getattr(control, attr, None)
        for c in child if isinstance(child, list) else [child]:
            if isinstance(c, ft.Control):
                _track_text_focus(c)


def _menu_item(
    label: str, icon, data, shortcut: str | None, disabled: bool, danger: bool = False
) -> ft.PopupMenuItem:
    color = ft.Colors.RED_400 if danger and not disabled else None
    text = ft.Text(label, size=13, color=color)
    if danger:
        icon = ft.Icon(icon, color=color)
    if shortcut:
        content = ft.Row(
            width=190,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[text, ft.Text(shortcut, size=12, color=ft.Colors.ON_SURFACE_VARIANT)],
        )
    else:
        content = text
    return ft.PopupMenuItem(content=content, icon=icon, height=36, disabled=disabled, data=data)


# Caret between two characters so both Backspace and forward Delete have
# something to remove (and so count as handled).
_KEY_SINK_TEXT = "\u200b\u200b"
_KEY_SINK_SELECTION = ft.TextSelection(base_offset=1, extent_offset=1)


def _delete_entry(count: int, disabled: bool) -> tuple:
    label = f"Delete {count} items" if count > 1 else "Delete"
    return (label, ft.Icons.DELETE_OUTLINE, ("delete",), None, disabled, True)


class _ListMenu:
    """Right-click menu around a list card.

    ``sync`` takes ``(label, icon, data, shortcut, disabled[, danger])`` tuples
    (None for a divider) and only replaces the items when they change, so a rebuild while
    the menu is open keeps the item ids the open menu reports.
    """

    def __init__(self, content: ft.Control, *, expand: bool, on_choose):
        self._spec: tuple | None = None
        self._on_choose = on_choose
        self.control = ft.ContextMenu(expand=expand, content=content, on_select=self._select)

    def sync(self, spec: list) -> None:
        spec = tuple(spec)
        if spec == self._spec:
            return
        self._spec = spec
        self.control.secondary_items = [
            _menu_item(*entry) if entry is not None else ft.PopupMenuItem() for entry in spec
        ]
        if _is_mounted(self.control):
            self.control.update()

    def _select(self, e: ft.ContextMenuSelectEvent):
        items = self.control.secondary_items
        chosen = next(
            (it for it in items if e.item_id is not None and getattr(it, "_i", None) == e.item_id),
            None,
        )
        if chosen is None and e.item_index is not None and 0 <= e.item_index < len(items):
            chosen = items[e.item_index]
        if chosen is not None and chosen.data is not None:
            self._on_choose(chosen.data)


class ActionGroup:
    """A named, collapsible run of consecutive actions.

    Membership lives on each action (``action.group``); the list stays flat so
    playback, export and parsing ignore groups entirely.
    """

    deletable = True

    def __init__(self, name: str = "Group", collapsed: bool = False):
        self.name = name
        self.collapsed = collapsed
        self.key = f"group-{id(self)}"
        self.on_click = None
        self.on_toggle = None
        self.on_menu = None
        self.on_delete = None
        self.on_hover_change = None
        self.menu: _ListMenu | None = None
        self._hovering = False
        self._dragging = False
        self._card: ft.Container | None = None
        self._row: ft.Row | None = None

    def _handle_delete(self, e):
        if self.on_delete is not None:
            self.on_delete(self)

    def _handle_hover(self, e):
        if self.on_hover_change is not None:
            self.on_hover_change(self, str(e.data).lower() == "true")

    def build_header(self, *, selected: bool, count: int, time_ms: float) -> ft.Control:
        if self._card is None:
            self._card = ft.Container(
                expand=True,
                padding=_LIST_CARD_PADDING,
                border_radius=8,
                on_click=lambda e: self.on_click and self.on_click(self),
                on_hover=self._handle_hover,
            )
            self.menu = _ListMenu(
                self._card,
                expand=True,
                on_choose=lambda data: self.on_menu and self.on_menu(self, data),
            )
            self._row = _list_row(self, self.menu.control, reorderable=True, delete_tooltip="Delete group")
        if selected:
            self._card.bgcolor, self._card.border = ACCENT_CONTAINER, ft.Border.all(2, ACCENT_BORDER)
        elif self._hovering:
            self._card.bgcolor, self._card.border = ft.Colors.SURFACE_CONTAINER_HIGH, ft.Border.all(2, ft.Colors.OUTLINE)
        else:
            self._card.bgcolor = ft.Colors.SURFACE_CONTAINER_HIGHEST
            self._card.border = ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
        # Separate tap target so collapsing doesn't also select the group.
        chevron = ft.Container(
            width=22,
            height=22,
            border_radius=11,
            alignment=ft.Alignment(0, 0),
            tooltip="Expand" if self.collapsed else "Collapse",
            on_click=lambda e: self.on_toggle and self.on_toggle(self),
            content=ft.Icon(
                ft.Icons.CHEVRON_RIGHT if self.collapsed else ft.Icons.EXPAND_MORE,
                size=18,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        )
        self._card.content = ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                chevron,
                ft.Icon(
                    ft.Icons.FOLDER if self.collapsed else ft.Icons.FOLDER_OPEN,
                    size=18,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Container(expand=True, content=title_text(self.name, size=TITLE_SIZE_LIST)),
                ft.Text(
                    f"{count} item{'s' if count != 1 else ''}",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                _time_label(time_ms),
            ],
        )
        self._row.opacity = 0.55 if self._dragging else 1.0
        if _is_mounted(self._card):
            self._card.update()
        return self._row


def normalize_groups(actions: list) -> None:
    """Keep every group one consecutive run (Start is never grouped).

    A run separated from the rest of its group gets its own copy of the group.
    """
    seen: set[int] = set()
    run_src = run_dst = None
    for action in actions:
        group = None if isinstance(action, StartAction) else getattr(action, "group", None)
        if group is None:
            action.group = None
            run_src = run_dst = None
            continue
        if group is run_src:
            action.group = run_dst
            continue
        run_src = group
        run_dst = group if id(group) not in seen else ActionGroup(group.name, group.collapsed)
        seen.add(id(group))
        seen.add(id(run_dst))
        action.group = run_dst


def default_curve_points(previous_pose: tuple[float, float, float] | None) -> list[tuple[float, float]]:
    """Default cubic bezier with control points every 6\" along the previous heading."""
    step = 6.0

    if previous_pose is None:
        px, py, heading = 24.0, 24.0, 0.0
    else:
        px, py, heading = previous_pose

    h = radians(heading)
    return [
        clamp_field_point(px, py),
        clamp_field_point(px + cos(h) * step, py + sin(h) * step),
        clamp_field_point(px + cos(h) * step * 2, py + sin(h) * step * 2),
        clamp_field_point(px + cos(h) * step * 3, py + sin(h) * step * 3),
    ]


class CanvasCurve:
    """Canvas/render handle for one curve within a motion."""

    def __init__(self, motion_action: "MotionAction", curve_index: int):
        self.motion_action = motion_action
        self.curve_index = curve_index
        # Drawn faded when another motion holds the selection.
        self.dimmed = False

    @property
    def curve(self) -> Curve:
        curves = self.motion_action.motion.curves
        if not curves:
            raise IndexError("motion has no curves")
        index = min(max(self.curve_index, 0), len(curves) - 1)
        return curves[index]

    def canvas_color(self, selected: bool) -> str:
        return ACCENT if selected else ACCENT_LIGHT


class RobotAction:
    type_name = "Action"
    icon = ft.Icons.CIRCLE
    deletable = True

    def __init__(self, name: str, time: float = 0.0):
        self.name = name
        self.time = time
        self.selected = False
        self.key = str(id(self))
        self.on_select = None
        self.on_delete = None
        self.on_change = None
        self.on_hover_change = None
        self.on_menu = None
        self.menu: _ListMenu | None = None
        self.group: ActionGroup | None = None
        self._hovering = False
        self._dragging = False
        self._list_index = 0
        self._item = None
        self._row = None

    def summary_controls(self) -> list:
        return []

    def build_editor(self, previous_action: "RobotAction | None") -> ft.Control | None:
        return None

    def end_pose(self):
        return None

    def canvas_color(self) -> str:
        return ft.Colors.YELLOW

    def _bgcolor(self) -> str:
        if self.selected:
            return ACCENT_CONTAINER
        if self._hovering:
            return ft.Colors.SURFACE_CONTAINER_HIGH
        return ft.Colors.SURFACE_CONTAINER_HIGHEST

    def _border(self):
        if self.selected:
            return ft.Border.all(2, ACCENT_BORDER)
        if self._hovering:
            return ft.Border.all(2, ft.Colors.OUTLINE)
        return ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)

    def _list_trailing(self) -> ft.Control | None:
        """Right-aligned value next to the title in list cards."""
        return None

    def _list_header_row(self, leading: ft.Control | None = None) -> ft.Row:
        trailing = self._list_trailing()
        return ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                leading or ft.Icon(self.icon, size=18, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Container(expand=True, content=title_text(self.name, size=TITLE_SIZE_LIST)),
                *([trailing] if trailing is not None else []),
            ],
        )

    def _with_context_menu(self, content: ft.Control, *, expand: bool = True) -> ft.Control:
        """Right-click menu; ActionList fills in the items (see ``_menu_spec``)."""
        self.menu = _ListMenu(
            content,
            expand=expand,
            on_choose=lambda data: self.on_menu and self.on_menu(self, data),
        )
        return self.menu.control

    def build_list_item(self, reorderable: bool = False) -> ft.Control:
        # Reuse the same controls across rebuilds: replacing the card while
        # the pointer is over it (hover triggers a rebuild) swallows clicks.
        if self._item is None:
            self._item = ft.Container(
                expand=True,
                padding=_LIST_CARD_PADDING,
                border_radius=8,
                on_click=self._handle_click,
                on_hover=self._handle_hover,
            )
            self._row = _list_row(
                self,
                self._with_context_menu(self._item),
                reorderable=reorderable,
                delete_tooltip="Delete",
            )
        self._item.bgcolor = self._bgcolor()
        self._item.border = self._border()
        self._item.content = self._list_header_row()
        self._row.opacity = 0.55 if self._dragging else 1.0
        if _is_mounted(self._item):
            self._item.update()
        return self._row

    def _notify_change(self, *, commit: bool = False):
        if self.on_change is not None:
            self.on_change(self, commit=commit)

    def _handle_click(self, e):
        if self.on_select is not None:
            self.on_select(self)

    def _handle_hover(self, e):
        hovering = str(e.data).lower() == "true"
        if self.on_hover_change is not None:
            self.on_hover_change(self, hovering)

    def _handle_delete(self, e):
        if self.on_delete is not None:
            self.on_delete(self)


class StartAction(RobotAction):
    type_name = "Start"
    icon = ft.Icons.MY_LOCATION
    deletable = False

    def __init__(self, x: float = 24.0, y: float = 24.0, heading: float = 0.0, name: str = "Start"):
        super().__init__(name)
        self.x = clamp_field_coord(x)
        self.y = clamp_field_coord(y)
        self.heading = heading

    def end_pose(self):
        return (self.x, self.y, self.heading)

    def _list_trailing(self) -> ft.Control:
        return _time_label(self.time)

    def build_editor(self, previous_action) -> ft.Control:
        def apply(attr, clamp=False):
            def set_value(v):
                if clamp:
                    v = clamp_field_coord(v)
                setattr(self, attr, v)

            def on_apply(v):
                set_value(v)
                self._notify_change(commit=False)

            def on_commit(v):
                set_value(v)
                self._notify_change(commit=True)

            return on_apply, on_commit

        x_apply, x_commit = apply("x", clamp=True)
        y_apply, y_commit = apply("y", clamp=True)
        heading_apply, heading_commit = apply("heading")
        time_apply, time_commit = apply("time")

        return ft.Column(
            spacing=10,
            controls=[
                _title_with_divider("Starting pose", TITLE_SIZE_SECTION),
                ft.Row(
                    spacing=8,
                    controls=[
                        _num_field("X (in)", self.x, x_apply, on_commit=x_commit, clamp_field=True),
                        _num_field("Y (in)", self.y, y_apply, on_commit=y_commit, clamp_field=True),
                        _num_field("θ (deg)", self.heading, heading_apply, on_commit=heading_commit),
                        _num_field("Time (ms)", self.time, time_apply, on_commit=time_commit),
                    ],
                ),
            ],
        )


class MotionAction(RobotAction):
    type_name = "Motion"
    icon = ft.Icons.ROUTE

    def __init__(
        self,
        motion: Motion,
        motion_id: int = 1,
        code: str = "",
        timeout: float | None = None,
    ):
        self.motion_id = max(1, int(motion_id))
        super().__init__(f"Motion {self.motion_id}")
        self.motion = motion
        # ms, math.inf, or None to follow the profile time. Export-only: the
        # simulation always runs for the profile time.
        self.timeout = timeout
        self.on_timeout_change = None
        # Runs concurrently with the drive (joined with drive_along on export).
        self.code = code
        self.on_code_change = None
        self.code_editor = CodeEditor(
            lambda: self.code,
            lambda text: setattr(self, "code", text),
            self._code_edited,
            hint="action1();",
            min_lines=5,
            max_lines=12,
        )
        self.expanded = False
        self.on_select_motion = None
        self.on_select_curve = None
        self.on_add_curve = None
        self.on_delete_curve = None
        self.on_reorder_curves = None
        self.on_reorder_curves_start = None
        self.on_reorder_curves_end = None
        # (motion, curve_index) -> menu spec, and (motion, curve_index, data) on choose.
        self.curve_menu_spec = None
        self.on_curve_menu = None
        self._motion_card: ft.Container | None = None
        self._curves_panel: ft.Container | None = None
        self._curves_reorder_list: ft.ReorderableListView | None = None
        self._list_row: ft.Row | None = None
        self._dragging_curve_index: int | None = None
        self.recompute_profile()

    def set_motion_id(self, motion_id: int):
        self.motion_id = max(1, int(motion_id))
        self.name = f"Motion {self.motion_id}"

    def _code_edited(self, commit: bool):
        if self.on_code_change is not None:
            self.on_code_change(self, commit=commit)

    def _timeout_edited(self, commit: bool):
        if self.on_timeout_change is not None:
            self.on_timeout_change(self, commit=commit)

    def recompute_profile(self) -> None:
        self.time = profile_motion(self.motion, f"motion_{self.motion_id}")

    def _list_trailing(self) -> ft.Control:
        return _time_label(self.time)

    @property
    def effective_timeout(self) -> float:
        return self.time if self.timeout is None else self.timeout

    def canvas_color(self) -> str:
        return ACCENT_LIGHT

    def end_pose(self):
        pose = self.motion.end_pose()
        if pose is None:
            return None
        x, y, heading_deg = pose
        return (x, y, heading_deg)

    def previous_pose_for_curve(self, curve_index: int, previous_action: "RobotAction | None"):
        if curve_index > 0:
            return self.motion.curves[curve_index - 1].end_pose()
        if previous_action is not None:
            return previous_action.end_pose()
        return None

    def _curve_bgcolor(self, curve_index: int, selected_curves: set[int]) -> str:
        if curve_index in selected_curves:
            return ACCENT_CONTAINER
        return ft.Colors.SURFACE_CONTAINER_HIGH

    def _curve_border(self, curve_index: int, selected_curves: set[int]):
        if curve_index in selected_curves:
            return ft.Border.all(2, ACCENT_BORDER)
        return ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)

    def _curve_summary(self, curve: Curve, curve_index: int, duration_ms: float) -> ft.Control:
        return ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(f"Curve {curve_index + 1}", size=13, weight=ft.FontWeight.BOLD, expand=True),
                ft.Text(f"{curve.max_speed:.0f}%", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                _time_label(duration_ms),
            ],
        )

    def _make_curve_row(
        self,
        curve: Curve,
        curve_index: int,
        selected_curves: set[int],
        on_curve_click,
        on_delete_curve,
        duration_ms: float,
    ) -> ft.Control:
        def handle_click(e, index=curve_index):
            on_curve_click(index)

        def handle_menu(data, index=curve_index):
            if self.on_curve_menu is not None:
                self.on_curve_menu(self, index, data)

        card = ft.Container(
            expand=True,
            padding=_LIST_CARD_PADDING,
            border_radius=8,
            bgcolor=self._curve_bgcolor(curve_index, selected_curves),
            border=self._curve_border(curve_index, selected_curves),
            on_click=handle_click,
            content=self._curve_summary(curve, curve_index, duration_ms),
        )
        menu = _ListMenu(card, expand=True, on_choose=handle_menu)
        if self.curve_menu_spec is not None:
            menu.sync(self.curve_menu_spec(self, curve_index))
        delete_btn = _compact_icon_button(
            ft.Icons.DELETE_OUTLINE,
            color=RED_LIGHT,
            tooltip="Delete curve",
            disabled=len(self.motion.curves) <= 1,
            on_click=lambda e, index=curve_index: on_delete_curve(index),
        )
        return ft.Row(
            key=curve.key,
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            opacity=0.55 if self._dragging_curve_index == curve_index else 1.0,
            controls=[
                ft.ReorderableDragHandle(
                    content=ft.Icon(
                        ft.Icons.DRAG_HANDLE,
                        size=18,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ),
                menu.control,
                delete_btn,
            ],
        )

    def _on_curves_reorder(self, e: ft.OnReorderEvent):
        if self.on_reorder_curves is not None:
            self.on_reorder_curves(self, e)

    def _on_curves_reorder_start(self, e: ft.OnReorderEvent):
        if self.on_reorder_curves_start is not None:
            self.on_reorder_curves_start(self, e)

    def _on_curves_reorder_end(self, e: ft.OnReorderEvent):
        if self.on_reorder_curves_end is not None:
            self.on_reorder_curves_end(self, e)

    def _sync_curves_panel(
        self,
        selected_curves: set[int],
        on_curve_click,
        on_delete_curve,
    ):
        if self._curves_reorder_list is None:
            self._curves_reorder_list = ft.ReorderableListView(
                spacing=6,
                padding=ft.Padding.only(left=8),
                show_default_drag_handles=False,
                on_reorder=self._on_curves_reorder,
                on_reorder_start=self._on_curves_reorder_start,
                on_reorder_end=self._on_curves_reorder_end,
            )

        if self.expanded:
            self._curves_panel.padding = ft.Padding.only(bottom=8)
            bounds = curve_boundaries(self.motion)
            durations = [b - a for a, b in zip(bounds, bounds[1:])]
            self._curves_reorder_list.controls = [
                self._make_curve_row(
                    curve,
                    i,
                    selected_curves,
                    on_curve_click,
                    on_delete_curve,
                    durations[i] if i < len(durations) else 0.0,
                )
                for i, curve in enumerate(self.motion.curves)
            ]
            self._curves_panel.content = self._curves_reorder_list
            self._curves_panel.opacity = 1.0
        else:
            self._curves_panel.padding = ft.Padding.all(0)
            self._curves_reorder_list.controls = []
            self._curves_panel.content = self._curves_reorder_list
            self._curves_panel.opacity = 0.0

    def _sync_motion_card(
        self,
        selected_curves: set[int],
        on_motion_click,
        on_add_curve,
        on_curve_click,
        on_delete_curve,
    ):
        self._motion_card.bgcolor = self._bgcolor()
        self._motion_card.border = self._border()
        self._motion_card.on_click = on_motion_click
        chevron = ft.Icon(
            ft.Icons.EXPAND_MORE if self.expanded else ft.Icons.CHEVRON_RIGHT,
            size=18,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        header = self._list_header_row(leading=chevron)
        if self.expanded:
            # Fixed-size box so the button doesn't make the row taller.
            header.controls.append(
                ft.Container(
                    width=22,
                    height=22,
                    border_radius=11,
                    alignment=ft.Alignment(0, 0),
                    tooltip="Add curve",
                    on_click=on_add_curve,
                    content=ft.Icon(ft.Icons.ADD, size=18, color=ft.Colors.GREEN_ACCENT_400),
                )
            )
        self._motion_card.content = header
        self._sync_curves_panel(selected_curves, on_curve_click, on_delete_curve)

    def _ensure_list_row(
        self,
        reorderable: bool,
        selected_curves: set[int],
    ) -> ft.Control:
        def on_motion_click(e):
            if self.on_select_motion is not None:
                self.on_select_motion(self)

        def on_add_curve(e):
            if self.on_add_curve is not None:
                self.on_add_curve(self)

        def on_curve_click(index: int):
            if self.on_select_curve is not None:
                self.on_select_curve(self, index)

        def on_delete_curve(index: int):
            if self.on_delete_curve is not None:
                self.on_delete_curve(self, index)

        if self._curves_panel is None:
            self._curves_panel = ft.Container(
                animate_size=EASE_ANIM,
                animate_opacity=EASE_ANIM,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                opacity=0,
                content=ft.Column(spacing=6, tight=True),
            )

        if self._motion_card is None:
            self._motion_card = ft.Container(
                padding=_LIST_CARD_PADDING,
                border_radius=8,
                on_hover=self._handle_hover,
            )

        self._sync_motion_card(
            selected_curves, on_motion_click, on_add_curve, on_curve_click, on_delete_curve
        )

        if self._list_row is None:
            card_body = ft.Column(
                spacing=6,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    self._with_context_menu(self._motion_card, expand=False),
                    self._curves_panel,
                ],
            )
            card = ft.Container(expand=True, content=card_body)
            self._item = card
            self._list_row = _list_row(
                self, card, reorderable=reorderable, delete_tooltip="Delete motion"
            )
        else:
            self._list_row.opacity = 0.55 if self._dragging else 1.0
        # Keep the handle and delete button beside the header once curves show.
        self._list_row.vertical_alignment = (
            ft.CrossAxisAlignment.START if self.expanded else ft.CrossAxisAlignment.CENTER
        )

        if _is_mounted(self._curves_panel):
            self._curves_panel.update()
        if _is_mounted(self._curves_reorder_list):
            self._curves_reorder_list.update()
        if _is_mounted(self._motion_card):
            self._motion_card.update()

        return self._list_row

    def build_list_item(
        self,
        reorderable: bool = False,
        selected_curves: set[int] | None = None,
    ) -> ft.Control:
        return self._ensure_list_row(reorderable, selected_curves or set())

    def build_motion_editor(self) -> ft.Control:
        def g1_change(e):
            self.motion.g1_continuity = bool(e.control.value)
            self._notify_change(commit=True)

        def start_speed_apply(v):
            self.motion.start_speed = max(0.0, v)
            self._notify_change(commit=False)

        def start_speed_commit(v):
            self.motion.start_speed = max(0.0, v)
            self._notify_change(commit=True)

        def end_speed_apply(v):
            self.motion.end_speed = max(0.0, v)
            self._notify_change(commit=False)

        def end_speed_commit(v):
            self.motion.end_speed = max(0.0, v)
            self._notify_change(commit=True)

        def timeout_text() -> str:
            if self.timeout is None:
                return ""
            if math.isinf(self.timeout):
                return "inf"
            return _format_num(self.timeout)

        def parse_timeout(raw: str) -> tuple[bool, float | None]:
            text = raw.strip()
            if not text:
                return True, None
            value = timeout_from_json(text)
            return value is not None, value

        def timeout_change(e):
            ok, value = parse_timeout(e.control.value or "")
            if ok and value != self.timeout:
                self.timeout = value
                self._timeout_edited(commit=False)

        def timeout_blur(e):
            ok, value = parse_timeout(e.control.value or "")
            if ok:
                self.timeout = value
            e.control.value = timeout_text()
            if _is_mounted(e.control):
                e.control.update()
            self._timeout_edited(commit=True)

        timeout_field = ft.TextField(
            label="Timeout (ms)",
            value=timeout_text(),
            hint_text=f"{self.time:.0f} (profile time)",
            on_change=timeout_change,
            on_blur=timeout_blur,
            width=LEFT_PANEL_WIDTH,
            text_size=13,
            text_style=MONO_TEXT_STYLE,
            border=ft.InputBorder.OUTLINE,
            border_width=1.5,
            border_color=ft.Colors.OUTLINE,
            focused_border_width=2,
            focused_border_color=ACCENT_BORDER,
        )

        return ft.Column(
            spacing=10,
            controls=[
                ft.Text(
                    f"Motion id: {self.motion_id}",
                    size=13,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Row(
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text("G1 continuity", size=13),
                        ft.Switch(value=self.motion.g1_continuity, on_change=g1_change),
                    ],
                ),
                ft.Text(
                    "Aligns the first curve to the previous pose and keeps joins tangent-matched.",
                    size=11,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                _num_field(
                    "Start speed %",
                    self.motion.start_speed,
                    start_speed_apply,
                    on_commit=start_speed_commit,
                    width=LEFT_PANEL_WIDTH,
                ),
                _num_field(
                    "End speed %",
                    self.motion.end_speed,
                    end_speed_apply,
                    on_commit=end_speed_commit,
                    width=LEFT_PANEL_WIDTH,
                ),
                ft.Text(
                    "Speed at the first / final point. Capped by curvature and the "
                    "first / last curve's max speed; 0 starts from or ends at rest.",
                    size=11,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                timeout_field,
                ft.Text(
                    "Exported with the profile. Leave blank to use the profile time, "
                    "or enter inf for no timeout. Doesn't change the simulation.",
                    size=11,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                _title_with_divider("Code during motion", TITLE_SIZE_SECTION),
                ft.Text(
                    "Runs at the same time as this drive (joined with drive_along). "
                    "Tab indents, and brackets autocomplete.",
                    size=11,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                self.code_editor.build(),
            ],
        )

    def build_curve_editor(self, curve: Curve, curve_index: int, previous_action: "RobotAction | None") -> ft.Control:
        point_fields: list[tuple[ft.TextField, ft.TextField]] = []

        def point_apply(index, axis):
            def set_value(v):
                v = clamp_field_coord(v)
                x, y = curve.points[index]
                curve.points[index] = (v, y) if axis == 0 else (x, v)
                prev_pose = self.previous_pose_for_curve(curve_index, previous_action)
                self.motion.enforce_g1_after_edit(curve_index, index, previous_pose=prev_pose)

            def on_apply(v):
                set_value(v)
                self._notify_change(commit=False)

            def on_commit(v):
                set_value(v)
                self._notify_change(commit=True)

            return on_apply, on_commit

        def max_speed_apply(v):
            curve.max_speed = v
            self._notify_change(commit=False)

        def max_speed_commit(v):
            curve.max_speed = v
            self._notify_change(commit=True)

        def reversed_change(e):
            curve.reversed = bool(e.control.value)
            if self.motion.g1_continuity:
                prior = previous_action.end_pose() if previous_action is not None else None
                self.motion.enforce_g1(previous_pose=prior)
            self._notify_change(commit=True)

        point_rows = []
        for i, label in enumerate(POINT_LABELS):
            x, y = curve.points[i]
            px_apply, px_commit = point_apply(i, 0)
            py_apply, py_commit = point_apply(i, 1)
            fx = _num_field("X", x, px_apply, on_commit=px_commit, width=72, clamp_field=True)
            fy = _num_field("Y", y, py_apply, on_commit=py_commit, width=72, clamp_field=True)
            point_fields.append((fx, fy))
            point_rows.append(
                ft.Row(
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Container(width=28, content=ft.Text(label, size=12, weight=ft.FontWeight.BOLD)),
                        fx,
                        fy,
                    ],
                )
            )

        prev_pose = self.previous_pose_for_curve(curve_index, previous_action)

        def snap_to_previous(e):
            if prev_pose is None:
                return
            if self.motion.g1_continuity and curve_index > 0:
                apply_g1_forward(self.motion.curves[curve_index - 1], curve)
            elif self.motion.g1_continuity:
                apply_g1_from_pose(curve, prev_pose)
            else:
                px, py = clamp_field_point(prev_pose[0], prev_pose[1])
                curve.points[0] = (px, py)
            fx, fy = point_fields[0]
            fx.value = _format_num(curve.points[0][0])
            fy.value = _format_num(curve.points[0][1])
            if _is_mounted(fx):
                fx.update()
                fy.update()
            self._notify_change(commit=True)

        left_panel = ft.Column(
            spacing=10,
            controls=[
                _num_field(
                    "Max speed %",
                    curve.max_speed,
                    max_speed_apply,
                    on_commit=max_speed_commit,
                    width=LEFT_PANEL_WIDTH,
                ),
                ft.Row(
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text("Reversed", size=13),
                        ft.Switch(value=curve.reversed, on_change=reversed_change),
                    ],
                ),
                ft.OutlinedButton(
                    width=LEFT_PANEL_WIDTH,
                    on_click=snap_to_previous,
                    disabled=prev_pose is None,
                    style=OUTLINED_GREEN_STYLE,
                    content=ft.Row(
                        tight=True,
                        spacing=6,
                        controls=[
                            ft.Icon(ft.Icons.LINK, size=18),
                            ft.Text("Snap P0 to previous pose", size=13, no_wrap=True),
                        ],
                    ),
                ),
                _tag_line("Motion time", f"{self.time:.0f} ms (continuous profile)", muted=True),
            ],
        )

        right_panel = ft.Column(
            spacing=8,
            controls=[
                _title_with_divider("Control points (in)", TITLE_SIZE_SECTION),
                *point_rows,
            ],
        )

        return ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.START,
            controls=[
                ft.Container(width=LEFT_PANEL_WIDTH, content=left_panel),
                ft.Container(expand=True),
                ft.Container(alignment=ft.Alignment(1, -1), content=right_panel),
            ],
        )

    def build_editor(self, previous_action) -> ft.Control | None:
        return self.build_motion_editor()


_CODE_SIZE = 13
_CODE_PADDING = 12
# Flutter's editable text reserves cursor width + a 1px gap at line ends.
_CARET_MARGIN = 3
# Desktop's compact visual density pulls the TextField's text up by this much
# relative to its content padding; the highlight layer has to match.
_FIELD_DENSITY_SHIFT = 4
_CODE_LINE_RATIO = 1.35
_CODE_LINE_HEIGHT = _CODE_SIZE * _CODE_LINE_RATIO

CODE_DEFAULT = "#D4D4D4"
CODE_KEYWORD = "#569CD6"
CODE_CONTROL = "#C586C0"
CODE_FUNCTION = "#DCDCAA"
CODE_TYPE = "#4EC9B0"
CODE_FIELD = "#9CDCFE"
CODE_NUMBER = "#B5CEA8"
CODE_STRING = "#CE9178"
CODE_COMMENT = "#6A9955"
CODE_HINT = ft.Colors.with_opacity(0.32, ft.Colors.ON_SURFACE)

_KEYWORDS = {
    "let", "mut", "fn", "async", "move", "pub", "use", "ref", "const", "static",
    "struct", "enum", "impl", "trait", "as", "in", "where", "self", "Self",
    "true", "false", "crate", "super", "mod", "type", "unsafe", "dyn",
}
_CONTROL_KEYWORDS = {
    "if", "else", "for", "while", "loop", "match", "return", "await", "break", "continue",
}

_TOKEN_RE = re.compile(
    r"(?P<comment>//[^\n]*|/\*[\s\S]*?(?:\*/|$))"
    r"|(?P<string>\"(?:\\.|[^\"\\])*\"?)"
    r"|(?P<number>\b\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?(?:[iuf](?:8|16|32|64|128|size))?\b)"
    r"|(?P<ident>[A-Za-z_]\w*)"
)


def _ident_color(code: str, start: int, end: int, word: str) -> str:
    if word in _CONTROL_KEYWORDS:
        return CODE_CONTROL
    if word in _KEYWORDS:
        return CODE_KEYWORD
    rest = code[end:].lstrip(" \t")
    if rest.startswith("!"):
        return CODE_FUNCTION
    if rest.startswith("(") or rest.startswith("::<"):
        return CODE_FUNCTION
    if word[0].isupper():
        return CODE_TYPE
    if code[:start].rstrip(" \t").endswith("."):
        return CODE_FIELD
    return CODE_DEFAULT


def highlight_code(code: str) -> list[tuple[str, str]]:
    """Split Rust-like code into ``(text, color)`` runs for syntax coloring."""
    runs: list[tuple[str, str]] = []

    def push(text: str, color: str):
        if not text:
            return
        if runs and runs[-1][1] == color:
            runs[-1] = (runs[-1][0] + text, color)
        else:
            runs.append((text, color))

    pos = 0
    for match in _TOKEN_RE.finditer(code):
        push(code[pos : match.start()], CODE_DEFAULT)
        kind = match.lastgroup
        text = match.group()
        if kind == "comment":
            color = CODE_COMMENT
        elif kind == "string":
            color = CODE_STRING
        elif kind == "number":
            color = CODE_NUMBER
        else:
            color = _ident_color(code, match.start(), match.end(), text)
        push(text, color)
        pos = match.end()
    push(code[pos:], CODE_DEFAULT)
    return runs


class CodeEditor:
    """Monospace code box with syntax coloring, bracket autocomplete and Tab indenting.

    Flutter text fields cannot color individual characters, so the field's
    own text is transparent and a colored copy is drawn underneath it.

    ``on_edit(commit)`` fires after each edit; ``commit`` is True on blur / Tab.
    """

    def __init__(self, get_text, set_text, on_edit, *, hint: str = "", min_lines: int = 8, max_lines: int = 16):
        self._get_text = get_text
        self._set_text = set_text
        self._on_edit = on_edit
        self.hint = hint
        self.min_lines = min_lines
        self.max_lines = max_lines
        self.on_focus = None
        self.field: ft.TextField | None = None
        self._highlight: ft.Text | None = None
        self._frame: ft.Container | None = None
        self._scroller: ft.Column | None = None
        self.focused = False
        self._blur_at = 0.0
        self._tab_at = 0.0
        self._suppress = False
        self._last_text = get_text()
        self._caret = len(self._last_text)
        self._pending_caret: int | None = None
        self._pending_token = None

    def _store(self, text: str):
        self._last_text = text
        self._set_text(text)

    def is_active(self) -> bool:
        """Focused, or blurred just now by the Tab key that is being handled."""
        import time

        if self.field is None or not _is_mounted(self.field):
            return False
        return self.focused or (time.monotonic() - self._blur_at) < 0.5

    def _spans(self, text: str) -> list[ft.TextSpan]:
        return [ft.TextSpan(run, style=ft.TextStyle(color=color)) for run, color in highlight_code(text)]

    def _box_height(self, text: str) -> float | None:
        """Cap the box at ``max_lines``; past that it scrolls."""
        if text.count("\n") + 1 <= self.max_lines:
            return None
        return self.max_lines * _CODE_LINE_HEIGHT + 2 * _CODE_PADDING

    def _refresh_highlight(self, text: str):
        if self._highlight is not None:
            self._highlight.spans = self._spans(text)
            if _is_mounted(self._highlight):
                self._highlight.update()
        if self._scroller is not None:
            height = self._box_height(text)
            if self._scroller.height != height:
                self._scroller.height = height
                if _is_mounted(self._scroller):
                    self._scroller.update()

    def _set_frame_focus(self, focused: bool):
        if self._frame is None:
            return
        self._frame.border = ft.Border.all(1.5, ACCENT_BORDER if focused else ft.Colors.OUTLINE)
        if _is_mounted(self._frame):
            self._frame.update()

    def _apply(self, field: ft.TextField, text: str, caret: int):
        """Replace the text and place the caret.

        When the value changes, Flutter moves the caret to the end and reports
        that back, which overwrites any caret sent alongside the value. So the
        caret is sent only after that report arrives (or a short timeout).
        """
        field.value = text
        field.selection = None
        if _is_mounted(field):
            field.update()
        self._refresh_highlight(text)
        if caret >= len(text):
            self._pending_caret = None
            return
        self._pending_caret = caret
        token = object()
        self._pending_token = token

        async def fallback():
            import asyncio

            await asyncio.sleep(0.2)
            if self._pending_token is token and self._pending_caret is not None:
                self._send_caret(field)

        if field.page is not None:
            field.page.run_task(fallback)

    def _send_caret(self, field: ft.TextField):
        caret = self._pending_caret
        self._pending_caret = None
        if caret is None or not _is_mounted(field):
            return
        self._caret = caret
        field.selection = ft.TextSelection(base_offset=caret, extent_offset=caret)
        field.update()

    def _handle_focus(self, e):
        self.focused = True
        self._set_frame_focus(True)
        if self.on_focus is not None:
            self.on_focus(self)

    def _handle_blur(self, e):
        import time

        now = time.monotonic()
        self._blur_at = now
        # Tab moves focus before Python runs; ignore that blur so it cannot
        # revert the indent we just inserted.
        if self._suppress or now - self._tab_at < 0.3:
            return
        self.focused = False
        self._set_frame_focus(False)
        self._store(e.control.value or "")
        self._on_edit(True)

    def _handle_selection(self, e):
        if self._pending_caret is not None:
            # This is Flutter reporting the caret it jumped to the end.
            self._send_caret(e.control)
            return
        selection = e.control.selection
        if selection is not None and selection.is_valid:
            self._caret = selection.extent_offset

    def _handle_change(self, e):
        new = e.control.value or ""
        if self._suppress:
            self._store(new)
            self._refresh_highlight(new)
            return

        selection = e.control.selection
        cursor = None
        if selection is not None and selection.is_valid:
            cursor = selection.end
        updated = transform_code_edit(self._last_text, new, cursor)
        if updated is None:
            self._store(new)
            if cursor is not None:
                self._caret = cursor
            self._refresh_highlight(new)
            self._on_edit(False)
            return

        text, caret = updated
        self._suppress = True
        try:
            self._store(text)
            self._caret = caret
            self._apply(e.control, text, caret)
        finally:
            self._suppress = False
        self._on_edit(False)

    def build(self) -> ft.Control:
        text = self._get_text()
        self._last_text = text
        self._caret = len(text)
        self.focused = False
        # Both layers must share identical metrics (the field otherwise inherits
        # the theme's letter spacing and line height) or the caret drifts.
        code_style = ft.TextStyle(
            font_family=MONO_FONT,
            weight=ft.FontWeight.W_600,
            size=_CODE_SIZE,
            height=_CODE_LINE_RATIO,
            letter_spacing=0,
            word_spacing=0,
        )
        padding = ft.Padding.all(_CODE_PADDING)

        self._highlight = ft.Text(
            spans=self._spans(text),
            style=code_style,
            color=CODE_DEFAULT,
        )
        self.field = ft.TextField(
            value=text,
            multiline=True,
            min_lines=self.min_lines,
            text_size=_CODE_SIZE,
            text_style=code_style,
            color=ft.Colors.TRANSPARENT,
            cursor_color=ft.Colors.ON_SURFACE,
            hint_text=self.hint or None,
            hint_style=ft.TextStyle(
                font_family=MONO_FONT,
                size=_CODE_SIZE,
                height=_CODE_LINE_RATIO,
                letter_spacing=0,
                color=CODE_HINT,
            ),
            content_padding=padding,
            border=ft.InputBorder.NONE,
            autocorrect=False,
            enable_suggestions=False,
            smart_dashes_type=False,
            smart_quotes_type=False,
            capitalization=ft.TextCapitalization.NONE,
            on_change=self._handle_change,
            on_focus=self._handle_focus,
            on_blur=self._handle_blur,
            on_selection_change=self._handle_selection,
        )
        layers = ft.Stack(
            expand=True,
            fit=ft.StackFit.PASS_THROUGH,
            controls=[
                ft.Container(
                    padding=ft.Padding.only(
                        left=_CODE_PADDING,
                        top=_CODE_PADDING - _FIELD_DENSITY_SHIFT,
                        right=_CODE_PADDING + _CARET_MARGIN,
                        bottom=_CODE_PADDING,
                    ),
                    content=self._highlight,
                ),
                self.field,
            ],
        )
        self._scroller = ft.Column(
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            height=self._box_height(text),
            controls=[ft.Row(spacing=0, controls=[layers])],
        )
        self._frame = ft.Container(
            border_radius=6,
            border=ft.Border.all(1.5, ft.Colors.OUTLINE),
            bgcolor=ft.Colors.with_opacity(0.25, ft.Colors.BLACK),
            content=self._scroller,
        )
        return self._frame

    def handle_tab(self, shift: bool):
        """Rewrite the indent and pull focus back onto the box.

        Tab moves focus before Python sees the key, so this runs after the
        field blurs and then focuses it again.
        """
        import time

        field = self.field
        if field is None:
            return
        text = field.value if field.value is not None else self._get_text()
        text = text or ""
        cursor = self._caret
        selection = field.selection
        if selection is not None and selection.is_valid:
            cursor = selection.end
        cursor = max(0, min(cursor, len(text)))
        new_text, new_cursor = apply_tab_edit(text, cursor, shift=shift)

        self._tab_at = time.monotonic()
        self._suppress = True
        self._store(new_text)
        self._caret = new_cursor

        async def finish():
            try:
                await field.focus()
                self._apply(field, new_text, new_cursor)
            finally:
                self._suppress = False
                self.focused = True
                self._set_frame_focus(True)

        if field.page is not None:
            field.page.run_task(finish)
        else:
            field.value = new_text
            self._suppress = False
        self._on_edit(True)


class CodeAction(RobotAction):
    """Custom robot code plus a wait, run on its own between motions."""

    type_name = "Action"
    icon = ft.Icons.CODE

    def __init__(self, code: str = "", time: float = 0.0, name: str = "Action"):
        super().__init__(name, time=max(0.0, float(time)))
        self.code = code
        self.action_id = 1
        self.on_code_change = None
        self.code_editor = CodeEditor(
            lambda: self.code,
            lambda text: setattr(self, "code", text),
            self._code_edited,
            hint="action1();",
        )

    def _code_edited(self, commit: bool):
        if self.on_code_change is not None:
            self.on_code_change(self, commit=commit)

    def set_action_id(self, action_id: int):
        self.action_id = max(1, int(action_id))
        self.name = f"Action {self.action_id}"

    def _list_trailing(self) -> ft.Control:
        return _time_label(self.time)

    def build_editor(self, previous_action) -> ft.Control:
        def time_apply(v):
            self.time = max(0.0, float(v))
            self._notify_change(commit=False)

        def time_commit(v):
            self.time = max(0.0, float(v))
            self._notify_change(commit=True)

        return ft.Column(
            spacing=10,
            controls=[
                _title_with_divider("Robot code", TITLE_SIZE_SECTION),
                ft.Text(
                    "Runs on its own, after the previous motion finishes. "
                    "Tab indents, and brackets autocomplete.",
                    size=11,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                self.code_editor.build(),
                _num_field(
                    "Time (ms)",
                    self.time,
                    time_apply,
                    on_commit=time_commit,
                    width=120,
                ),
            ],
        )


class ActionList:
    def __init__(
        self,
        page: ft.Page,
        on_scene_change=None,
        on_playback_pose=None,
        history: ActionHistory | None = None,
        on_dirty=None,
        on_save=None,
        on_export=None,
    ):
        self.page = page
        self.on_scene_change = on_scene_change
        self.on_playback_pose = on_playback_pose
        self.on_dirty = on_dirty
        self.on_save = on_save
        self.on_export = on_export
        self.history = history or ActionHistory()
        self._restoring = False
        self._dirty = False
        self.actions: list[RobotAction] = actions_from_json(self.history.current)
        # ``selected`` / ``_selected_curve`` are the primary (editor) selection;
        # ``_multi`` / ``_multi_curves`` hold everything shift-selected with it.
        self.selected: RobotAction | None = None
        self._selected_curve: tuple[MotionAction, int] | None = None
        self._multi: list[RobotAction] = []
        self._multi_curves: list[int] = []
        self._selected_group: ActionGroup | None = None
        # Display rows of the list, ("action", action) or ("group", group).
        self._display: list[tuple[str, object]] = []
        self._code_focus: CodeEditor | None = None
        self._modal: ft.Container | None = None
        self._timeline = Timeline()
        self._play_time_ms = 0.0
        self._playing = False
        self._playback_task = None
        self._slider_dragging = False

        self._editor_title_text = title_text("", size=TITLE_SIZE_EDITOR)
        self._editor_body = ft.Container(
            opacity=1.0,
            animate_opacity=EASE_ANIM,
            on_animation_end=self._on_editor_fade_end,
        )
        self._editor_inner = ft.Column(
            spacing=8,
            tight=True,
            controls=[
                ft.Column(
                    spacing=8,
                    tight=True,
                    controls=[
                        self._editor_title_text,
                        ft.Divider(height=1),
                    ],
                ),
                self._editor_body,
            ],
        )

        self._editor_shell = ft.Container(
            padding=14,
            border_radius=10,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            content=self._editor_inner,
        )
        self._pending_editor_apply: tuple[str, ft.Control | None] | None = None

        self._reorder_list = ft.ReorderableListView(
            expand=True,
            spacing=8,
            padding=0,
            show_default_drag_handles=False,
            on_reorder=self._handle_reorder,
            on_reorder_start=self._handle_reorder_start,
            on_reorder_end=self._handle_reorder_end,
        )

        self._routine_tag = ft.Text(
            "",
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self._save_status = ft.Text(
            "All changes saved",
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self._undo_button = ft.IconButton(
            icon=ft.Icons.UNDO,
            icon_size=18,
            tooltip="Undo (⌘Z)",
            on_click=lambda e: self.undo(),
            disabled=True,
        )
        self._redo_button = ft.IconButton(
            icon=ft.Icons.REDO,
            icon_size=18,
            tooltip="Redo (⇧⌘Z)",
            on_click=lambda e: self.redo(),
            disabled=True,
        )
        self._save_button = blue_button(
            "Save",
            icon=ft.Icons.SAVE,
            tooltip="Save (⌘S)",
            on_click=lambda e: self.save(),
            disabled=True,
        )
        self._export_button = green_button(
            "Export",
            icon=ft.Icons.UPLOAD_FILE,
            tooltip="Save, export macro, and write motion profiles",
            on_click=lambda e: self.export(),
        )
        self._play_button = ft.IconButton(
            icon=ft.Icons.PLAY_ARROW,
            icon_color=ft.Colors.GREEN_ACCENT_400,
            tooltip="Play routine",
            on_click=self._toggle_playback,
        )
        self._play_selection_button = ft.IconButton(
            icon=ft.Icons.PLAY_ARROW,
            icon_color=ACCENT_LIGHT,
            tooltip="Play selected motion",
            visible=False,
            on_click=self._toggle_selection_playback,
        )
        # End of the window being played; None plays to the routine's end.
        self._play_end_ms: float | None = None
        self._time_label = ft.Text("0.00 s", size=12, font_family=MONO_FONT, weight=ft.FontWeight.W_600)
        self._duration_label = ft.Text("/ 0.00 s", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._time_slider = ft.Slider(
            min=0,
            max=1,
            value=0,
            divisions=None,
            active_color=ACCENT,
            inactive_color=ft.Colors.OUTLINE_VARIANT,
            thumb_color=ACCENT_LIGHT,
            on_change=self._on_slider_change,
            on_change_start=self._on_slider_drag_start,
            on_change_end=self._on_slider_drag_end,
            expand=True,
        )
        self._simulate_bar = ft.Container(
            padding=ft.Padding.only(top=8),
            content=ft.Column(
                spacing=6,
                tight=True,
                controls=[
                    ft.Divider(height=1),
                    title_text("Simulate", size=16),
                ft.Row(
                        spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                            self._play_button,
                            self._play_selection_button,
                            self._time_label,
                            self._duration_label,
                        ],
                    ),
                    self._time_slider,
                ],
            ),
        )
        # The header scrolls with the action list, so a tall editor never
        # pushes the list off-screen. Reorder indices exclude the header.
        self._reorder_list.header = ft.Container(
            padding=ft.Padding.only(bottom=8),
            content=ft.Column(
                spacing=12,
                tight=True,
                controls=[
                    ft.Column(
                        spacing=2,
                        tight=True,
                        controls=[
                            title_text("Actions", size=22),
                            self._routine_tag,
                        ],
                    ),
                    self._editor_shell,
                    ft.Divider(),
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            green_button(
                                "New action",
                                icon=ft.Icons.ADD,
                                on_click=self._show_new_action_dialog,
                            ),
                            self._save_button,
                            self._export_button,
                            self._undo_button,
                            self._redo_button,
                        ],
                    ),
                    self._save_status,
                ],
            ),
        )
        # Only gets keys while focus is inside the list (sidebar fields don't
        # reach it); entry clicks move focus here.
        # Holds focus for the list. The listener above it still sees every key
        # first, but a text field then claims edit keys (Delete, ⌘V, …) so macOS
        # doesn't play its "unhandled key" beep. Edits to it are undone at once.
        self._key_sink = ft.TextField(
            value=_KEY_SINK_TEXT,
            selection=_KEY_SINK_SELECTION,
            on_change=self._reset_key_sink,
            border=ft.InputBorder.NONE,
            show_cursor=False,
            enable_interactive_selection=False,
            autocorrect=False,
            enable_suggestions=False,
            text_size=1,
        )
        self._keys = ft.KeyboardListener(
            on_key_down=self._on_list_key_down,
            content=ft.Stack(
                expand=True,
                controls=[
                    ft.Container(
                        left=0, top=0, width=1, height=1, opacity=0,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        content=self._key_sink,
                    ),
                    ft.Container(left=0, top=0, right=0, bottom=0, content=self._reorder_list),
                ],
            ),
        )
        self._actions_section = ft.Container(
            expand=True,
            padding=16,
            border_radius=12,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            content=ft.Column(
                spacing=12,
                expand=True,
                controls=[
                    # KeyboardListener ignores `expand`.
                    ft.Container(expand=True, content=self._keys),
                    self._simulate_bar,
                ],
            ),
        )

        # Outer chrome (padding / page background) is owned by Sidebar.
        self.control = self._actions_section

        # Align history.current with the live list (profiles recomputed on load).
        self.history.replace_current(self.snapshot())
        self._set_selection_state(self.actions[0], None)
        self._apply_selection(notify=False, animate=False)
        self._refresh_timeline(emit_pose=False)
        self._refresh_history_buttons()
        self.set_dirty(False)

    def set_routine_name(self, name: str):
        self._routine_tag.value = f"Editing {name}" if name else ""
        if _is_mounted(self._routine_tag):
            self._routine_tag.update()

    def snapshot(self) -> dict:
        return actions_to_json(self.actions)

    @property
    def actions_doc(self) -> dict:
        return self.history.current

    def _refresh_history_buttons(self):
        self._undo_button.disabled = not self.history.can_undo
        self._redo_button.disabled = not self.history.can_redo
        if _is_mounted(self._undo_button):
            self._undo_button.update()
        if _is_mounted(self._redo_button):
            self._redo_button.update()

    def set_dirty(self, dirty: bool):
        self._dirty = bool(dirty)
        self._save_button.disabled = not self._dirty
        if self._dirty:
            self._save_status.value = "Unsaved changes"
            self._save_status.color = ACCENT_LIGHT
        else:
            self._save_status.value = "All changes saved"
            self._save_status.color = ft.Colors.ON_SURFACE_VARIANT
        if _is_mounted(self._save_button):
            self._save_button.update()
        if _is_mounted(self._save_status):
            self._save_status.update()

    def _mark_dirty(self):
        if self.on_dirty is not None:
            self.on_dirty()

    def save(self):
        if self.on_save is not None:
            self.on_save()

    def export(self):
        if self.on_export is not None:
            self.on_export()

    def commit_history(self) -> bool:
        """Push current action list into history if it changed."""
        if self._restoring:
            return False
        changed = self.history.push(self.snapshot())
        self._refresh_history_buttons()
        if changed:
            self._mark_dirty()
        return changed

    def restore_from_doc(self, doc: dict, *, notify: bool = True):
        """Replace live actions from an actions JSON document."""
        prev_selected_idx = self.actions.index(self.selected) if self.selected in self.actions else 0
        prev_curve_idx = self._selected_curve[1] if self._selected_curve is not None else None

        self._restoring = True
        try:
            if self._playing:
                self.stop_playback()
            self.actions = actions_from_json(doc)
            self.history.replace_current(self.snapshot())

            # Best-effort selection restore.
            idx = min(prev_selected_idx, len(self.actions) - 1)
            action = self.actions[idx]
            if isinstance(action, MotionAction) and action.motion.curves:
                curve_idx = 0 if prev_curve_idx is None else min(prev_curve_idx, len(action.motion.curves) - 1)
                self._set_selection_state(action, (action, curve_idx))
            else:
                self._set_selection_state(action, None)

            self._apply_selection(notify=notify, animate=False)
            self._refresh_history_buttons()
        finally:
            self._restoring = False
        self._mark_dirty()

    def undo(self) -> bool:
        doc = self.history.undo_once()
        if doc is None:
            return False
        self.restore_from_doc(doc)
        return True

    def redo(self) -> bool:
        doc = self.history.redo_once()
        if doc is None:
            return False
        self.restore_from_doc(doc)
        return True

    def stop_playback(self):
        self._playing = False
        self._playback_task = None
        self._play_end_ms = None
        self._sync_play_buttons()

    def _selection_span(self) -> tuple[float, float] | None:
        """Timeline window covering the selected curves, else the selected motions."""
        if self._selected_curve is not None:
            motion = self._selected_curve[0]
            span = self._timeline.span_of(motion)
            if span is None:
                return None
            bounds = curve_boundaries(motion.motion)
            windows = [
                (span[0] + bounds[i], min(span[1], span[0] + bounds[i + 1]))
                for i in self._multi_curves
                if i + 1 < len(bounds)
            ]
            if not windows:
                return span
            return (min(w[0] for w in windows), max(w[1] for w in windows))
        spans = [
            span
            for a in self._multi
            if isinstance(a, MotionAction) and (span := self._timeline.span_of(a)) is not None
        ]
        if not spans:
            return None
        return (min(s[0] for s in spans), max(s[1] for s in spans))

    def _sync_play_buttons(self):
        selection_window = self._playing and self._play_end_ms is not None
        routine_playing = self._playing and not selection_window
        self._play_button.icon = ft.Icons.PAUSE if routine_playing else ft.Icons.PLAY_ARROW
        self._play_button.tooltip = "Pause" if routine_playing else "Play routine"
        self._play_selection_button.icon = ft.Icons.PAUSE if selection_window else ft.Icons.PLAY_ARROW
        if selection_window:
            tooltip = "Pause"
        elif self._selected_curve is not None:
            tooltip = "Play selected curves" if len(self._multi_curves) > 1 else "Play selected curve"
        elif len(self._multi) > 1:
            tooltip = "Play selection"
        else:
            tooltip = "Play selected motion"
        self._play_selection_button.tooltip = tooltip
        self._play_selection_button.visible = any(isinstance(a, MotionAction) for a in self._multi)
        self._play_selection_button.disabled = self._selection_span() is None
        for button in (self._play_button, self._play_selection_button):
            if _is_mounted(button):
                button.update()

    def _format_time(self, ms: float) -> str:
        return f"{ms / 1000.0:.2f} s"

    def _refresh_timeline(self, *, emit_pose: bool = True):
        was_playing = self._playing
        if was_playing:
            self.stop_playback()

        self._timeline = build_routine_timeline(self.actions)
        duration = max(self._timeline.duration_ms, 1.0)
        self._play_time_ms = min(self._play_time_ms, self._timeline.duration_ms)
        self._time_slider.max = duration
        self._time_slider.value = self._play_time_ms
        self._time_slider.disabled = self._timeline.duration_ms <= 0
        self._play_button.disabled = self._timeline.duration_ms <= 0
        self._time_label.value = self._format_time(self._play_time_ms)
        self._duration_label.value = f"/ {self._format_time(self._timeline.duration_ms)}"

        if _is_mounted(self._time_slider):
            self._time_slider.update()
        if _is_mounted(self._time_label):
            self._time_label.update()
        if _is_mounted(self._duration_label):
            self._duration_label.update()
        self._sync_play_buttons()

        if emit_pose:
            self._emit_playback_pose()

    def _emit_playback_pose(self):
        if self.on_playback_pose is None:
            return
        if self._timeline.duration_ms <= 0:
            self.on_playback_pose(None)
            return
        # At t=0 while idle, let the canvas use the editable start pose.
        if not self._playing and self._play_time_ms <= 0 and not self._slider_dragging:
            self.on_playback_pose(None)
            return
        self.on_playback_pose(self._timeline.pose_at(self._play_time_ms))

    def _seek(self, t_ms: float, *, update_chrome: bool = True):
        duration = self._timeline.duration_ms
        self._play_time_ms = max(0.0, min(float(t_ms), duration))
        # Canvas pose every seek; slider/label are optional (expensive in Flet).
        self._emit_playback_pose()
        if update_chrome:
            self._sync_playback_chrome()

    def _sync_playback_chrome(self):
        self._time_label.value = self._format_time(self._play_time_ms)
        self._time_slider.value = self._play_time_ms
        if _is_mounted(self._time_label):
            self._time_label.update()
        if _is_mounted(self._time_slider):
            self._time_slider.update()

    def _on_slider_drag_start(self, e):
        self._slider_dragging = True
        if self._playing:
            self.stop_playback()

    def _on_slider_drag_end(self, e):
        self._slider_dragging = False
        self._seek(float(e.control.value or 0), update_chrome=True)

    def _on_slider_change(self, e):
        self._seek(float(e.control.value or 0), update_chrome=False)
        self._time_label.value = self._format_time(self._play_time_ms)
        if _is_mounted(self._time_label):
            self._time_label.update()

    def _toggle_playback(self, e):
        if self._timeline.duration_ms <= 0:
            return
        if self._playing:
            was_routine = self._play_end_ms is None
            self.stop_playback()
            self._sync_playback_chrome()
            if was_routine:
                return
        if self._play_time_ms >= self._timeline.duration_ms:
            self._seek(0.0, update_chrome=True)
        self._start_playback(end_ms=None)

    def _toggle_selection_playback(self, e):
        span = self._selection_span()
        if span is None:
            return
        if self._playing:
            was_selection = self._play_end_ms is not None
            self.stop_playback()
            self._sync_playback_chrome()
            if was_selection:
                return
        start, end = span
        # Resume inside the motion; otherwise restart it from the beginning.
        if not (start <= self._play_time_ms < end):
            self._seek(start, update_chrome=True)
        self._start_playback(end_ms=end)

    def _start_playback(self, *, end_ms: float | None):
        self._playing = True
        self._play_end_ms = end_ms
        self._sync_play_buttons()
        self._playback_task = self.page.run_task(self._playback_loop)

    async def _playback_loop(self):
        import asyncio
        import time

        last = time.monotonic()
        last_chrome = last
        while self._playing:
            # Target ~60fps for the robot; chrome (slider) updates much less often.
            await asyncio.sleep(1 / 60)
            now = time.monotonic()
            dt_ms = (now - last) * 1000.0
            last = now
            nxt = self._play_time_ms + dt_ms
            end = self._timeline.duration_ms
            if self._play_end_ms is not None:
                end = min(end, self._play_end_ms)
            if nxt >= end:
                self._seek(end, update_chrome=True)
                self.stop_playback()
                break
            # Canvas every frame; slider/label ~12Hz to avoid Flet UI hitching.
            update_chrome = (now - last_chrome) >= (1 / 12)
            if update_chrome:
                last_chrome = now
            self._seek(nxt, update_chrome=update_chrome)

    def start_action(self) -> StartAction:
        return self.actions[0]

    def motion_actions(self) -> list[MotionAction]:
        return [a for a in self.actions if isinstance(a, MotionAction)]

    def previous_action(self, action: RobotAction) -> RobotAction | None:
        """Previous action that establishes a pose.

        Code actions do not move the robot, so they are skipped.
        """
        idx = self.actions.index(action)
        for i in range(idx - 1, -1, -1):
            prev = self.actions[i]
            if prev.end_pose() is not None:
                return prev
        return None

    def _motion_index(self, action: RobotAction) -> int:
        motion_idx = 0
        for a in self.actions:
            if isinstance(a, MotionAction):
                motion_idx += 1
                if a is action:
                    return motion_idx
        return 0

    def _set_selection_state(
        self,
        action: RobotAction | None,
        curve: tuple[MotionAction, int] | None,
        *,
        multi: list[RobotAction] | None = None,
        curves: list[int] | None = None,
        group: ActionGroup | None = None,
    ):
        """Set the primary selection plus any extra shift-selected actions or curves."""
        self.selected = action
        self._selected_curve = curve
        self._selected_group = group
        if curve is not None:
            self._multi = [curve[0]]
            picked = set(curves or ()) | {curve[1]}
            self._multi_curves = sorted(i for i in picked if 0 <= i < len(curve[0].motion.curves))
        else:
            picked = list(multi or ()) + ([action] if action is not None else [])
            self._multi = [a for a in self.actions if any(a is p for p in picked)]
            self._multi_curves = []
        single = len(self._multi) == 1
        for a in self.actions:
            a.selected = any(a is m for m in self._multi)
            if isinstance(a, MotionAction):
                a.expanded = a is action and (curve is not None or single)

    def _is_single(self, action: RobotAction) -> bool:
        return (
            self.selected is action
            and self._selected_curve is None
            and self._selected_group is None
            and len(self._multi) == 1
        )

    def motion_dimmed(self, motion_action: MotionAction) -> bool:
        """Whether the canvas fades this motion because others are selected."""
        if self.selected is None:
            return False
        if self._selected_curve is not None:
            return self._selected_curve[0] is not motion_action
        if not any(isinstance(a, MotionAction) for a in self._multi) and self._selected_group is None:
            return False
        return not any(a is motion_action for a in self._multi)

    def curve_selected(self, motion_action: MotionAction, curve_index: int) -> bool:
        return (
            self._selected_curve is not None
            and self._selected_curve[0] is motion_action
            and curve_index in self._multi_curves
        )

    def _editor_content(self) -> ft.Control | None:
        if self.selected is None:
            return None
        if self._selected_group is not None:
            return self._build_group_editor(self._selected_group)
        if self._selected_curve is not None:
            motion_action, curve_index = self._selected_curve
            if len(self._multi_curves) > 1:
                return self._build_multi_editor()
            curve = motion_action.motion.curves[curve_index]
            return motion_action.build_curve_editor(
                curve,
                curve_index,
                self.previous_action(motion_action),
            )
        if len(self._multi) > 1:
            return self._build_multi_editor()
        if isinstance(self.selected, MotionAction):
            return self.selected.build_motion_editor()
        return self.selected.build_editor(self.previous_action(self.selected))

    def _editor_title(self) -> str:
        if self._selected_group is not None:
            return "Group"
        if self._selected_curve is not None:
            if len(self._multi_curves) > 1:
                return f"{len(self._multi_curves)} curves selected"
            _, curve_index = self._selected_curve
            return f"Curve {curve_index + 1}"
        if len(self._multi) > 1:
            return f"{len(self._multi)} actions selected"
        if self.selected is not None:
            return self.selected.type_name
        return ""

    def _build_multi_editor(self) -> ft.Control:
        if self._selected_curve is not None:
            motion = self._selected_curve[0]
            names = ", ".join(f"Curve {i + 1}" for i in self._multi_curves)
            summary = f"{names} of {motion.name}"
        else:
            summary = ", ".join(a.name for a in self._multi)
        span = self._selection_span()
        lines = [ft.Text(summary, size=13)]
        if span is not None:
            lines.append(_tag_line("Simulated", f"{span[1] - span[0]:.0f} ms", muted=True))
        lines.append(
            ft.Text(
                "Shift-click to add or remove items. Right-click for cut, copy, paste"
                + ("" if self._selected_curve is not None else " and grouping")
                + ".",
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
            )
        )
        return ft.Column(spacing=8, tight=True, controls=lines)

    def _build_group_editor(self, group: ActionGroup) -> ft.Control:
        members = self._group_members(group)

        def rename(e):
            group.name = (e.control.value or "").strip() or "Group"
            self._rebuild_list()

        def commit(e):
            rename(e)
            self.commit_history()

        name_field = _text_field("Name", group.name, rename, width=LEFT_PANEL_WIDTH, on_blur=commit)
        name_field.on_submit = commit
        return ft.Column(
            spacing=10,
            tight=True,
            controls=[
                name_field,
                ft.Text(", ".join(a.name for a in members), size=13),
                _tag_line("Total", f"{sum(a.time for a in members):.0f} ms", muted=True),
            ],
        )

    def select_motion(self, action: MotionAction, notify: bool = True):
        if self._is_single(action):
            return
        self._set_selection_state(action, None)
        self._apply_selection(notify)

    def select_curve(self, motion_action: MotionAction, curve_index: int, notify: bool = True):
        if self._selected_curve == (motion_action, curve_index) and len(self._multi_curves) == 1:
            return
        self._set_selection_state(motion_action, (motion_action, curve_index))
        self._apply_selection(notify)

    def select(self, action: RobotAction | None, notify: bool = True):
        if action is None:
            self._set_selection_state(None, None)
            self._apply_selection(notify)
            return
        if isinstance(action, MotionAction):
            self.select_motion(action, notify=notify)
            return
        self._set_selection_state(action, None)
        self._apply_selection(notify)

    def select_many(self, actions: list[RobotAction], *, group: ActionGroup | None = None, notify: bool = True):
        actions = [a for a in self.actions if any(a is x for x in actions)]
        if not actions:
            self.select(None, notify=notify)
            return
        self._set_selection_state(actions[-1], None, multi=actions, group=group)
        self._apply_selection(notify)

    def focus_keys(self):
        """Route keys to the list so its shortcuts work after a click."""
        _TextFocus.field = None
        if _is_mounted(self._key_sink):
            self.page.run_task(self._key_sink.focus)

    def _reset_key_sink(self, e=None):
        self._key_sink.value = _KEY_SINK_TEXT
        self._key_sink.selection = _KEY_SINK_SELECTION
        if _is_mounted(self._key_sink):
            self._key_sink.update()

    def _click_action(self, action: RobotAction):
        """List click: shift adds / removes it, otherwise select or deselect it."""
        self.focus_keys()
        if keys.shift_held() and self.selected is not None:
            self._extend_selection([action])
        elif self._is_single(action):
            self.select(None)
        else:
            self.select(action)

    def _click_group(self, group: ActionGroup):
        self.focus_keys()
        members = self._group_members(group)
        if keys.shift_held() and self.selected is not None:
            self._extend_selection(members)
        elif self._selected_group is group:
            self.select(None)
        else:
            self.select_many(members, group=group)

    def _click_curve(self, motion_action: MotionAction, curve_index: int):
        self.focus_keys()
        same_motion = self._selected_curve is not None and self._selected_curve[0] is motion_action
        if keys.shift_held() and same_motion:
            picked = list(self._multi_curves)
            if curve_index in picked:
                picked.remove(curve_index)
            else:
                picked.append(curve_index)
            if not picked:
                self.select_motion(motion_action)
                return
            primary = curve_index if curve_index in picked else picked[-1]
            self._set_selection_state(motion_action, (motion_action, primary), curves=picked)
            self._apply_selection(True)
        elif self._selected_curve == (motion_action, curve_index) and len(self._multi_curves) == 1:
            self.select_motion(motion_action)
        else:
            self.select_curve(motion_action, curve_index)

    def _extend_selection(self, items: list[RobotAction]):
        """Toggle ``items`` in the action selection (all out if all were in)."""
        base = list(self._multi)
        if all(any(i is b for b in base) for i in items):
            base = [b for b in base if not any(b is i for i in items)]
        else:
            base += [i for i in items if not any(i is b for b in base)]
        if not base:
            self.select(None)
            return
        primary = items[-1] if any(items[-1] is b for b in base) else base[-1]
        self._set_selection_state(primary, None, multi=base)
        self._apply_selection(True)

    def _apply_editor_content(self, title: str, editor: ft.Control | None):
        if editor is not None:
            _track_text_focus(editor)
        self._editor_title_text.value = title
        self._editor_body.content = (
            editor if editor is not None else ft.Text("No editor for this action.")
        )

    def _on_editor_fade_end(self, e: ft.ControlEvent):
        if e.data != "opacity" or self._pending_editor_apply is None:
            return
        title, editor = self._pending_editor_apply
        self._pending_editor_apply = None
        self._apply_editor_content(title, editor)
        self._editor_body.opacity = 1.0
        if _is_mounted(self.page):
            self.page.update()

    def _update_editor_panel(self, *, flush: bool = True, animate: bool = False):
        if self.selected is None:
            self._pending_editor_apply = None
            self._editor_shell.visible = False
            self._editor_body.content = ft.Container(height=0)
            self._editor_body.opacity = 1.0
        else:
            # A shell that was hidden never fires the fade's animation-end event.
            was_visible = self._editor_shell.visible
            self._editor_shell.visible = True
            title = self._editor_title()
            editor = self._editor_content()
            if animate and was_visible and self._editor_body.opacity == 1.0:
                self._pending_editor_apply = (title, editor)
                self._editor_body.opacity = 0.0
            else:
                self._pending_editor_apply = None
                self._apply_editor_content(title, editor)
                self._editor_body.opacity = 1.0

        if flush and _is_mounted(self.page):
            self.page.update()

    def _apply_selection(self, notify: bool, *, animate: bool = True):
        self._update_editor_panel(flush=False, animate=animate)
        self._rebuild_list(flush=False)
        self._sync_play_buttons()
        if notify:
            self._notify_scene()
        if _is_mounted(self.page):
            self.page.update()

    def refresh_editor(self):
        if self.selected is None:
            return
        self._update_editor_panel(animate=False)

    def apply_live_g1(self, target, point_index: int | None = None):
        """Cheap G1 sync during canvas drags (no profiling / list rebuild)."""
        if isinstance(target, CanvasCurve):
            prev = self.previous_action(target.motion_action)
            # Curve 0 uses prior action pose; later curves use prior curve end.
            if target.curve_index == 0:
                pose = prev.end_pose() if prev is not None else None
            else:
                pose = None
            target.motion_action.motion.enforce_g1_after_edit(
                target.curve_index,
                point_index if point_index is not None else 0,
                previous_pose=pose,
            )
            return
        if isinstance(target, StartAction):
            self._propagate_g1_from_action(target)

    def _propagate_g1_from_action(self, action: RobotAction):
        idx = self.actions.index(action)
        pose = action.end_pose()
        if pose is None:
            return
        for nxt in self.actions[idx + 1 :]:
            if isinstance(nxt, MotionAction):
                if nxt.motion.g1_continuity:
                    nxt.motion.enforce_g1(previous_pose=pose)
                return
            if nxt.end_pose() is not None:
                return

    def on_canvas_point_drag_end(self, target):
        """Commit a canvas drag: re-profile, refresh timeline/list/editor."""
        if self._playing:
            self.stop_playback()
        if isinstance(target, CanvasCurve):
            prev = self.previous_action(target.motion_action)
            pose = prev.end_pose() if prev is not None else None
            target.motion_action.motion.enforce_g1(previous_pose=pose)
            profile_action = target.motion_action
        else:
            self._propagate_g1_from_action(target)
            profile_action = None
        self._notify_scene(profile_action=profile_action)
        self._rebuild_list()
        if isinstance(target, CanvasCurve):
            if self._selected_curve == (target.motion_action, target.curve_index):
                self.refresh_editor()
        elif self.selected is target:
            self.refresh_editor()
        self.commit_history()

    def _wire_action(self, action: RobotAction):
        action.on_select = self._click_action
        action.on_delete = self._confirm_delete
        action.on_change = self._handle_action_change
        action.on_hover_change = self._handle_hover_change
        action.on_menu = self._handle_menu
        if isinstance(action, MotionAction):
            action.on_select_motion = self._click_action
            action.on_select_curve = self._click_curve
            action.on_add_curve = self._add_curve
            action.on_delete_curve = self._delete_curve
            action.on_reorder_curves = self._handle_curve_reorder
            action.on_reorder_curves_start = self._handle_curve_reorder_start
            action.on_reorder_curves_end = self._handle_curve_reorder_end
            action.on_timeout_change = self._handle_code_change
            action.curve_menu_spec = lambda motion, i: self._menu_spec((motion, i))
            action.on_curve_menu = lambda motion, i, data: self._handle_menu((motion, i), data)
        if isinstance(action, (MotionAction, CodeAction)):
            action.on_code_change = self._handle_code_change
            action.code_editor.on_focus = self._on_code_focus

    def _wire_group(self, group: ActionGroup):
        group.on_click = self._click_group
        group.on_toggle = self._toggle_group_collapsed
        group.on_menu = self._handle_menu
        group.on_delete = self._confirm_delete_group
        group.on_hover_change = self._handle_hover_change

    def _handle_hover_change(self, target, hovering: bool):
        changed = False
        for item in [*self.actions, *self._groups()]:
            new_hover = item is target and hovering
            if item._hovering != new_hover:
                changed = True
            item._hovering = new_hover
        if changed:
            self._rebuild_list()

    def _renumber_motions(self):
        """Assign 1-based motion ids from current list order."""
        for i, action in enumerate(self.motion_actions(), start=1):
            action.set_motion_id(i)
            action._list_index = i

    def _renumber_code_actions(self):
        index = 0
        for action in self.actions:
            if isinstance(action, CodeAction):
                index += 1
                action.set_action_id(index)
                action._list_index = index

    def _on_code_focus(self, editor: CodeEditor):
        self._code_focus = editor

    def handle_keyboard(self, e: ft.KeyboardEvent) -> bool:
        """Indent the focused code box when Tab is pressed."""
        key = e.key or ""
        if key != "Tab" or e.ctrl or e.meta or e.alt:
            return False
        editor = self._code_focus
        if editor is None or not editor.is_active():
            return False
        editor.handle_tab(shift=bool(e.shift))
        return True

    def _handle_code_change(self, action: RobotAction, *, commit: bool = False):
        # Code and timeouts never change paths or sim timing, so skip
        # re-profiling, and keep the editor mounted so the field keeps focus.
        if commit:
            self.commit_history()
            self._rebuild_list()

    def _groups(self) -> list[ActionGroup]:
        groups: list[ActionGroup] = []
        for action in self.actions:
            if action.group is not None and not any(action.group is g for g in groups):
                groups.append(action.group)
        return groups

    def _group_members(self, group: ActionGroup) -> list[RobotAction]:
        return [a for a in self.actions if a.group is group]

    def _rebuild_list(self, *, flush: bool = True):
        normalize_groups(self.actions)
        self._renumber_motions()
        self._renumber_code_actions()
        items = []
        display: list[tuple[str, object]] = []
        current_group = None
        for action in self.actions:
            if not isinstance(action, (MotionAction, CodeAction)):
                action._list_index = 0
            self._wire_action(action)
            group = action.group
            if group is not None and group is not current_group:
                self._wire_group(group)
                members = self._group_members(group)
                header = group.build_header(
                    selected=self._selected_group is group,
                    count=len(members),
                    time_ms=sum(m.time for m in members),
                )
                group.menu.sync(self._menu_spec(group))
                header.margin = None
                items.append(header)
                display.append(("group", group))
            current_group = group
            if group is not None and group.collapsed:
                continue
            if isinstance(action, MotionAction):
                row = action._ensure_list_row(
                    reorderable=True,
                    selected_curves=set(self._multi_curves) if self._selected_curve is not None
                    and self._selected_curve[0] is action else set(),
                )
            else:
                row = action.build_list_item(reorderable=isinstance(action, CodeAction))
            if action.menu is not None:
                action.menu.sync(self._menu_spec(action))
            if isinstance(action, StartAction):
                row.margin = ft.Margin.only(bottom=6)
            else:
                row.margin = _GROUP_INDENT if group is not None else None
            items.append(row)
            display.append(("action", action))
        self._display = display
        self._reorder_list.controls = items
        if flush and _is_mounted(self._reorder_list):
            self._reorder_list.update()

    def _handle_action_change(self, action: RobotAction, *, commit: bool = False):
        if isinstance(action, CodeAction):
            # Wait time changes the simulate clock. Skip re-profiling motions,
            # and leave the code field mounted (a rebuild would drop focus).
            self._refresh_timeline(emit_pose=True)
            if commit:
                self.commit_history()
                self._rebuild_list()
            return
        if isinstance(action, MotionAction) and action.motion.g1_continuity:
            prev = self.previous_action(action)
            pose = prev.end_pose() if prev is not None else None
            action.motion.enforce_g1(previous_pose=pose)
        elif isinstance(action, StartAction):
            self._propagate_g1_from_action(action)
        profile_action = action if isinstance(action, MotionAction) else None
        self._notify_scene(profile_action=profile_action)
        if commit:
            self.commit_history()
            self._rebuild_list()
            if self.selected is action:
                self.refresh_editor()

    def _profile_all_motions(self):
        for action in self.motion_actions():
            action.recompute_profile()

    def reprofile(self, *, refresh_ui: bool = True):
        """Recompute all motion profiles (e.g. after robot specs change)."""
        self._profile_all_motions()
        # Always rebuild the simulate timeline so duration reflects new profiles.
        self._refresh_timeline(emit_pose=refresh_ui)
        if refresh_ui:
            self._rebuild_list()
            if self.selected is not None:
                self.refresh_editor()
            if self.on_scene_change is not None:
                self.on_scene_change()

    def _notify_scene(self, profile_action: RobotAction | None = None):
        if isinstance(profile_action, MotionAction):
            profile_action.recompute_profile()
        else:
            self._profile_all_motions()
        # Update canvas curve handles before emitting a playback pose — otherwise
        # set_playback_pose → _render can touch stale CanvasCurve indices (e.g. after delete).
        if self.on_scene_change is not None:
            self.on_scene_change()
        self._refresh_timeline(emit_pose=True)

    def _next_motion_id(self) -> int:
        return len(self.motion_actions()) + 1

    def _latest_pose(self, before: int | None = None):
        for action in reversed(self.actions[:before]):
            pose = action.end_pose()
            if pose is not None:
                return pose
        return None

    def _add_motion(self):
        self._insert_motion(len(self.actions))

    def _add_code_action(self):
        self._insert_code_action(len(self.actions))

    def _insert_motion(self, index: int, group: ActionGroup | None = None):
        prev_pose = self._latest_pose(before=index)
        motion = Motion(curves=[Curve(points=default_curve_points(prev_pose))])
        if motion.g1_continuity:
            motion.enforce_g1(previous_pose=prev_pose)
        action = MotionAction(motion, self._next_motion_id())
        action.group = group
        self.actions.insert(index, action)
        # The next motion now starts from this one's end.
        self._propagate_g1_from_action(action)
        self.select_curve(action, 0)
        self.commit_history()

    def _insert_code_action(self, index: int, group: ActionGroup | None = None):
        action = CodeAction()
        action.group = group
        self.actions.insert(index, action)
        self.select(action)
        self.commit_history()

    def _insert_relative(self, anchor: RobotAction, where: str, kind: str):
        if anchor not in self.actions:
            return
        index = self.actions.index(anchor) + (1 if where == "after" else 0)
        # Start stays first.
        index = max(1, index)
        # Inserting beside a group member puts the new item in that group.
        if kind == "Motion":
            self._insert_motion(index, anchor.group)
        else:
            self._insert_code_action(index, anchor.group)

    def _add_curve(self, motion_action: MotionAction):
        previous = self.previous_action(motion_action)
        prior_pose = previous.end_pose() if previous is not None else None
        if motion_action.motion.curves:
            prev_curve = motion_action.motion.curves[-1]
            prev_pose = prev_curve.end_pose()
        else:
            prev_curve = None
            prev_pose = prior_pose
        new_curve = Curve(points=default_curve_points(prev_pose))
        motion_action.motion.curves.append(new_curve)
        if motion_action.motion.g1_continuity:
            if prev_curve is not None:
                apply_g1_forward(prev_curve, new_curve)
            else:
                motion_action.motion.enforce_g1(previous_pose=prior_pose)
        self.select_curve(motion_action, len(motion_action.motion.curves) - 1)
        self.commit_history()

    def _delete_curve(self, motion_action: MotionAction, curve_index: int):
        if len(motion_action.motion.curves) <= 1:
            return
        motion_action.motion.curves.pop(curve_index)
        prev = self.previous_action(motion_action)
        pose = prev.end_pose() if prev is not None else None
        motion_action.motion.enforce_g1(previous_pose=pose)
        if self._selected_curve == (motion_action, curve_index):
            new_index = min(curve_index, len(motion_action.motion.curves) - 1)
            self._selected_curve = (motion_action, new_index)
            self.selected = motion_action
            self._update_editor_panel(flush=False, animate=False)
            self._rebuild_list(flush=False)
            self._notify_scene(motion_action)
            if _is_mounted(self.page):
                self.page.update()
        elif (
            self._selected_curve is not None
            and self._selected_curve[0] is motion_action
            and self._selected_curve[1] > curve_index
        ):
            self._selected_curve = (motion_action, self._selected_curve[1] - 1)
            self._rebuild_list()
            self._notify_scene(motion_action)
            if _is_mounted(self.page):
                self.page.update()
        else:
            self._rebuild_list()
            self._notify_scene(motion_action)
        self.commit_history()

    def _adjust_selected_curve_after_reorder(
        self, motion_action: MotionAction, old_index: int, new_index: int
    ):
        if self._selected_curve is None or self._selected_curve[0] is not motion_action:
            return
        selected_index = self._selected_curve[1]
        if selected_index == old_index:
            self._selected_curve = (motion_action, new_index)
        elif old_index < selected_index <= new_index:
            self._selected_curve = (motion_action, selected_index - 1)
        elif new_index <= selected_index < old_index:
            self._selected_curve = (motion_action, selected_index + 1)

    def _handle_curve_reorder_start(self, motion_action: MotionAction, e: ft.OnReorderEvent):
        motion_action._dragging_curve_index = e.old_index
        self._rebuild_list()

    def _handle_curve_reorder_end(self, motion_action: MotionAction, e: ft.OnReorderEvent):
        motion_action._dragging_curve_index = None
        self._rebuild_list()

    def _handle_curve_reorder(self, motion_action: MotionAction, e: ft.OnReorderEvent):
        curves = motion_action.motion.curves
        item = curves.pop(e.old_index)
        curves.insert(e.new_index, item)
        self._adjust_selected_curve_after_reorder(motion_action, e.old_index, e.new_index)
        prev = self.previous_action(motion_action)
        pose = prev.end_pose() if prev is not None else None
        motion_action.motion.enforce_g1(previous_pose=pose)
        motion_action._dragging_curve_index = None
        self._rebuild_list()
        self._notify_scene(motion_action)
        self.commit_history()

    def _close_modal(self):
        if self._modal is not None and self._modal in self.page.overlay:
            self.page.overlay.remove(self._modal)
            self._modal = None
            self.page.update()

    def _show_modal(self, title: str, body: ft.Control, actions: list[ft.Control]):
        self._close_modal()

        card = ft.Container(
            width=340,
            padding=20,
            bgcolor=ft.Colors.SURFACE,
            border_radius=12,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            content=ft.Column(
                tight=True,
                spacing=14,
                controls=[
                    title_text(title, size=18),
                    body,
                    ft.Row(alignment=ft.MainAxisAlignment.END, spacing=8, controls=actions),
                ],
            ),
        )

        scrim = ft.Container(expand=True, bgcolor=ft.Colors.with_opacity(0.45, ft.Colors.BLACK))
        self._modal = ft.Stack(
            expand=True,
            controls=[
                scrim,
                ft.Container(
                    expand=True,
                    alignment=ft.Alignment(0, 0),
                    content=card,
                ),
            ],
        )
        self.page.overlay.append(self._modal)
        self.page.update()

    def _confirm_delete(self, action: RobotAction):
        if not action.deletable:
            return

        def cancel(e):
            self._close_modal()

        def confirm(e):
            self._close_modal()
            idx = self.actions.index(action)
            self.actions.remove(action)
            if any(action is a for a in self._multi):
                self.select(self.actions[min(idx, len(self.actions) - 1)])
            else:
                self._rebuild_list()
                self._notify_scene()
            self.commit_history()

        self._show_modal(
            "Delete action?",
            ft.Container(width=300, content=ft.Text(f'Remove "{action.name}" from the routine?')),
            [
                ft.OutlinedButton("Cancel", on_click=cancel),
                red_button("Delete", on_click=confirm),
            ],
        )

    def _show_new_action_dialog(self, e):
        def close(ev):
            self._close_modal()

        def add_kind(kind: str):
            def handler(ev):
                self._close_modal()
                if kind == "Motion":
                    self._add_motion()
                else:
                    self._add_code_action()

            return handler

        icons = {
            "Motion": ft.Icons.ROUTE,
            "Action": ft.Icons.CODE,
        }
        tiles = [
            ft.Container(
                padding=ft.Padding.symmetric(vertical=8),
                ink=True,
                on_click=add_kind(kind),
                content=ft.Row(
                    spacing=12,
                    controls=[
                        ft.Icon(icons.get(kind, ft.Icons.CIRCLE)),
                        ft.Column(
                            spacing=2,
                            tight=True,
                            controls=[
                                ft.Text(kind, weight=ft.FontWeight.BOLD),
                                ft.Text(
                                    description,
                                    size=12,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                            ],
                        ),
                    ],
                ),
            )
            for kind, description in CREATABLE_ACTION_TYPES.items()
        ]

        self._show_modal(
            "New action",
            ft.Container(width=300, content=ft.Column(spacing=4, tight=True, controls=tiles)),
            [ft.OutlinedButton("Cancel", on_click=close)],
        )

    def _handle_reorder_start(self, e: ft.OnReorderEvent):
        idx = e.old_index
        if idx is None or not 0 < idx < len(self._display):
            return
        self._display[idx][1]._dragging = True
        self._rebuild_list()

    def _clear_dragging(self):
        for item in [*self.actions, *self._groups()]:
            item._dragging = False

    def _handle_reorder_end(self, e: ft.OnReorderEvent):
        self._clear_dragging()
        self._rebuild_list()

    def _handle_reorder(self, e: ft.OnReorderEvent):
        """Move a row; a group header moves its whole group.

        A dropped action joins the group of the row it lands in front of.
        Groups don't nest, so a group dropped inside another lands before it.
        """
        self._clear_dragging()
        rows = self._display
        old, new = e.old_index, e.new_index
        if old is None or new is None or old == 0 or new == 0 or old >= len(rows):
            self._rebuild_list()
            return
        kind, obj = rows[old]
        moving = self._group_members(obj) if kind == "group" else [obj]
        rest = rows[:old] + rows[old + 1 :]
        target = rest[new] if new < len(rest) else None
        join = None
        if target is None:
            before = None
        elif target[0] == "group":
            before = self._group_members(target[1])[0]
        else:
            before = target[1]
            if kind == "action":
                join = before.group
            elif before.group is not None:
                before = self._group_members(before.group)[0]
        if before is not None and any(before is m for m in moving):
            self._rebuild_list()
            return
        remaining = [a for a in self.actions if not any(a is m for m in moving)]
        index = len(remaining) if before is None else next(
            i for i, a in enumerate(remaining) if a is before
        )
        if kind == "action":
            obj.group = join
        self.actions = remaining[:index] + moving + remaining[index:]
        self._rebuild_list()
        self._notify_scene()
        self.commit_history()

    # --- Groups -------------------------------------------------------------

    def _toggle_group_collapsed(self, group: ActionGroup):
        group.collapsed = not group.collapsed
        # View state: saved with the routine, but not its own undo step.
        self.history.replace_current(self.snapshot())
        self._mark_dirty()
        self._rebuild_list()

    def _groupable(self, items: list[RobotAction]) -> list[RobotAction] | None:
        """Actions a new group over ``items`` would hold, or None if not allowed.

        Groups the selection touches are absorbed whole, and the result must be
        consecutive and exclude Start.
        """
        if not items or any(isinstance(a, StartAction) for a in items):
            return None
        members = list(items)
        for action in items:
            if action.group is not None:
                members += [
                    m for m in self._group_members(action.group) if not any(m is x for x in members)
                ]
        indices = sorted(self.actions.index(a) for a in members)
        if indices[-1] - indices[0] + 1 != len(indices):
            return None
        result = [self.actions[i] for i in indices]
        first = result[0].group
        if first is not None and all(a.group is first for a in result):
            return None
        return result

    def _unused_group_name(self) -> str:
        taken = {g.name for g in self._groups()}
        n = len(taken) + 1
        while f"Group {n}" in taken:
            n += 1
        return f"Group {n}"

    def _show_group_dialog(self, rename: ActionGroup | None = None):
        if rename is None:
            members = self._groupable(self._multi) if self._selected_curve is None else None
            if members is None:
                self._toast("Select consecutive actions (not Start or curves) to group them.")
                return
            touched = []
            for a in members:
                if a.group is not None and not any(a.group is g for g in touched):
                    touched.append(a.group)
            default = touched[0].name if len(touched) == 1 else self._unused_group_name()
        else:
            members = None
            default = rename.name

        field = _text_field("Name", default, None, width=300)
        field.autofocus = True

        def submit(e):
            name = (field.value or "").strip() or "Group"
            self._close_modal()
            if rename is not None:
                rename.name = name
                self._rebuild_list()
                if self._selected_group is rename:
                    self.refresh_editor()
            else:
                group = ActionGroup(name)
                for action in members:
                    action.group = group
                self.select_many(members, group=group)
            self.commit_history()

        field.on_submit = submit
        self._show_modal(
            "Rename group" if rename is not None else "New group",
            field,
            [
                ft.OutlinedButton("Cancel", on_click=lambda e: self._close_modal()),
                green_button("Rename" if rename is not None else "Group", on_click=submit),
            ],
        )

    def _ungroup(self, group: ActionGroup | None):
        if group is None:
            return
        members = self._group_members(group)
        for action in members:
            action.group = None
        if self._selected_group is group:
            self.select_many(members)
        else:
            self._rebuild_list()
        self.commit_history()

    def _confirm_delete_group(self, group: ActionGroup):
        members = self._group_members(group)

        def ungroup(e):
            self._close_modal()
            self._ungroup(group)

        def confirm(e):
            self._close_modal()
            idx = self.actions.index(members[0])
            self.actions = [a for a in self.actions if a.group is not group]
            self.select(self.actions[min(idx, len(self.actions) - 1)])
            self.commit_history()

        count = len(members)
        self._show_modal(
            "Delete group?",
            ft.Container(
                width=300,
                content=ft.Text(
                    f'Remove "{group.name}" and its {count} item{"s" if count != 1 else ""} '
                    "from the routine?"
                ),
            ),
            [
                ft.OutlinedButton("Cancel", on_click=lambda e: self._close_modal()),
                ft.OutlinedButton("Ungroup", on_click=ungroup),
                red_button("Delete", on_click=confirm),
            ],
        )

    # --- Context menu -------------------------------------------------------

    def _target_selected(self, target) -> bool:
        """Whether a right-clicked row is part of the current selection."""
        if isinstance(target, tuple):
            return self.curve_selected(*target)
        if isinstance(target, ActionGroup):
            return self._selected_group is target
        return self._selected_curve is None and any(target is a for a in self._multi)

    def _menu_spec(self, target) -> list:
        """Items for a row's right-click menu (see ``_ListMenu.sync``)."""
        clip = _clipboard["data"]
        selected = self._target_selected(target)
        if isinstance(target, tuple):
            motion, _ = target
            count = len(self._multi_curves) if selected else 1
            keeps_none = count >= len(motion.motion.curves)
            return [
                ("Cut", ft.Icons.CONTENT_CUT, ("cut",), "⌘X", keeps_none),
                ("Copy", ft.Icons.CONTENT_COPY, ("copy",), "⌘C", False),
                ("Paste after", ft.Icons.CONTENT_PASTE, ("paste",), "⌘V", clip is None),
                None,
                _delete_entry(count, keeps_none),
            ]
        if isinstance(target, ActionGroup):
            items = self._group_members(target)
        else:
            items = self._multi if selected else [target]
        copyable = any(not isinstance(a, StartAction) for a in items)
        spec = [
            ("Cut", ft.Icons.CONTENT_CUT, ("cut",), "⌘X", not copyable),
            ("Copy", ft.Icons.CONTENT_COPY, ("copy",), "⌘C", not copyable),
            ("Paste after", ft.Icons.CONTENT_PASTE, ("paste",), "⌘V", clip is None),
            None,
        ]
        if isinstance(target, ActionGroup):
            return spec + [
                ("Rename group…", ft.Icons.EDIT, ("rename",), None, False),
                ("Ungroup", ft.Icons.FOLDER_OFF, ("ungroup",), None, False),
                None,
                ("Delete group", ft.Icons.DELETE_OUTLINE, ("delete",), None, False, True),
            ]
        spec.append(
            (
                "Group selection…" if len(items) > 1 else "Group…",
                ft.Icons.CREATE_NEW_FOLDER,
                ("group",),
                "⌘G",
                self._groupable(items) is None,
            )
        )
        if target.group is not None:
            spec.append(("Ungroup", ft.Icons.FOLDER_OFF, ("ungroup",), None, False))
        # Nothing may precede Start.
        no_before = not target.deletable
        return spec + [
            None,
            ("Insert motion before", ft.Icons.ROUTE, ("insert", "before", "Motion"), None, no_before),
            ("Insert action before", ft.Icons.CODE, ("insert", "before", "Action"), None, no_before),
            ("Insert motion after", ft.Icons.ROUTE, ("insert", "after", "Motion"), None, False),
            ("Insert action after", ft.Icons.CODE, ("insert", "after", "Action"), None, False),
            None,
            _delete_entry(sum(1 for a in items if a.deletable), not copyable),
        ]

    def _handle_menu(self, target, data: tuple):
        op = data[0]
        if op == "insert":
            self._insert_relative(target, data[1], data[2])
            return
        if not self._target_selected(target):
            if isinstance(target, tuple):
                self.select_curve(*target)
            elif isinstance(target, ActionGroup):
                self.select_many(self._group_members(target), group=target)
            else:
                self.select(target)
        if op == "copy":
            self.copy_selection()
        elif op == "cut":
            self.cut_selection()
        elif op == "paste":
            self.paste()
        elif op == "group":
            self._show_group_dialog()
        elif op == "rename":
            self._show_group_dialog(rename=target)
        elif op == "ungroup":
            self._ungroup(target if isinstance(target, ActionGroup) else target.group)
        elif op == "delete":
            self.delete_selection()

    def delete_selection(self) -> bool:
        """Remove the selected curves, group or actions; a toast offers Undo."""
        if self.selected is None:
            return False
        label = self._selection_label()
        if self._selected_curve is not None:
            motion = self._selected_curve[0]
            picked = sorted(self._multi_curves)
            if len(picked) >= len(motion.motion.curves):
                self._toast("A motion needs at least one curve. Delete the motion instead.")
                return False
            for i in reversed(picked):
                motion.motion.curves.pop(i)
            prev = self.previous_action(motion)
            motion.motion.enforce_g1(previous_pose=prev.end_pose() if prev is not None else None)
            self._set_selection_state(
                motion, (motion, min(picked[0], len(motion.motion.curves) - 1))
            )
            self._apply_selection(True)
        else:
            if self._selected_group is not None:
                removed = self._group_members(self._selected_group)
            else:
                removed = [a for a in self._multi if a.deletable]
            if not removed:
                return False
            first_index = self.actions.index(removed[0])
            self.actions = [a for a in self.actions if not any(a is r for r in removed)]
            self.select(self.actions[min(first_index, len(self.actions)) - 1])
        self.commit_history()
        self._toast(f"Deleted {label}", undo=True)
        return True

    # --- Clipboard ----------------------------------------------------------

    def _on_list_key_down(self, e: ft.KeyDownEvent):
        if _TextFocus.field is not None or self._modal is not None:
            return
        if e.key in ("Delete", "Backspace") and not keys.command_held():
            self.delete_selection()
            return
        if not keys.command_held():
            return
        handler = {
            "C": self.copy_selection,
            "X": self.cut_selection,
            "V": self.paste,
            "G": self._show_group_dialog,
        }.get((e.key or "").upper())
        if handler is not None:
            handler()

    def _toast(self, message: str, *, undo: bool = False):
        """Short notice that dismisses itself, optionally offering Undo."""
        if self.page is None:
            return
        self.page.show_dialog(
            ft.SnackBar(
                content=ft.Text(message, color=ft.Colors.ON_SURFACE),
                action=ft.SnackBarAction(
                    label="Undo", text_color=ACCENT, on_click=lambda e: self.undo()
                ) if undo else None,
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                shape=ft.RoundedRectangleBorder(
                    radius=10, side=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)
                ),
                behavior=ft.SnackBarBehavior.FLOATING,
                width=440,
                duration=ft.Duration(milliseconds=3000 if undo else 2500),
            )
        )

    def _selection_label(self) -> str:
        if self._selected_curve is not None:
            n = len(self._multi_curves)
            return f"Curve {self._multi_curves[0] + 1}" if n == 1 else f"{n} curves"
        if self._selected_group is not None:
            return f'group "{self._selected_group.name}"'
        actions = [a for a in self._multi if not isinstance(a, StartAction)]
        return actions[0].name if len(actions) == 1 else f"{len(actions)} items"

    def _selection_clip(self) -> dict | None:
        if self._selected_curve is not None:
            motion = self._selected_curve[0]
            return {
                "kind": "curves",
                "items": [curve_to_json(motion.motion.curves[i]) for i in self._multi_curves],
            }
        actions = [a for a in self._multi if not isinstance(a, StartAction)]
        if not actions:
            if self._multi:
                self._toast("Start can't be copied.")
            return None
        items = action_items_to_json(actions)
        # Keep a group only when all of it was copied.
        for action, item in zip(actions, items):
            group = action.group
            if group is not None and not all(
                any(m is a for a in actions) for m in self._group_members(group)
            ):
                item.pop("group", None)
        return {"kind": "actions", "items": items}

    def copy_selection(self) -> bool:
        clip = self._selection_clip()
        if clip is None:
            return False
        _clipboard["data"] = clip
        self._toast(f"Copied {self._selection_label()}")
        self._rebuild_list()
        return True

    def cut_selection(self) -> bool:
        if self._selected_curve is not None:
            motion = self._selected_curve[0]
            picked = list(self._multi_curves)
            if len(picked) >= len(motion.motion.curves):
                self._toast("A motion needs at least one curve. Cut the motion instead.")
                return False
            label = self._selection_label()
            _clipboard["data"] = self._selection_clip()
            for i in sorted(picked, reverse=True):
                motion.motion.curves.pop(i)
            prev = self.previous_action(motion)
            motion.motion.enforce_g1(previous_pose=prev.end_pose() if prev is not None else None)
            first = min(picked)
            # Leave the curve before the gap selected so ⌘V puts them back.
            self._set_selection_state(motion, (motion, first - 1) if first > 0 else None)
            self._apply_selection(True)
            self.commit_history()
            self._toast(f"Cut {label}")
            return True

        clip = self._selection_clip()
        if clip is None:
            return False
        label = self._selection_label()
        removed = [a for a in self._multi if not isinstance(a, StartAction)]
        first_index = self.actions.index(removed[0])
        first_group = removed[0].group
        _clipboard["data"] = clip
        self.actions = [a for a in self.actions if not any(a is r for r in removed)]
        # Select what preceded the cut so ⌘V puts it back in place.
        anchor = self.actions[first_index - 1]
        if anchor.group is not None and anchor.group is not first_group:
            self.select_many(self._group_members(anchor.group), group=anchor.group)
        else:
            self.select(anchor)
        self.commit_history()
        self._toast(f"Cut {label}")
        return True

    def paste(self) -> bool:
        clip = _clipboard["data"]
        if clip is None:
            self._toast("Nothing to paste.")
            return False
        if clip["kind"] == "curves":
            return self._paste_curves(clip["items"])
        return self._paste_actions(clip["items"])

    def _paste_curves(self, items: list[dict]) -> bool:
        if self._selected_curve is not None:
            motion = self._selected_curve[0]
            at = max(self._multi_curves) + 1
        elif len(self._multi) == 1 and isinstance(self.selected, MotionAction):
            motion = self.selected
            at = len(motion.motion.curves)
        else:
            self._toast("Curves can only be pasted into a motion. Select a motion or one of its curves.")
            return False
        new = [curve_from_json(c) for c in items]
        motion.motion.curves[at:at] = new
        prev = self.previous_action(motion)
        motion.motion.enforce_g1(previous_pose=prev.end_pose() if prev is not None else None)
        self._propagate_g1_from_action(motion)
        self._set_selection_state(
            motion, (motion, at + len(new) - 1), curves=list(range(at, at + len(new)))
        )
        self._apply_selection(True)
        self.commit_history()
        return True

    def _paste_actions(self, items: list[dict]) -> bool:
        if self._selected_curve is not None:
            self._toast("Can't paste actions into a motion's curves. Select an action to paste after it.")
            return False
        new = action_items_from_json(items)
        if not new:
            return False
        has_groups = any(a.group is not None for a in new)
        index, join = len(self.actions), None
        if self._multi:
            last = self._multi[-1]
            index = self.actions.index(last) + 1
            group = last.group
            if group is not None:
                if self._selected_group is group or has_groups:
                    # Groups don't nest: land after the whole group.
                    index = self.actions.index(self._group_members(group)[-1]) + 1
                else:
                    join = group
        for action in new:
            if action.group is None:
                action.group = join
        self.actions[index:index] = new
        # Keep the path continuous into, through and out of the pasted block.
        for source in [self.previous_action(new[0]), *new]:
            if source is not None and source.end_pose() is not None:
                self._propagate_g1_from_action(source)
        first_group = new[0].group
        whole_group = (
            first_group is not None
            and join is None
            and all(a.group is first_group for a in new)
        )
        self.select_many(new, group=first_group if whole_group else None)
        self.commit_history()
        return True
