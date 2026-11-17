import sys
from pathlib import Path

import flet as ft

import keys
from actions import CanvasCurve, RobotAction
from render import FieldCanvas
from sidebar import Sidebar
from theme import apply_page_theme

_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from settings import field_image_for_background, get_field_background


def main(page: ft.Page):
    page.title = "Path Planner"
    page.padding = 0
    apply_page_theme(page)

    def refresh_scene():
        action_list = sidebar.action_list
        start = action_list.start_action()
        curves = []
        for motion_action in action_list.motion_actions():
            dimmed = action_list.motion_dimmed(motion_action)
            for i, _ in enumerate(motion_action.motion.curves):
                selected = action_list.curve_selected(motion_action, i)
                canvas_curve = CanvasCurve(motion_action, i)
                canvas_curve.dimmed = dimmed
                curves.append((canvas_curve, selected))
        field.set_scene(
            start.end_pose(),
            curves,
            start_action=start,
            start_selected=(action_list.selected is start),
            robot_specs=sidebar.robot_specs,
            robot_image=sidebar.robot_image,
            body_opacity=sidebar.body_opacity,
        )

    def on_playback_pose(pose):
        field.set_playback_pose(pose)

    def on_visualize_frame(frame):
        if frame is None:
            field.clear_visualize()
            return
        field.set_visualize(
            path=frame.get("path") or [],
            desired=frame.get("desired"),
            actual=frame.get("actual"),
            active=True,
        )

    def on_field_background_change(key: str):
        field.set_field_image(field_image_for_background(key))

    def on_canvas_action_select(target: RobotAction | CanvasCurve):
        action_list = sidebar.action_list
        action_list.focus_keys()
        if isinstance(target, CanvasCurve):
            action_list.select_curve(target.motion_action, target.curve_index)
        else:
            action_list.select(target)

    def on_canvas_point_drag(target, point_index, x, y):
        sidebar.action_list.apply_live_g1(target, point_index)

    def on_canvas_point_drag_end(target):
        sidebar.action_list.on_canvas_point_drag_end(target)

    def on_keyboard(e: ft.KeyboardEvent):
        if sidebar.handle_keyboard(e):
            return
        if not (e.ctrl or e.meta):
            return
        key = (e.key or "").upper()
        action_list = sidebar.action_list
        if key == "S":
            sidebar.save_selected_routine()
        elif key == "Z" and e.shift:
            action_list.redo()
        elif key == "Z":
            action_list.undo()
        elif key == "Y":
            action_list.redo()

    field = FieldCanvas(
        field_image_for_background(get_field_background()),
        on_click=lambda x, y: print(f"tapped field: ({x:.1f}, {y:.1f}) in"),
        on_motion_select=on_canvas_action_select,
        on_point_drag=on_canvas_point_drag,
        on_point_drag_end=on_canvas_point_drag_end,
    )

    sidebar = Sidebar(page, on_scene_change=refresh_scene)
    sidebar.on_playback_pose = on_playback_pose
    sidebar.on_visualize_frame = on_visualize_frame
    sidebar.on_field_background_change = on_field_background_change

    page.on_keyboard_event = on_keyboard
    if page.web:
        # Otherwise the browser's menu covers the action list's right-click menu.
        page.run_task(ft.BrowserContextMenu().disable)
    page.add(
        # The listener sees every key from focused descendants, so held
        # modifiers (for shift-click) are known wherever focus is. It ignores
        # `expand`, hence the Container.
        ft.Container(
            expand=True,
            content=ft.KeyboardListener(
                autofocus=True,
                on_key_down=lambda e: keys.key_down(e.key),
                on_key_up=lambda e: keys.key_up(e.key),
                content=ft.Row(
                    expand=True,
                    spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[field.control, sidebar.control],
                ),
            ),
        )
    )

    refresh_scene()


ft.run(main, assets_dir="assets")
