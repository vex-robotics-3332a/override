import flet as ft

from actions import ActionList, _is_mounted, _num_field, _text_field
from capture import load_pose_capture
from history import ActionHistory
from playback import Timeline, build_routine_timeline
from robot import RobotSpecs, get_robot_specs, set_robot_specs
from serialize import routines_to_json
from storage import (
    AUTOSAVE_DELAY_S,
    allocate_file_id,
    delete_routine_file,
    fingerprint_routine,
    is_valid_routine_name,
    list_routine_files,
    load_index,
    parse_routine_file,
    rename_routine_file,
    resolve_routine_path,
    save_index,
    save_routine_file,
)
from theme import (
    ACCENT_BORDER,
    ACCENT_CONTAINER,
    TITLE_FONT,
    green_button,
    red_button,
    title_text,
)

import sys
from pathlib import Path

_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from settings import (
    get_code_root,
    get_field_background,
    get_sd_card_path,
    save_code_root,
    save_field_background,
    save_sd_card_path,
)

MODE_ACTIONS = "actions"
MODE_ROUTINES = "routines"
MODE_SETTINGS = "settings"
MODE_VISUALIZE = "visualize"

MODE_LABELS = {
    MODE_ACTIONS: "Actions",
    MODE_ROUTINES: "Routines",
    MODE_SETTINGS: "Settings",
    MODE_VISUALIZE: "Visualize",
}

MODE_ICONS = {
    MODE_ACTIONS: ft.Icons.LIST_ALT,
    MODE_ROUTINES: ft.Icons.FOLDER_OUTLINED,
    MODE_SETTINGS: ft.Icons.SETTINGS,
    MODE_VISUALIZE: ft.Icons.TIMELINE,
}


class Routine:
    """One editable routine: a name, optional export path, and its action list.

    Persistence:
    - on-disk file ``routines/<file_id>.json`` (typed actions for parsing)
    - in-memory ``history`` for undo/redo (not written to disk)
    """

    def __init__(
        self,
        name: str,
        path: str = "",
        history: ActionHistory | None = None,
        file_id: str | None = None,
    ):
        from storage import sanitize_routine_name

        stem = sanitize_routine_name(file_id or name or "routine")
        self.name = stem
        self.path = path
        self.history = history or ActionHistory()
        self.file_id = stem
        self.action_list: ActionList | None = None
        self.dirty = False
        self.saved_fingerprint = ""
        self._autosave_token = 0

    @property
    def actions_doc(self) -> dict:
        if self.action_list is not None:
            return self.action_list.actions_doc
        return self.history.current

    @property
    def history_doc(self) -> dict:
        return self.history.to_json()


