import flet as ft

from actions import CanvasCurve, RobotAction
from render import FieldCanvas
from sidebar import Sidebar
from theme import apply_page_theme

FIELD_IMAGE = "override_match.png"


def main(page: ft.Page):
    page.title = "Path Planner"
    page.padding = 0
    apply_page_theme(page)

    def refresh_scene():
        action_list = sidebar.action_list
        start = action_list.start_action()
        curves = []
        for motion_action in action_list.motion_actions():
            for i, _ in enumerate(motion_action.motion.curves):
                selected = action_list._selected_curve == (motion_action, i)
                curves.append((CanvasCurve(motion_action, i), selected))
        field.set_scene(
            start.end_pose(),
            curves,
            start_action=start,
            start_selected=(action_list.selected is start),
            robot_specs=sidebar.robot_specs,
        )

    def on_playback_pose(pose):
        field.set_playback_pose(pose)

    def on_canvas_action_select(target: RobotAction | CanvasCurve):
        action_list = sidebar.action_list
        if isinstance(target, CanvasCurve):
            action_list.select_curve(target.motion_action, target.curve_index)
        else:
            action_list.select(target)

    def on_canvas_point_drag(target, point_index, x, y):
        sidebar.action_list.apply_live_g1(target, point_index)

    def on_canvas_point_drag_end(target):
        sidebar.action_list.on_canvas_point_drag_end(target)

    field = FieldCanvas(
        FIELD_IMAGE,
        on_click=lambda x, y: print(f"tapped field: ({x:.1f}, {y:.1f}) in"),
        on_motion_select=on_canvas_action_select,
        on_point_drag=on_canvas_point_drag,
        on_point_drag_end=on_canvas_point_drag_end,
    )

    sidebar = Sidebar(page, on_scene_change=refresh_scene)
    sidebar.on_playback_pose = on_playback_pose

    page.add(
        ft.Row(
            expand=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[field.control, sidebar.control],
        )
    )

    refresh_scene()


ft.run(main, assets_dir="assets")
