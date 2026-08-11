import flet as ft

from actions import ActionList, _is_mounted, _num_field, _text_field
from robot import RobotSpecs, get_robot_specs, set_robot_specs
from theme import (
    ACCENT_BORDER,
    ACCENT_CONTAINER,
    TITLE_FONT,
    green_button,
    red_button,
    title_text,
)

MODE_ACTIONS = "actions"
MODE_ROUTINES = "routines"
MODE_ROBOT = "robot"
MODE_VISUALIZE = "visualize"

MODE_LABELS = {
    MODE_ACTIONS: "Actions",
    MODE_ROUTINES: "Routines",
    MODE_ROBOT: "Robot",
    MODE_VISUALIZE: "Visualize",
}

MODE_ICONS = {
    MODE_ACTIONS: ft.Icons.LIST_ALT,
    MODE_ROUTINES: ft.Icons.FOLDER_OUTLINED,
    MODE_ROBOT: ft.Icons.PRECISION_MANUFACTURING,
    MODE_VISUALIZE: ft.Icons.TIMELINE,
}


class Routine:
    """One editable routine: a name, optional export path, and its action list."""

    def __init__(self, name: str, path: str = ""):
        self.name = name
        self.path = path
        self.action_list: ActionList | None = None


class Sidebar:
    """Right-hand panel with a top mode selector and swapable content."""

    def __init__(self, page: ft.Page, on_scene_change=None):
        self.page = page
        self.on_scene_change = on_scene_change
        self.mode = MODE_ACTIONS
        self.visualize_path = ""
        self._modal: ft.Control | None = None
        self._routine_counter = 1
        self.robot_specs = get_robot_specs().copy()
        self._robot_draft = self.robot_specs.copy()
        self.on_playback_pose = None

        first = Routine(f"Routine {self._routine_counter}")
        first.action_list = ActionList(
            page,
            on_scene_change=on_scene_change,
            on_playback_pose=self._forward_playback_pose,
        )
        first.action_list.set_routine_name(first.name)
        self.routines: list[Routine] = [first]
        self.selected_routine_index = 0

        self._selector = ft.Row(
            spacing=4,
            controls=[self._make_mode_chip(mode) for mode in MODE_LABELS],
        )

        self._content_host = ft.Container(expand=True)
        self._routines_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        self._visualize_path_field = _text_field(
            "Path",
            self.visualize_path,
            self._on_visualize_path_change,
        )

        self.control = ft.Container(
            expand=True,
            padding=16,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            content=ft.Column(
                spacing=12,
                expand=True,
                controls=[
                    self._selector,
                    self._content_host,
                ],
            ),
        )

        self._show_mode(MODE_ACTIONS, flush=False)

    @property
    def action_list(self) -> ActionList:
        return self.routines[self.selected_routine_index].action_list

    @property
    def selected_routine(self) -> Routine:
        return self.routines[self.selected_routine_index]

    def _make_mode_chip(self, mode: str) -> ft.Container:
        selected = mode == self.mode
        color = ft.Colors.WHITE if selected else ft.Colors.ON_SURFACE_VARIANT
        return ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=8, vertical=10),
            border_radius=8,
            bgcolor=ACCENT_CONTAINER if selected else ft.Colors.SURFACE_CONTAINER,
            border=ft.Border.all(1, ACCENT_BORDER if selected else ft.Colors.OUTLINE_VARIANT),
            alignment=ft.Alignment(0, 0),
            ink=True,
            on_click=lambda e, m=mode: self._set_mode(m),
            content=ft.Row(
                spacing=4,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
                controls=[
                    ft.Icon(MODE_ICONS[mode], size=16, color=color),
                    ft.Text(
                        MODE_LABELS[mode],
                        size=12,
                        font_family=TITLE_FONT,
                        italic=True,
                        weight=ft.FontWeight.BOLD,
                        color=color,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
            ),
        )

    def _refresh_selector(self):
        self._selector.controls = [self._make_mode_chip(mode) for mode in MODE_LABELS]
        if _is_mounted(self._selector):
            self._selector.update()

    def _set_mode(self, mode: str):
        if mode == self.mode:
            return
        self.mode = mode
        self._refresh_selector()
        self._show_mode(mode)

    def _show_mode(self, mode: str, *, flush: bool = True):
        if mode == MODE_ACTIONS:
            self.action_list.set_routine_name(self.selected_routine.name)
            self._content_host.content = self.action_list.control
        elif mode == MODE_ROUTINES:
            self._rebuild_routines_panel()
            self._content_host.content = self._routines_panel()
        elif mode == MODE_ROBOT:
            self._robot_draft = self.robot_specs.copy()
            self._content_host.content = self._robot_panel()
        else:
            self._content_host.content = self._visualize_panel()

        if flush and _is_mounted(self._content_host):
            self._content_host.update()

    def _forward_playback_pose(self, pose):
        if self.on_playback_pose is not None:
            self.on_playback_pose(pose)

    def _routines_panel(self) -> ft.Control:
        return ft.Container(
            expand=True,
            padding=16,
            border_radius=12,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            content=ft.Column(
                spacing=12,
                expand=True,
                controls=[
                    title_text("Routines", size=22),
                    ft.Text(
                        "Select a routine to edit, or add a new one.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Divider(height=1),
                    green_button(
                        "New routine",
                        icon=ft.Icons.ADD,
                        on_click=lambda e: self._add_routine(),
                    ),
                    self._routines_list,
                ],
            ),
        )

    def _robot_panel(self) -> ft.Control:
        draft = self._robot_draft

        def set_field(attr: str):
            def on_apply(v: float):
                setattr(draft, attr, max(0.0, v))

            return on_apply

        return ft.Container(
            expand=True,
            padding=16,
            border_radius=12,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            content=ft.Column(
                spacing=12,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    title_text("Robot", size=22),
                    ft.Text(
                        "Changes apply only after confirmation.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Divider(height=1),
                    ft.Text("Profiling", size=14, weight=ft.FontWeight.BOLD),
                    _num_field("Max speed (in/s)", draft.max_speed, set_field("max_speed"), width=None),
                    _num_field("Max accel (in/s²)", draft.max_accel, set_field("max_accel"), width=None),
                    _num_field("Track width (in)", draft.track_width, set_field("track_width"), width=None),
                    ft.Divider(height=1),
                    ft.Text("Body size", size=14, weight=ft.FontWeight.BOLD),
                    _num_field("Length X (in)", draft.length, set_field("length"), width=None),
                    _num_field("Width Y (in)", draft.width, set_field("width"), width=None),
                    ft.Container(height=4),
                    green_button("Apply changes", on_click=lambda e: self._confirm_apply_robot()),
                ],
            ),
        )

    def _confirm_apply_robot(self):
        draft = self._robot_draft

        def cancel(e):
            self._close_modal()

        def confirm(e):
            self._close_modal()
            self.robot_specs = draft.copy()
            set_robot_specs(self.robot_specs)
            for routine in self.routines:
                if routine.action_list is not None:
                    routine.action_list.reprofile(refresh_ui=False)
            current = self.action_list
            current._rebuild_list()
            if current.selected is not None:
                current.refresh_editor()
            if self.on_scene_change is not None:
                self.on_scene_change()

        self._show_modal(
            "Apply robot specs?",
            ft.Container(
                width=300,
                content=ft.Text(
                    "Update robot specs and re-profile all routines?\n\n"
                    f"Max speed: {draft.max_speed:g} in/s\n"
                    f"Max accel: {draft.max_accel:g} in/s²\n"
                    f"Track width: {draft.track_width:g} in\n"
                    f"Length X: {draft.length:g} in\n"
                    f"Width Y: {draft.width:g} in"
                ),
            ),
            [
                ft.OutlinedButton("Cancel", on_click=cancel),
                green_button("Apply", on_click=confirm),
            ],
        )

    def _visualize_panel(self) -> ft.Control:
        return ft.Container(
            expand=True,
            padding=16,
            border_radius=12,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            content=ft.Column(
                spacing=12,
                expand=True,
                controls=[
                    title_text("Visualize", size=22),
                    ft.Divider(height=1),
                    self._visualize_path_field,
                ],
            ),
        )

    def _on_visualize_path_change(self, e):
        self.visualize_path = e.control.value or ""

    def _rebuild_routines_panel(self):
        self._routines_list.controls = [
            self._make_routine_card(i, routine) for i, routine in enumerate(self.routines)
        ]

    def _make_routine_card(self, index: int, routine: Routine) -> ft.Control:
        selected = index == self.selected_routine_index

        def select_routine(e, idx=index):
            self._select_routine(idx)

        def name_change(e, r=routine, idx=index):
            r.name = e.control.value or r.name
            if idx == self.selected_routine_index and r.action_list is not None:
                r.action_list.set_routine_name(r.name)

        def path_change(e, r=routine):
            r.path = e.control.value or ""

        def delete_routine(e, idx=index):
            self._confirm_delete_routine(idx)

        header = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                title_text(routine.name or f"Routine {index + 1}", size=16),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_color=ft.Colors.RED_400,
                    icon_size=20,
                    tooltip="Delete routine",
                    disabled=len(self.routines) <= 1,
                    on_click=delete_routine,
                ),
            ],
        )

        return ft.Container(
            padding=12,
            border_radius=10,
            bgcolor=ACCENT_CONTAINER if selected else ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border=ft.Border.all(2 if selected else 1, ACCENT_BORDER if selected else ft.Colors.OUTLINE_VARIANT),
            on_click=select_routine,
            content=ft.Column(
                spacing=10,
                tight=True,
                controls=[
                    header,
                    _text_field("Name", routine.name, name_change),
                    _text_field("Path", routine.path, path_change),
                ],
            ),
        )

    def _ensure_action_list(self, routine: Routine) -> ActionList:
        if routine.action_list is None:
            routine.action_list = ActionList(
                self.page,
                on_scene_change=self.on_scene_change,
                on_playback_pose=self._forward_playback_pose,
            )
        else:
            routine.action_list.on_playback_pose = self._forward_playback_pose
        routine.action_list.set_routine_name(routine.name)
        return routine.action_list

    def _select_routine(self, index: int):
        if index < 0 or index >= len(self.routines):
            return
        if index == self.selected_routine_index:
            self._rebuild_routines_panel()
            if _is_mounted(self._routines_list):
                self._routines_list.update()
            return

        previous = self.routines[self.selected_routine_index]
        if previous.action_list is not None:
            previous.action_list.stop_playback()
        self.selected_routine_index = index
        self._ensure_action_list(self.selected_routine)
        self._rebuild_routines_panel()
        if _is_mounted(self._routines_list):
            self._routines_list.update()
        if self.on_scene_change is not None:
            self.on_scene_change()
        if self.on_playback_pose is not None:
            self.on_playback_pose(None)

    def _add_routine(self):
        current = self.action_list
        if current is not None:
            current.stop_playback()
        self._routine_counter += 1
        routine = Routine(f"Routine {self._routine_counter}")
        self._ensure_action_list(routine)
        self.routines.append(routine)
        self.selected_routine_index = len(self.routines) - 1
        self._rebuild_routines_panel()
        if _is_mounted(self._routines_list):
            self._routines_list.update()
        if self.on_scene_change is not None:
            self.on_scene_change()
        if self.on_playback_pose is not None:
            self.on_playback_pose(None)

    def _confirm_delete_routine(self, index: int):
        if len(self.routines) <= 1 or index < 0 or index >= len(self.routines):
            return

        routine = self.routines[index]

        def cancel(e):
            self._close_modal()

        def confirm(e):
            self._close_modal()
            self.routines.pop(index)
            if self.selected_routine_index >= len(self.routines):
                self.selected_routine_index = len(self.routines) - 1
            elif self.selected_routine_index > index:
                self.selected_routine_index -= 1
            self._ensure_action_list(self.selected_routine)
            self._rebuild_routines_panel()
            if self.mode == MODE_ROUTINES and _is_mounted(self._routines_list):
                self._routines_list.update()
            elif self.mode == MODE_ACTIONS:
                self._show_mode(MODE_ACTIONS)
            if self.on_scene_change is not None:
                self.on_scene_change()

        self._show_modal(
            "Delete routine?",
            ft.Container(
                width=300,
                content=ft.Text(f'Remove "{routine.name}" and all of its actions?'),
            ),
            [
                ft.OutlinedButton("Cancel", on_click=cancel),
                red_button("Delete", on_click=confirm),
            ],
        )

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