class Sidebar:
    """Right-hand panel with a top mode selector and swapable content."""

    def __init__(self, page: ft.Page, on_scene_change=None):
        self.page = page
        self.on_scene_change = on_scene_change
        self.mode = MODE_ACTIONS
        self._modal: ft.Control | None = None
        self.robot_specs = get_robot_specs().copy()
        self._robot_draft = self.robot_specs.copy()
        self.on_playback_pose = None
        self.on_visualize_frame = None
        self.on_field_background_change = None
        self.code_root = get_code_root()
        self.sd_card_path = get_sd_card_path()
        self.field_background = get_field_background()
        self._field_bg_row = ft.Row(spacing=8)
        # Visualize tab: capture CSV vs current routine desired timeline.
        default_capture = ""
        if self.sd_card_path.strip():
            default_capture = str(Path(self.sd_card_path).expanduser() / "pose_capture.txt")
        self.visualize_path = default_capture
        self._capture_timeline: Timeline | None = None
        self._capture_path: list[tuple[float, float]] = []
        self._desired_timeline: Timeline | None = None
        self._viz_play_time_ms = 0.0
        self._viz_playing = False
        self._viz_playback_task = None
        self._viz_slider_dragging = False
        self._viz_loaded = False

        self.routines: list[Routine] = []
        self.selected_routine_index = 0
        self._routine_counter = 1
        self._load_routines_from_disk()

        self._selector = ft.Row(
            spacing=4,
            controls=[self._make_mode_chip(mode) for mode in MODE_LABELS],
        )

        self._content_host = ft.Container(expand=True)
        self._routines_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        self._code_root_field = _text_field(
            "Master path (robot code root)",
            self.code_root,
            self._on_code_root_change,
            on_blur=self._on_code_root_blur,
        )
        self._sd_card_field = _text_field(
            "SD card path (motion profiles)",
            self.sd_card_path,
            self._on_sd_card_change,
            on_blur=self._on_sd_card_blur,
        )
        self._visualize_path_field = _text_field(
            "Pose capture CSV",
            self.visualize_path,
            self._on_visualize_path_change,
        )
        self._viz_status = ft.Text(
            "Load a pose_capture CSV to compare desired vs actual.",
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self._viz_play_button = ft.IconButton(
            icon=ft.Icons.PLAY_ARROW,
            tooltip="Play",
            disabled=True,
            on_click=self._viz_toggle_playback,
        )
        self._viz_time_label = ft.Text("0.00 s", size=12, width=56)
        self._viz_duration_label = ft.Text("/ 0.00 s", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._viz_time_slider = ft.Slider(
            min=0,
            max=1,
            value=0,
            disabled=True,
            on_change=self._viz_on_slider_change,
            on_change_start=self._viz_on_slider_drag_start,
            on_change_end=self._viz_on_slider_drag_end,
        )
        self._viz_load_button = green_button("Load", on_click=self._viz_load_capture)

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

    def handle_keyboard(self, e: ft.KeyboardEvent) -> bool:
        if not self.routines:
            return False
        action_list = self.selected_routine.action_list
        if action_list is None:
            return False
        return action_list.handle_keyboard(e)

    def _load_routines_from_disk(self):
        index = load_index()
        loaded: list[Routine] = []
        for path in list_routine_files():
            parsed = parse_routine_file(path)
            if parsed is None:
                continue
            file_id, name, export_path, actions_doc = parsed
            routine = Routine(
                name=name,
                path=export_path,
                history=ActionHistory(actions_doc),
                file_id=file_id,
            )
            loaded.append(routine)

        if not loaded:
            file_id = allocate_file_id(set())
            routine = Routine(file_id, file_id=file_id)
            self.routines = [routine]
            self.selected_routine_index = 0
            self._routine_counter = 1
            self._ensure_action_list(routine)
            self.save_routine(routine, manual=True)
            self._persist_index()
            return

        self.routines = loaded
        selected_id = index.get("selected")
        selected_index = 0
        if selected_id:
            for i, routine in enumerate(self.routines):
                if routine.file_id == selected_id:
                    selected_index = i
                    break
        self.selected_routine_index = selected_index

        max_n = 0
        for routine in self.routines:
            if routine.file_id.startswith("routine_"):
                try:
                    max_n = max(max_n, int(routine.file_id.split("_", 1)[1]))
                except ValueError:
                    pass
        self._routine_counter = max(max_n, int(index.get("next_id") or 1) - 1, len(self.routines))

        for routine in self.routines:
            self._ensure_action_list(routine)
            # Keep on-disk name field synced to the filename stem.
            self.save_routine(routine, manual=False)

    def _persist_index(self):
        save_index(
            selected_file_id=self.selected_routine.file_id if self.routines else None,
            next_id=self._routine_counter + 1,
        )

    def _wire_action_list(self, routine: Routine, action_list: ActionList):
        action_list.on_scene_change = self.on_scene_change
        action_list.on_playback_pose = self._forward_playback_pose
        action_list.on_dirty = lambda r=routine: self._on_routine_dirty(r)
        action_list.on_save = lambda r=routine: self.save_routine(r, manual=True)
        action_list.on_export = lambda r=routine: self.export_routine(r)
        action_list.set_routine_name(routine.name)
        action_list.set_dirty(routine.dirty)

    def _ensure_action_list(self, routine: Routine) -> ActionList:
        if routine.action_list is None:
            routine.action_list = ActionList(
                self.page,
                on_scene_change=self.on_scene_change,
                on_playback_pose=self._forward_playback_pose,
                history=routine.history,
                on_dirty=lambda r=routine: self._on_routine_dirty(r),
                on_save=lambda r=routine: self.save_routine(r, manual=True),
                on_export=lambda r=routine: self.export_routine(r),
            )
        else:
            self._wire_action_list(routine, routine.action_list)
        routine.action_list.set_routine_name(routine.name)
        return routine.action_list

    def _on_routine_dirty(self, routine: Routine):
        fp = fingerprint_routine(routine)
        routine.dirty = fp != routine.saved_fingerprint
        if routine.action_list is not None:
            routine.action_list.set_dirty(routine.dirty)
        if routine.dirty:
            self._schedule_autosave(routine)

    def _schedule_autosave(self, routine: Routine):
        routine._autosave_token += 1
        token = routine._autosave_token

        async def _run():
            import asyncio

            await asyncio.sleep(AUTOSAVE_DELAY_S)
            if token != routine._autosave_token:
                return
            if not routine.dirty:
                return
            self.save_routine(routine, manual=False)

        self.page.run_task(_run)

    def save_routine(self, routine: Routine | None = None, *, manual: bool = False) -> bool:
        routine = routine or self.selected_routine
        try:
            save_routine_file(routine)
        except OSError:
            if routine.action_list is not None:
                routine.action_list._save_status.value = "Save failed"
                routine.action_list._save_status.color = ft.Colors.RED_400
                if _is_mounted(routine.action_list._save_status):
                    routine.action_list._save_status.update()
            return False

        routine.saved_fingerprint = fingerprint_routine(routine)
        routine.dirty = False
        if routine.action_list is not None:
            routine.action_list.set_dirty(False)
            if manual:
                routine.action_list._save_status.value = "Saved"
                routine.action_list._save_status.color = ft.Colors.ON_SURFACE_VARIANT
                if _is_mounted(routine.action_list._save_status):
                    routine.action_list._save_status.update()
        self._persist_index()
        return True

    def save_selected_routine(self) -> bool:
        return self.save_routine(self.selected_routine, manual=True)

    def export_routine(self, routine: Routine | None = None):
        """Save, run the parser, and write motion profiles (SD or local fallback)."""
        from export import export_routine as run_export

        routine = routine or self.selected_routine
        if not self.save_routine(routine, manual=True):
            self._show_export_result(
                "Export failed",
                "Save failed — export canceled.",
            )
            return

        result = run_export(
            routine,
            code_root=self.code_root,
            sd_card_path=self.sd_card_path,
        )
        if not result.ok:
            title = "Export failed"
        elif result.used_sd_fallback:
            title = "Export complete (SD fallback)"
        else:
            title = "Export complete"
        self._show_export_result(title, result.summary)
        # Refresh UI times after recompute during export.
        if routine.action_list is not None:
            routine.action_list._rebuild_list()
            if routine.action_list.selected is not None:
                routine.action_list.refresh_editor()
            if self.on_scene_change is not None:
                self.on_scene_change()

    def _show_export_result(self, title: str, message: str):
        def close(e):
            self._close_modal()

        self._show_modal(
            title,
            ft.Container(
                width=420,
                content=ft.Text(message, size=13, selectable=True),
            ),
            [ft.OutlinedButton("OK", on_click=close)],
        )

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
        prev = self.mode
        self.mode = mode
        if prev == MODE_VISUALIZE and mode != MODE_VISUALIZE:
            self._viz_leave()
        if mode == MODE_VISUALIZE:
            self.action_list.stop_playback()
            if self.on_playback_pose is not None:
                self.on_playback_pose(None)
        self._refresh_selector()
        self._show_mode(mode)

    def _show_mode(self, mode: str, *, flush: bool = True):
        if mode == MODE_ACTIONS:
            self.action_list.set_routine_name(self.selected_routine.name)
            self.action_list.set_dirty(self.selected_routine.dirty)
            self._content_host.content = self.action_list.control
        elif mode == MODE_ROUTINES:
            self._rebuild_routines_panel()
            self._content_host.content = self._routines_panel()
        elif mode == MODE_SETTINGS:
            self._robot_draft = self.robot_specs.copy()
            self._content_host.content = self._settings_panel()
        else:
            self._content_host.content = self._visualize_panel()
            if self._viz_loaded:
                self._viz_refresh_desired()
                self._viz_sync_chrome(disabled=False)
                self._viz_emit_frame()

        if flush and _is_mounted(self._content_host):
            self._content_host.update()

    def _forward_playback_pose(self, pose):
        if self.mode == MODE_VISUALIZE:
            return
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
                        "Stored as JSON in path_planner/routines/. "
                        "Relative paths use the master path from Settings.",
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

    def _settings_panel(self) -> ft.Control:
        draft = self._robot_draft

        def set_field(attr: str):
            def on_apply(v: float):
                setattr(draft, attr, max(0.0, v))

            return on_apply

        def set_signed_field(attr: str):
            def on_apply(v: float):
                setattr(draft, attr, v)

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
                    title_text("Settings", size=22),
                    ft.Text(
                        "Project and robot configuration.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Divider(height=1),
                    ft.Text("Paths", size=14, weight=ft.FontWeight.BOLD),
                    self._code_root_field,
                    ft.Text(
                        "Master path to the robot code on this computer. "
                        "Routine relative paths resolve from here (stored in settings.json).",
                        size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    self._sd_card_field,
                    ft.Text(
                        "Root folder for motion profile JSONs. Each routine is written "
                        "as a subdirectory: {sd_card}/{routine}/motionN.json.",
                        size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Divider(height=1),
                    ft.Text("Field background", size=14, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "Swap the planner field image between Match and Skills.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    self._field_background_selector(),
                    ft.Divider(height=1),
                    ft.Text("Robot profiling", size=14, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "Changes apply only after confirmation.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    _num_field("Max speed v_max (in/s)", draft.max_speed, set_field("max_speed"), width=None),
                    _num_field("Stall accel a₀ (in/s²)", draft.max_accel, set_field("max_accel"), width=None),
                    ft.Text(
                        (
                            f"First-order model: a(v)=a₀·(1−|v|/v_max), "
                            f"τ=v_max/a₀={draft.max_speed / max(draft.max_accel, 1e-9):.3f} s. "
                            "Braking uses a=−a₀·(1+|v|/v_max)."
                        ),
                        size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    _num_field("Track width (in)", draft.track_width, set_field("track_width"), width=None),
                    _num_field(
                        "Accel curvature gain",
                        draft.accel_curvature_gain,
                        set_field("accel_curvature_gain"),
                        width=None,
                    ),
                    ft.Text(
                        "Scales a₀ with curvature: a₀(κ)=a₀/(1+gain·|κ|·track/2). "
                        "0 = ignore curvature; 1 ≈ wheel force split; >1 adds inertia.",
                        size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Divider(height=1),
                    ft.Text("Body size", size=14, weight=ft.FontWeight.BOLD),
                    _num_field("Length X (in)", draft.length, set_field("length"), width=None),
                    _num_field("Width Y (in)", draft.width, set_field("width"), width=None),
                    ft.Divider(height=1),
                    ft.Text("Turning center offset", size=14, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "Where the robot turns about, relative to the body's center. "
                        "+X is forward along the heading, +Y is to the robot's left. "
                        "Poses and paths track the turning center.",
                        size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Row(
                        spacing=8,
                        controls=[
                            ft.Container(
                                expand=True,
                                content=_num_field(
                                    "Offset X (in)",
                                    draft.turn_offset_x,
                                    set_signed_field("turn_offset_x"),
                                    width=None,
                                ),
                            ),
                            ft.Container(
                                expand=True,
                                content=_num_field(
                                    "Offset Y (in)",
                                    draft.turn_offset_y,
                                    set_signed_field("turn_offset_y"),
                                    width=None,
                                ),
                            ),
                        ],
                    ),
                    ft.Container(height=4),
                    green_button("Apply robot changes", on_click=lambda e: self._confirm_apply_robot()),
                ],
            ),
        )

    def _field_background_selector(self) -> ft.Control:
        self._field_bg_row.controls = [
            self._make_field_bg_chip("match", "Match"),
            self._make_field_bg_chip("skills", "Skills"),
        ]
        return self._field_bg_row

    def _make_field_bg_chip(self, key: str, label: str) -> ft.Container:
        selected = key == self.field_background
        color = ft.Colors.WHITE if selected else ft.Colors.ON_SURFACE_VARIANT
        return ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=8,
            bgcolor=ACCENT_CONTAINER if selected else ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border=ft.Border.all(1, ACCENT_BORDER if selected else ft.Colors.OUTLINE_VARIANT),
            alignment=ft.Alignment(0, 0),
            ink=True,
            on_click=lambda e, k=key: self._set_field_background(k),
            content=ft.Text(
                label,
                size=13,
                font_family=TITLE_FONT,
                italic=True,
                weight=ft.FontWeight.BOLD,
                color=color,
                text_align=ft.TextAlign.CENTER,
            ),
        )

    def _set_field_background(self, key: str):
        key = save_field_background(key)
        self.field_background = key
        self._field_bg_row.controls = [
            self._make_field_bg_chip("match", "Match"),
            self._make_field_bg_chip("skills", "Skills"),
        ]
        if _is_mounted(self._field_bg_row):
            self._field_bg_row.update()
        if self.on_field_background_change is not None:
            self.on_field_background_change(key)

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
            current._refresh_timeline(emit_pose=True)
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
                    f"Max speed v_max: {draft.max_speed:g} in/s\n"
                    f"Stall accel a₀: {draft.max_accel:g} in/s²\n"
                    f"τ = v_max/a₀: {draft.max_speed / max(draft.max_accel, 1e-9):.3f} s\n"
                    f"Track width: {draft.track_width:g} in\n"
                    f"Accel curvature gain: {draft.accel_curvature_gain:g}\n"
                    f"Length X: {draft.length:g} in\n"
                    f"Width Y: {draft.width:g} in\n"
                    f"Turn offset: ({draft.turn_offset_x:g}, {draft.turn_offset_y:g}) in"
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
                    ft.Text(
                        "CSV format: timestamp,x,y,heading (ms, inches, degrees).",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    self._visualize_path_field,
                    ft.Row(
                        spacing=8,
                        controls=[
                            self._viz_load_button,
                            ft.OutlinedButton("Clear", on_click=self._viz_clear),
                        ],
                    ),
                    self._viz_status,
                    ft.Row(
                        spacing=12,
                        controls=[
                            ft.Row(
                                spacing=6,
                                controls=[
                                    ft.Container(width=10, height=10, bgcolor="#3B82F6", border_radius=2),
                                    ft.Text("Desired", size=12),
                                ],
                            ),
                            ft.Row(
                                spacing=6,
                                controls=[
                                    ft.Container(width=10, height=10, bgcolor="#F97316", border_radius=2),
                                    ft.Text("Actual + path", size=12),
                                ],
                            ),
                        ],
                    ),
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=4, vertical=8),
                        border_radius=10,
                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                        content=ft.Column(
                            spacing=4,
                            tight=True,
                            controls=[
                                ft.Row(
                                    spacing=4,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        self._viz_play_button,
                                        self._viz_time_label,
                                        self._viz_duration_label,
                                    ],
                                ),
                                self._viz_time_slider,
                            ],
                        ),
                    ),
                ],
            ),
        )

    def _on_visualize_path_change(self, e):
        self.visualize_path = e.control.value or ""

    def _viz_format_time(self, ms: float) -> str:
        return f"{ms / 1000.0:.2f} s"

    def _viz_duration_ms(self) -> float:
        actual = self._capture_timeline.duration_ms if self._capture_timeline else 0.0
        desired = self._desired_timeline.duration_ms if self._desired_timeline else 0.0
        return max(actual, desired, 0.0)

    def _viz_leave(self):
        self._viz_stop_playback()
        self._viz_emit_clear()

    def _viz_clear(self, e=None):
        self._viz_stop_playback()
        self._capture_timeline = None
        self._capture_path = []
        self._desired_timeline = None
        self._viz_loaded = False
        self._viz_play_time_ms = 0.0
        self._viz_status.value = "Load a pose_capture CSV to compare desired vs actual."
        self._viz_status.color = ft.Colors.ON_SURFACE_VARIANT
        self._viz_sync_chrome(disabled=True)
        self._viz_emit_clear()
        if _is_mounted(self._viz_status):
            self._viz_status.update()

    def _viz_emit_clear(self):
        if self.on_visualize_frame is not None:
            self.on_visualize_frame(None)

    def _viz_load_capture(self, e=None):
        self._viz_stop_playback()
        path = (self.visualize_path or "").strip()
        self.visualize_path = path
        self._visualize_path_field.value = path

        result = load_pose_capture(path)
        if not result.ok:
            self._capture_timeline = None
            self._capture_path = []
            self._desired_timeline = None
            self._viz_loaded = False
            self._viz_play_time_ms = 0.0
            detail = result.error
            if result.warnings:
                detail = f"{detail} ({result.warnings[0]})"
            self._viz_status.value = detail
            self._viz_status.color = ft.Colors.ERROR
            self._viz_sync_chrome(disabled=True)
            self._viz_emit_clear()
            if _is_mounted(self._visualize_path_field):
                self._visualize_path_field.update()
            if _is_mounted(self._viz_status):
                self._viz_status.update()
            return

        self._capture_timeline = result.timeline
        self._capture_path = list(result.path_points)
        self._viz_refresh_desired()
        self._viz_loaded = True
        self._viz_play_time_ms = 0.0

        warn = ""
        if result.warnings:
            warn = f" — {result.warnings[0]}"
        desired_ms = self._desired_timeline.duration_ms if self._desired_timeline else 0.0
        self._viz_status.value = (
            f"Loaded {result.sample_count} samples "
            f"({self._viz_format_time(self._capture_timeline.duration_ms)} actual, "
            f"{self._viz_format_time(desired_ms)} desired)"
            f"{warn}"
        )
        self._viz_status.color = (
            ft.Colors.AMBER_400 if result.warnings else ft.Colors.ON_SURFACE_VARIANT
        )
        self._viz_sync_chrome(disabled=False)
        self._viz_emit_frame()
        if _is_mounted(self._visualize_path_field):
            self._visualize_path_field.update()
        if _is_mounted(self._viz_status):
            self._viz_status.update()

    def _viz_sync_chrome(self, *, disabled: bool | None = None):
        duration = max(self._viz_duration_ms(), 1.0)
        if disabled is None:
            disabled = not self._viz_loaded or self._viz_duration_ms() <= 0
        self._viz_play_time_ms = min(self._viz_play_time_ms, self._viz_duration_ms())
        self._viz_time_slider.max = duration
        self._viz_time_slider.value = self._viz_play_time_ms
        self._viz_time_slider.disabled = disabled
        self._viz_play_button.disabled = disabled
        self._viz_time_label.value = self._viz_format_time(self._viz_play_time_ms)
        self._viz_duration_label.value = f"/ {self._viz_format_time(self._viz_duration_ms())}"
        if _is_mounted(self._viz_time_slider):
            self._viz_time_slider.update()
        if _is_mounted(self._viz_time_label):
            self._viz_time_label.update()
        if _is_mounted(self._viz_duration_label):
            self._viz_duration_label.update()
        if _is_mounted(self._viz_play_button):
            self._viz_play_button.update()

    def _viz_emit_frame(self):
        if self.on_visualize_frame is None or not self._viz_loaded:
            return
        t = self._viz_play_time_ms
        desired = self._desired_timeline.pose_at(t) if self._desired_timeline else None
        actual = self._capture_timeline.pose_at(t) if self._capture_timeline else None
        self.on_visualize_frame(
            {
                "desired": desired,
                "actual": actual,
                "path": self._capture_path,
            }
        )

    def _viz_refresh_desired(self):
        self._desired_timeline = build_routine_timeline(self.action_list.actions)

    def _viz_seek(self, t_ms: float, *, update_chrome: bool = True):
        duration = self._viz_duration_ms()
        self._viz_play_time_ms = max(0.0, min(float(t_ms), duration))
        self._viz_emit_frame()
        if update_chrome:
            self._viz_time_label.value = self._viz_format_time(self._viz_play_time_ms)
            self._viz_time_slider.value = self._viz_play_time_ms
            if _is_mounted(self._viz_time_label):
                self._viz_time_label.update()
            if _is_mounted(self._viz_time_slider):
                self._viz_time_slider.update()

    def _viz_stop_playback(self):
        self._viz_playing = False
        self._viz_playback_task = None
        self._viz_play_button.icon = ft.Icons.PLAY_ARROW
        self._viz_play_button.tooltip = "Play"
        if _is_mounted(self._viz_play_button):
            self._viz_play_button.update()

    def _viz_on_slider_drag_start(self, e):
        self._viz_slider_dragging = True
        if self._viz_playing:
            self._viz_stop_playback()

    def _viz_on_slider_drag_end(self, e):
        self._viz_slider_dragging = False
        self._viz_seek(float(e.control.value or 0), update_chrome=True)

    def _viz_on_slider_change(self, e):
        self._viz_seek(float(e.control.value or 0), update_chrome=False)
        self._viz_time_label.value = self._viz_format_time(self._viz_play_time_ms)
        if _is_mounted(self._viz_time_label):
            self._viz_time_label.update()

    def _viz_toggle_playback(self, e):
        if not self._viz_loaded or self._viz_duration_ms() <= 0:
            return
        # Refresh desired from the currently selected routine before playing.
        self._viz_refresh_desired()
        self._viz_sync_chrome(disabled=False)
        if self._viz_playing:
            self._viz_stop_playback()
            self._viz_seek(self._viz_play_time_ms, update_chrome=True)
            return
        if self._viz_play_time_ms >= self._viz_duration_ms():
            self._viz_seek(0.0, update_chrome=True)
        self._viz_playing = True
        self._viz_play_button.icon = ft.Icons.PAUSE
        self._viz_play_button.tooltip = "Pause"
        if _is_mounted(self._viz_play_button):
            self._viz_play_button.update()
        self._viz_playback_task = self.page.run_task(self._viz_playback_loop)

    async def _viz_playback_loop(self):
        import asyncio
        import time

        last = time.monotonic()
        last_chrome = last
        while self._viz_playing:
            await asyncio.sleep(1 / 60)
            now = time.monotonic()
            dt_ms = (now - last) * 1000.0
            last = now
            nxt = self._viz_play_time_ms + dt_ms
            if nxt >= self._viz_duration_ms():
                self._viz_seek(self._viz_duration_ms(), update_chrome=True)
                self._viz_stop_playback()
                break
            update_chrome = (now - last_chrome) >= (1 / 12)
            if update_chrome:
                last_chrome = now
            self._viz_seek(nxt, update_chrome=update_chrome)

    def _on_code_root_change(self, e):
        self.code_root = e.control.value or ""
        self._refresh_resolved_path_labels()

    def _on_code_root_blur(self, e):
        self.code_root = (e.control.value or "").strip()
        e.control.value = self.code_root
        save_code_root(self.code_root)
        self._refresh_resolved_path_labels()
        if _is_mounted(e.control):
            e.control.update()

    def _on_sd_card_change(self, e):
        self.sd_card_path = e.control.value or ""

    def _on_sd_card_blur(self, e):
        self.sd_card_path = (e.control.value or "").strip()
        e.control.value = self.sd_card_path
        save_sd_card_path(self.sd_card_path)
        if _is_mounted(e.control):
            e.control.update()

    def resolved_path_for(self, routine: Routine):
        """Absolute export path for a routine, using the master code root from settings."""
        return resolve_routine_path(self.code_root, routine.path)

    def _resolved_path_label(self, routine: Routine) -> str:
        resolved = self.resolved_path_for(routine)
        if resolved is not None:
            return f"Resolves to: {resolved}"
        if (routine.path or "").strip():
            return "Set a master path to resolve this relative path."
        return "No export path set."

    def _refresh_resolved_path_labels(self):
        if self.mode != MODE_ROUTINES:
            return
        self._rebuild_routines_panel()
        if _is_mounted(self._routines_list):
            self._routines_list.update()

    def _rebuild_routines_panel(self):
        self._routines_list.controls = [
            self._make_routine_card(i, routine) for i, routine in enumerate(self.routines)
        ]

    def _make_routine_card(self, index: int, routine: Routine) -> ft.Control:
        selected = index == self.selected_routine_index
        title = title_text(routine.name or routine.file_id, size=16)

        def select_routine(e, idx=index):
            self._select_routine(idx)

        def name_change(e):
            # Allow free typing; clear any prior validation error.
            if e.control.error:
                e.control.error = None
                if _is_mounted(e.control):
                    e.control.update()

        def name_blur(e, r=routine, idx=index):
            raw = (e.control.value or "").strip()
            if raw == r.name:
                e.control.error = None
                if _is_mounted(e.control):
                    e.control.update()
                return

            error = None
            if not is_valid_routine_name(raw):
                error = "Letters, numbers, _ and - only (no spaces or periods)."
            else:
                taken = {x.file_id for x in self.routines if x is not r}
                if raw in taken:
                    error = "That name is already in use."

            if error is not None:
                e.control.value = r.name
                e.control.error = error
                if _is_mounted(e.control):
                    e.control.update()
                return

            if not self._rename_routine(r, raw):
                e.control.value = r.name
                e.control.error = "Could not rename routine file."
                if _is_mounted(e.control):
                    e.control.update()
                return

            e.control.value = r.name
            e.control.error = None
            title.value = r.name
            file_label.value = f"File: routines/{r.file_id}.json"
            if _is_mounted(e.control):
                e.control.update()
            if _is_mounted(title):
                title.update()
            if _is_mounted(file_label):
                file_label.update()
            if idx == self.selected_routine_index and r.action_list is not None:
                r.action_list.set_routine_name(r.name)

        def path_change(e, r=routine):
            r.path = e.control.value or ""
            resolved_label.value = self._resolved_path_label(r)
            if _is_mounted(resolved_label):
                resolved_label.update()
            self._on_routine_dirty(r)

        def delete_routine(e, idx=index):
            self._confirm_delete_routine(idx)

        header = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                title,
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

        file_label = ft.Text(
            f"File: routines/{routine.file_id}.json",
            size=11,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        resolved_label = ft.Text(
            self._resolved_path_label(routine),
            size=11,
            color=ft.Colors.ON_SURFACE_VARIANT,
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
                    _text_field(
                        "Name",
                        routine.name,
                        name_change,
                        on_blur=name_blur,
                    ),
                    ft.Text(
                        "Letters, numbers, _ and - only (no spaces or periods).",
                        size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    _text_field("Relative path", routine.path, path_change),
                    resolved_label,
                    file_label,
                ],
            ),
        )

    def _rename_routine(self, routine: Routine, new_name: str) -> bool:
        """Set routine name == file stem; rename the JSON file when it changes."""
        if not is_valid_routine_name(new_name):
            return False
        existing = {r.file_id for r in self.routines if r is not routine}
        if new_name in existing:
            return False
        if new_name == routine.file_id:
            routine.name = new_name
            return True

        old_id = routine.file_id
        try:
            rename_routine_file(old_id, new_name)
        except OSError:
            return False

        routine.file_id = new_name
        routine.name = new_name
        self.save_routine(routine, manual=False)
        self._persist_index()
        return True

    def routines_document(self) -> dict:
        """Routines list JSON (metadata + current actions); history is separate."""
        return routines_to_json(self.routines, self.selected_routine_index)

    def histories_document(self) -> dict:
        """Per-routine undo/redo JSON, keyed by routine name/index."""
        return {
            "version": 1,
            "histories": [
                {
                    "name": routine.name,
                    "history": routine.history_doc,
                }
                for routine in self.routines
            ],
        }

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
            if previous.dirty:
                self.save_routine(previous, manual=False)
        self.selected_routine_index = index
        self._ensure_action_list(self.selected_routine)
        self._persist_index()
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
            if self.selected_routine.dirty:
                self.save_routine(self.selected_routine, manual=False)

        existing = {r.file_id for r in self.routines}
        self._routine_counter += 1
        file_id = allocate_file_id(existing)
        routine = Routine(file_id, file_id=file_id)
        self._ensure_action_list(routine)
        self.routines.append(routine)
        self.selected_routine_index = len(self.routines) - 1
        self.save_routine(routine, manual=True)
        self._persist_index()
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
            delete_routine_file(routine.file_id)
            self.routines.pop(index)
            if self.selected_routine_index >= len(self.routines):
                self.selected_routine_index = len(self.routines) - 1
            elif self.selected_routine_index > index:
                self.selected_routine_index -= 1
            self._ensure_action_list(self.selected_routine)
            self._persist_index()
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
                content=ft.Text(
                    f'Remove "{routine.name}" and delete routines/{routine.file_id}.json?'
                ),
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
