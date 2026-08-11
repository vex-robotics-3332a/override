from math import cos, radians, sin

import flet as ft

from motion import (
    FIELD_SIZE_IN,
    Curve,
    Motion,
    apply_g1_forward,
    apply_g1_from_pose,
    clamp_field_coord,
    clamp_field_point,
)
from playback import Timeline, build_routine_timeline
from profiling import profile_motion
from theme import (
    ACCENT,
    ACCENT_BORDER,
    ACCENT_CONTAINER,
    ACCENT_LIGHT,
    MONO_FONT,
    MONO_TEXT_STYLE,
    OUTLINED_GREEN_STYLE,
    RED_LIGHT,
    green_button,
    red_button,
    title_text,
)


POINT_LABELS = ("P0", "P1", "P2", "P3")

CREATABLE_ACTION_TYPES: dict[str, str] = {
    "Motion": "Cubic bezier path",
}

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

    @property
    def curve(self) -> Curve:
        return self.motion_action.motion.curves[self.curve_index]

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
        self._hovering = False
        self._dragging = False
        self._list_index = 0
        self._item = None

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

    def _index_badge(self) -> ft.Control:
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=7, vertical=3),
            border_radius=8,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            content=ft.Text(str(self._list_index), size=12, weight=ft.FontWeight.BOLD),
        )

    def _list_header_row(self) -> ft.Row:
        return ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=8,
                    controls=[
                        ft.Icon(self.icon, size=22, color=ft.Colors.ON_SURFACE_VARIANT),
                        title_text(self.name, size=TITLE_SIZE_LIST),
                    ],
                ),
                self._index_badge(),
            ],
        )

    def _item_body(self) -> ft.Control | None:
        """Content shown below the title row in list cards."""
        return None

    def build_list_item(self, reorderable: bool = False) -> ft.Control:
        delete_btn = None
        if self.deletable:
            delete_btn = ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                icon_color=RED_LIGHT,
                icon_size=20,
                tooltip="Delete",
                on_click=lambda e, action=self: action._handle_delete(e),
            )

        body_parts = [self._list_header_row()]
        item_body = self._item_body()
        if item_body is not None:
            body_parts.append(item_body)

        card_body = ft.Column(spacing=8, tight=True, controls=body_parts)

        card = ft.Container(
            expand=True,
            padding=12,
            border_radius=10,
            bgcolor=self._bgcolor(),
            border=self._border(),
            on_click=self._handle_click,
            on_hover=self._handle_hover,
            content=card_body,
        )
        self._item = card

        if not reorderable:
            row_controls = [card]
        else:
            row_controls = [
                ft.ReorderableDragHandle(
                    content=ft.Icon(ft.Icons.DRAG_HANDLE, color=ft.Colors.ON_SURFACE_VARIANT),
                ),
                card,
            ]

        if delete_btn is not None:
            row_controls.append(ft.Container(padding=ft.Padding.only(top=4), content=delete_btn))

        return ft.Row(
            key=self.key,
            spacing=4,
            opacity=0.55 if self._dragging else 1.0,
            vertical_alignment=ft.CrossAxisAlignment.START,
            controls=row_controls,
        )

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

    def _item_body(self) -> ft.Control:
        return ft.Column(
            spacing=4,
            controls=[
                ft.Text(self.type_name, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                _tag_line("Pose", f"({self.x:.1f}, {self.y:.1f})  {self.heading:.0f}°"),
                _tag_line("Time", f"{self.time:.0f} ms", muted=True),
            ],
        )

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

    def __init__(self, motion: Motion, name: str):
        super().__init__(name)
        self.motion = motion
        self.expanded = False
        self.on_select_motion = None
        self.on_select_curve = None
        self.on_add_curve = None
        self.on_delete_curve = None
        self.on_reorder_curves = None
        self.on_reorder_curves_start = None
        self.on_reorder_curves_end = None
        self._motion_card: ft.Container | None = None
        self._curves_panel: ft.Container | None = None
        self._curves_reorder_list: ft.ReorderableListView | None = None
        self._list_row: ft.Row | None = None
        self._dragging_curve_index: int | None = None
        self.recompute_profile()

    def recompute_profile(self) -> None:
        self.time = profile_motion(self.motion, self.name)

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

    def is_curve_selected(self, curve_index: int, selected_curve: tuple["MotionAction", int] | None) -> bool:
        return selected_curve == (self, curve_index)

    def _curve_bgcolor(self, curve_index: int, selected_curve: tuple["MotionAction", int] | None) -> str:
        if self.is_curve_selected(curve_index, selected_curve):
            return ACCENT_CONTAINER
        return ft.Colors.SURFACE_CONTAINER_HIGH

    def _curve_border(self, curve_index: int, selected_curve: tuple["MotionAction", int] | None):
        if self.is_curve_selected(curve_index, selected_curve):
            return ft.Border.all(2, ACCENT_BORDER)
        return ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)

    def _curve_summary(self, curve: Curve, curve_index: int) -> ft.Control:
        return ft.Column(
            spacing=2,
            controls=[
                ft.Text(f"Curve {curve_index + 1}", size=13, weight=ft.FontWeight.BOLD),
                _tag_line("Speed", f"{curve.max_speed:.0f}%", size=11),
            ],
        )

    def _motion_summary(self, on_add_curve, on_motion_click) -> ft.Control:
        count = len(self.motion.curves)
        curve_label = "curve" if count == 1 else "curves"
        return ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    expand=True,
                    on_click=on_motion_click,
                    content=ft.Column(
                        spacing=4,
                        tight=True,
                        controls=[
                            _tag_line("Total time", f"{self.time:.0f} ms", muted=True),
                            _tag_line("Curves", f"{count} {curve_label}", muted=True),
                        ],
                    ),
                ),
                ft.IconButton(
                    icon=ft.Icons.ADD,
                    icon_color=ft.Colors.GREEN_ACCENT_400,
                    tooltip="Add curve",
                    on_click=on_add_curve,
                ),
            ],
        )

    def _make_curve_row(
        self,
        curve: Curve,
        curve_index: int,
        selected_curve: tuple["MotionAction", int] | None,
        on_curve_click,
        on_delete_curve,
    ) -> ft.Control:
        def handle_click(e, index=curve_index):
            on_curve_click(index)

        card = ft.Container(
            expand=True,
            padding=10,
            border_radius=8,
            bgcolor=self._curve_bgcolor(curve_index, selected_curve),
            border=self._curve_border(curve_index, selected_curve),
            on_click=handle_click,
            content=self._curve_summary(curve, curve_index),
        )
        delete_btn = ft.IconButton(
            icon=ft.Icons.DELETE_OUTLINE,
            icon_color=RED_LIGHT,
            icon_size=18,
            tooltip="Delete curve",
            disabled=len(self.motion.curves) <= 1,
            on_click=lambda e, index=curve_index: on_delete_curve(index),
        )
        return ft.Row(
            key=curve.key,
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.START,
            opacity=0.55 if self._dragging_curve_index == curve_index else 1.0,
            controls=[
                ft.ReorderableDragHandle(
                    content=ft.Icon(
                        ft.Icons.DRAG_HANDLE,
                        size=18,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ),
                card,
                ft.Container(padding=ft.Padding.only(top=2), content=delete_btn),
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
        selected_curve: tuple["MotionAction", int] | None,
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
            self._curves_reorder_list.controls = [
                self._make_curve_row(curve, i, selected_curve, on_curve_click, on_delete_curve)
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
        selected_curve: tuple["MotionAction", int] | None,
        on_motion_click,
        on_add_curve,
        on_curve_click,
        on_delete_curve,
    ):
        self._motion_card.bgcolor = self._bgcolor()
        self._motion_card.border = self._border()
        self._motion_card.content = ft.Column(
            spacing=8,
            tight=True,
            controls=[
                ft.Container(
                    on_click=on_motion_click,
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Row(
                                spacing=8,
                                controls=[
                                    ft.Icon(self.icon, size=22, color=ft.Colors.ON_SURFACE_VARIANT),
                                    title_text(self.name, size=TITLE_SIZE_LIST),
                                ],
                            ),
                            self._index_badge(),
                        ],
                    ),
                ),
                self._motion_summary(on_add_curve, on_motion_click),
            ],
        )
        self._sync_curves_panel(selected_curve, on_curve_click, on_delete_curve)

    def _ensure_list_row(
        self,
        reorderable: bool,
        selected_curve: tuple["MotionAction", int] | None,
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
                padding=12,
                border_radius=10,
                on_hover=self._handle_hover,
            )

        self._sync_motion_card(
            selected_curve, on_motion_click, on_add_curve, on_curve_click, on_delete_curve
        )

        if self._list_row is None:
            delete_btn = ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                icon_color=RED_LIGHT,
                icon_size=20,
                tooltip="Delete motion",
                on_click=lambda e, action=self: action._handle_delete(e),
            )
            card_body = ft.Column(
                spacing=6,
                tight=True,
                controls=[self._motion_card, self._curves_panel],
            )
            card = ft.Container(expand=True, content=card_body)
            self._item = card

            row_controls = [card] if not reorderable else [
                ft.ReorderableDragHandle(
                    content=ft.Icon(ft.Icons.DRAG_HANDLE, color=ft.Colors.ON_SURFACE_VARIANT),
                ),
                card,
            ]
            row_controls.append(ft.Container(padding=ft.Padding.only(top=4), content=delete_btn))
            self._list_row = ft.Row(
                key=self.key,
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=row_controls,
            )
        else:
            self._list_row.opacity = 0.55 if self._dragging else 1.0

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
        selected_curve: tuple["MotionAction", int] | None = None,
    ) -> ft.Control:
        return self._ensure_list_row(reorderable, selected_curve)

    def build_motion_editor(self) -> ft.Control:
        def name_change(e):
            self.name = e.control.value or self.name
            self._notify_change(commit=False)

        def name_blur(e):
            self._notify_change(commit=True)

        def g1_change(e):
            self.motion.g1_continuity = bool(e.control.value)
            self._notify_change(commit=True)

        return ft.Column(
            spacing=10,
            controls=[
                _text_field("Name", self.name, name_change, on_blur=name_blur, width=LEFT_PANEL_WIDTH),
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


class ActionList:
    def __init__(self, page: ft.Page, on_scene_change=None, on_playback_pose=None):
        self.page = page
        self.on_scene_change = on_scene_change
        self.on_playback_pose = on_playback_pose
        self.actions: list[RobotAction] = [StartAction()]
        self.selected: RobotAction | None = None
        self._selected_curve: tuple[MotionAction, int] | None = None
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
        self._play_button = ft.IconButton(
            icon=ft.Icons.PLAY_ARROW,
            icon_color=ft.Colors.GREEN_ACCENT_400,
            tooltip="Play",
            on_click=self._toggle_playback,
        )
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
                            self._time_label,
                            self._duration_label,
                        ],
                    ),
                    self._time_slider,
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
                    green_button("New action", icon=ft.Icons.ADD, on_click=self._show_new_action_dialog),
                    ft.Container(expand=True, content=self._reorder_list),
                    self._simulate_bar,
                ],
            ),
        )

        # Outer chrome (padding / page background) is owned by Sidebar.
        self.control = self._actions_section

        self._set_selection_state(self.actions[0], None)
        self._apply_selection(notify=False, animate=False)
        self._refresh_timeline(emit_pose=False)

    def set_routine_name(self, name: str):
        self._routine_tag.value = f"Editing {name}" if name else ""
        if _is_mounted(self._routine_tag):
            self._routine_tag.update()

    def stop_playback(self):
        self._playing = False
        self._playback_task = None
        self._play_button.icon = ft.Icons.PLAY_ARROW
        self._play_button.tooltip = "Play"
        if _is_mounted(self._play_button):
            self._play_button.update()

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
        if _is_mounted(self._play_button):
            self._play_button.update()

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
            self.stop_playback()
            self._sync_playback_chrome()
            return
        if self._play_time_ms >= self._timeline.duration_ms:
            self._seek(0.0, update_chrome=True)
        self._playing = True
        self._play_button.icon = ft.Icons.PAUSE
        self._play_button.tooltip = "Pause"
        if _is_mounted(self._play_button):
            self._play_button.update()
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
            if nxt >= self._timeline.duration_ms:
                self._seek(self._timeline.duration_ms, update_chrome=True)
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
        idx = self.actions.index(action)
        return self.actions[idx - 1] if idx > 0 else None

    def _motion_index(self, action: RobotAction) -> int:
        motion_idx = 0
        for a in self.actions:
            if isinstance(a, MotionAction):
                motion_idx += 1
                if a is action:
                    return motion_idx
        return 0

    def _set_selection_state(self, action: RobotAction | None, curve: tuple[MotionAction, int] | None):
        self.selected = action
        self._selected_curve = curve
        for a in self.actions:
            a.selected = a is action
            if isinstance(a, MotionAction):
                a.expanded = a is action

    def _editor_content(self) -> ft.Control | None:
        if self.selected is None:
            return None
        if self._selected_curve is not None:
            motion_action, curve_index = self._selected_curve
            curve = motion_action.motion.curves[curve_index]
            return motion_action.build_curve_editor(
                curve,
                curve_index,
                self.previous_action(motion_action),
            )
        if isinstance(self.selected, MotionAction):
            return self.selected.build_motion_editor()
        return self.selected.build_editor(self.previous_action(self.selected))

    def _editor_title(self) -> str:
        if self._selected_curve is not None:
            _, curve_index = self._selected_curve
            return f"Curve {curve_index + 1}"
        if self.selected is not None:
            return self.selected.type_name
        return ""

    def select_motion(self, action: MotionAction, notify: bool = True):
        if self.selected is action and self._selected_curve is None:
            return
        self._set_selection_state(action, None)
        self._apply_selection(notify)

    def select_curve(self, motion_action: MotionAction, curve_index: int, notify: bool = True):
        if self._selected_curve == (motion_action, curve_index):
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

    def _apply_editor_content(self, title: str, editor: ft.Control | None):
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
            self._editor_shell.visible = True
            title = self._editor_title()
            editor = self._editor_content()
            if animate and self._editor_body.opacity == 1.0:
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
        if idx + 1 >= len(self.actions):
            return
        nxt = self.actions[idx + 1]
        if isinstance(nxt, MotionAction) and nxt.motion.g1_continuity:
            nxt.motion.enforce_g1(previous_pose=action.end_pose())

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

    def _wire_action(self, action: RobotAction):
        action.on_select = self.select
        action.on_delete = self._confirm_delete
        action.on_change = self._handle_action_change
        action.on_hover_change = self._handle_hover_change
        if isinstance(action, MotionAction):
            action.on_select_motion = self.select_motion
            action.on_select_curve = self.select_curve
            action.on_add_curve = self._add_curve
            action.on_delete_curve = self._delete_curve
            action.on_reorder_curves = self._handle_curve_reorder
            action.on_reorder_curves_start = self._handle_curve_reorder_start
            action.on_reorder_curves_end = self._handle_curve_reorder_end

    def _handle_hover_change(self, action: RobotAction, hovering: bool):
        changed = False
        for a in self.actions:
            new_hover = a is action and hovering
            if a._hovering != new_hover:
                changed = True
            a._hovering = new_hover
        if changed:
            self._rebuild_list()

    def _rebuild_list(self, *, flush: bool = True):
        items = []
        motion_idx = 0
        for action in self.actions:
            if isinstance(action, MotionAction):
                motion_idx += 1
                action._list_index = motion_idx
            else:
                action._list_index = 0
            self._wire_action(action)
            if isinstance(action, MotionAction):
                items.append(
                    action._ensure_list_row(
                        reorderable=True,
                        selected_curve=self._selected_curve,
                    )
                )
            else:
                items.append(action.build_list_item(reorderable=False))
        self._reorder_list.controls = items
        if flush and _is_mounted(self._reorder_list):
            self._reorder_list.update()

    def _handle_action_change(self, action: RobotAction, *, commit: bool = False):
        if isinstance(action, MotionAction) and action.motion.g1_continuity:
            prev = self.previous_action(action)
            pose = prev.end_pose() if prev is not None else None
            action.motion.enforce_g1(previous_pose=pose)
        elif isinstance(action, StartAction):
            self._propagate_g1_from_action(action)
        profile_action = action if isinstance(action, MotionAction) else None
        self._notify_scene(profile_action=profile_action)
        if commit:
            self._rebuild_list()
            if self.selected is action:
                self.refresh_editor()

    def _profile_all_motions(self):
        for action in self.motion_actions():
            action.recompute_profile()

    def reprofile(self, *, refresh_ui: bool = True):
        """Recompute all motion profiles (e.g. after robot specs change)."""
        self._profile_all_motions()
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
        self._refresh_timeline(emit_pose=True)
        if self.on_scene_change is not None:
            self.on_scene_change()

    def _next_motion_name(self) -> str:
        return f"Motion {len(self.motion_actions()) + 1}"

    def _add_motion(self):
        previous = self.actions[-1] if self.actions else None
        prev_pose = previous.end_pose() if previous is not None else None
        motion = Motion(curves=[Curve(points=default_curve_points(prev_pose))])
        if motion.g1_continuity:
            motion.enforce_g1(previous_pose=prev_pose)
        action = MotionAction(motion, self._next_motion_name())
        self.actions.append(action)
        self.select_curve(action, 0)

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
            self._apply_selection(notify=True, animate=False)
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
            if self.selected is action or (
                isinstance(action, MotionAction)
                and self._selected_curve is not None
                and self._selected_curve[0] is action
            ):
                self.select(self.actions[min(idx, len(self.actions) - 1)])
            else:
                self._rebuild_list()
                self._notify_scene()

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

        def add_motion(ev):
            self._close_modal()
            self._add_motion()

        tile = ft.Container(
            padding=ft.Padding.symmetric(vertical=8),
            content=ft.Row(
                spacing=12,
                controls=[
                    ft.Icon(ft.Icons.ROUTE),
                    ft.Column(
                        spacing=2,
                        controls=[
                            ft.Text("Motion", weight=ft.FontWeight.BOLD),
                            ft.Text(CREATABLE_ACTION_TYPES["Motion"], size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ],
                    ),
                ],
            ),
            on_click=add_motion,
            ink=True,
        )

        self._show_modal(
            "New action",
            ft.Container(width=300, content=tile),
            [ft.OutlinedButton("Cancel", on_click=close)],
        )

    def _handle_reorder_start(self, e: ft.OnReorderEvent):
        idx = e.old_index
        if idx is None or idx <= 0 or idx >= len(self.actions):
            return
        self.actions[idx]._dragging = True
        self._rebuild_list()

    def _handle_reorder_end(self, e: ft.OnReorderEvent):
        for action in self.actions:
            action._dragging = False
        self._rebuild_list()

    def _handle_reorder(self, e: ft.OnReorderEvent):
        if e.old_index == 0 or e.new_index == 0:
            self._rebuild_list()
            return
        items = self.actions[:]
        item = items.pop(e.old_index)
        items.insert(e.new_index, item)
        self.actions = items
        for action in self.actions:
            action._dragging = False
        self._rebuild_list()
        self._notify_scene()
