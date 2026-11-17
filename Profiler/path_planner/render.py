from math import cos, exp, hypot, radians, sin

import flet as ft
import flet.canvas as cv

import keys
from actions import CanvasCurve
from motion import FIELD_SIZE_IN, clamp_field_coord, clamp_field_point
from robot import RobotSpecs, get_robot_specs
from robot_image import RobotImage, image_box, new_image_control, place_image

# Max distance (inches) from a tap to a bezier sample to count as a hit.
HIT_THRESHOLD_IN = 2.5

# Screen-pixel radius for grabbing a control point.
POINT_HIT_RADIUS_PX = 20.0

# Distinct colors for each bezier control point.
POINT_COLORS = (
    ft.Colors.GREEN_ACCENT_400,  # start
    ft.Colors.BLUE_400,           # control 1
    ft.Colors.PURPLE_400,         # control 2
    ft.Colors.RED_400,            # end
)
POINT_RADIUS_PX = 8.0

# Curves of motions outside the current selection.
DIMMED_CURVE_OPACITY = 0.6

MAX_ZOOM = 6.0
# Zoom factor per pixel of vertical scroll.
ZOOM_PER_SCROLL_PX = 0.0015

# Visualize overlay colors
DESIRED_ROBOT_COLOR = "#3B82F6"  # blue — planned / routine pose
ACTUAL_ROBOT_COLOR = "#F97316"  # orange — captured pose
ACTUAL_PATH_COLOR = "#FB923C"


def _is_mounted(control) -> bool:
    try:
        return control.page is not None
    except RuntimeError:
        return False


class FieldCanvas:
    """The main screen: the field image with an interactive drawing canvas on top."""

    def __init__(
        self,
        field_image: str,
        on_click=None,
        on_motion_select=None,
        on_point_drag=None,
        on_point_drag_end=None,
        padding: float = 24,
    ):
        self.on_click = on_click
        self.on_motion_select = on_motion_select
        self.on_point_drag = on_point_drag
        self.on_point_drag_end = on_point_drag_end

        self._size = 0.0
        self._start_pose: tuple[float, float, float] | None = None
        self._start_action = None
        self._start_selected = False
        self._curves: list[tuple[CanvasCurve, bool]] = []
        self._robot_specs: RobotSpecs = get_robot_specs().copy()
        self._robot_image: RobotImage | None = None
        self._body_opacity = 1.0
        # One per robot body drawn at once (Visualize shows desired + actual).
        self._robot_image_controls = [new_image_control(), new_image_control()]
        self._playback_pose: tuple[float, float, float] | None = None
        self._visualize_active = False
        self._visualize_path: list[tuple[float, float]] = []
        self._desired_pose: tuple[float, float, float] | None = None
        self._actual_pose: tuple[float, float, float] | None = None
        self._drag_state: tuple[object, int] | None = None
        # View: zoom ≥ 1 and the zoomed field's offset (px) at the view's top-left.
        self._zoom = 1.0
        self._pan = (0.0, 0.0)
        self._gesture_scale = 1.0
        self._gesture_active = False
        self._swipe_pos: tuple[float, float] | None = None

        self.canvas = cv.Canvas(expand=True, on_resize=self._handle_resize)
        # Robot and selected-curve handles, drawn above the robot picture.
        self._top_canvas = cv.Canvas(left=0, top=0, right=0, bottom=0)
        self._field_image = ft.Image(
            src=field_image, fit=ft.BoxFit.FILL, left=0, top=0, right=0, bottom=0
        )
        self._zoom_label = ft.Text("100%", size=12, weight=ft.FontWeight.W_600)
        self._zoom_badge = ft.Container(
            right=8,
            top=8,
            visible=False,
            padding=ft.Padding.only(left=10, right=2),
            border_radius=16,
            bgcolor=ft.Colors.with_opacity(0.85, ft.Colors.SURFACE_CONTAINER_HIGHEST),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            content=ft.Row(
                tight=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    self._zoom_label,
                    ft.IconButton(
                        ft.Icons.FIT_SCREEN,
                        icon_size=16,
                        tooltip="Reset zoom",
                        on_click=lambda e: self.reset_view(),
                    ),
                ],
            ),
        )

        gesture = ft.GestureDetector(
            expand=True,
            on_tap_down=self._handle_tap,
            on_pan_start=self._handle_pan_start,
            on_pan_update=self._handle_pan_update,
            on_pan_end=self._handle_pan_end,
            on_scroll=self._handle_scroll,
            content=self.canvas,
        )
        # Primary drags stay with the inner detector (control points);
        # the scale recognizer gets middle-button drags and trackpad pinches.
        view_gesture = ft.GestureDetector(
            expand=True,
            on_scale_start=self._handle_scale_start,
            on_scale_update=self._handle_scale_update,
            on_scale_end=self._handle_scale_end,
            content=gesture,
        )

        self.control = ft.Container(
            aspect_ratio=1,
            padding=padding,
            content=ft.Stack(
                expand=True,
                # Zoomed field and shapes extend past the view.
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                controls=[
                    self._field_image,
                    view_gesture,
                    # Above the paths but lets taps/drags through to the gesture layer.
                    ft.TransparentPointer(
                        left=0,
                        top=0,
                        right=0,
                        bottom=0,
                        content=ft.Stack(
                            controls=[*self._robot_image_controls, self._top_canvas],
                        ),
                    ),
                    self._zoom_badge,
                ],
            ),
        )

    def set_field_image(self, field_image: str):
        """Swap the field background asset (e.g. match vs skills)."""
        if self._field_image.src == field_image:
            return
        self._field_image.src = field_image
        if _is_mounted(self._field_image):
            self._field_image.update()

    # --- coordinate conversion ------------------------------------------

    def _scale(self) -> float:
        """Screen pixels per field inch at the current zoom."""
        return self._size / FIELD_SIZE_IN * self._zoom

    def field_to_screen(self, x: float, y: float) -> tuple[float, float]:
        scale = self._scale()
        return (x * scale - self._pan[0], (FIELD_SIZE_IN - y) * scale - self._pan[1])

    def screen_to_field(self, px: float, py: float) -> tuple[float, float]:
        if self._size == 0:
            return (0.0, 0.0)
        scale = self._scale()
        return ((px + self._pan[0]) / scale, FIELD_SIZE_IN - (py + self._pan[1]) / scale)

    # --- zoom / pan -------------------------------------------------------

    def reset_view(self):
        self._zoom = 1.0
        self._pan = (0.0, 0.0)
        self._apply_view()

    def _set_pan(self, x: float, y: float):
        # Keep the field covering the whole view.
        limit = max(0.0, self._size * self._zoom - self._size)
        self._pan = (min(max(x, 0.0), limit), min(max(y, 0.0), limit))

    def _pan_by(self, dx: float, dy: float):
        self._set_pan(self._pan[0] + dx, self._pan[1] + dy)
        self._apply_view()

    def _zoom_at(self, factor: float, sx: float, sy: float):
        """Zoom by ``factor`` keeping the field point under (sx, sy) in place."""
        fx, fy = self.screen_to_field(sx, sy)
        self._zoom = min(MAX_ZOOM, max(1.0, self._zoom * factor))
        scale = self._scale()
        self._set_pan(fx * scale - sx, (FIELD_SIZE_IN - fy) * scale - sy)
        self._apply_view()

    def _apply_view(self):
        img = self._field_image
        if self._size > 0:
            img.left, img.top = -self._pan[0], -self._pan[1]
            img.width = img.height = self._size * self._zoom
            img.right = img.bottom = None
        zoomed = self._zoom > 1.001
        self._zoom_label.value = f"{round(self._zoom * 100)}%"
        self._zoom_badge.visible = zoomed
        for c in (img, self._zoom_badge):
            if _is_mounted(c):
                c.update()
        self._render()

    def _clamp_field(self, x: float, y: float) -> tuple[float, float]:
        return (max(0.0, min(FIELD_SIZE_IN, x)), max(0.0, min(FIELD_SIZE_IN, y)))

    # --- scene API ------------------------------------------------------

    def set_scene(
        self,
        start_pose,
        curves: list[tuple[CanvasCurve, bool]],
        start_action=None,
        start_selected: bool = False,
        robot_specs: RobotSpecs | None = None,
        robot_image: RobotImage | None = None,
        body_opacity: float = 1.0,
    ):
        self._start_pose = start_pose
        self._robot_image = robot_image
        self._body_opacity = body_opacity
        self._start_action = start_action
        self._start_selected = start_selected
        self._curves = list(curves)
        if robot_specs is not None:
            self._robot_specs = robot_specs.copy()
        self._render()

    def set_playback_pose(self, pose: tuple[float, float, float] | None):
        """Override the robot body pose during simulate scrubbing/playback."""
        if self._visualize_active:
            return
        self._playback_pose = pose
        self._render()

    def set_visualize(
        self,
        *,
        path: list[tuple[float, float]] | None = None,
        desired: tuple[float, float, float] | None = None,
        actual: tuple[float, float, float] | None = None,
        active: bool = True,
    ):
        """Show desired (blue) + actual (orange) robots and the captured path."""
        self._visualize_active = bool(active)
        if not active:
            self._visualize_path = []
            self._desired_pose = None
            self._actual_pose = None
        else:
            self._visualize_path = list(path or [])
            self._desired_pose = desired
            self._actual_pose = actual
            self._playback_pose = None
        self._render()

    def clear_visualize(self):
        self.set_visualize(active=False)

    def find_curve_at(self, field_x: float, field_y: float):
        best = None
        # Same on-screen reach at any zoom.
        best_d2 = (HIT_THRESHOLD_IN / self._zoom) ** 2
        for canvas_curve, _ in self._curves:
            for px, py in canvas_curve.curve.sample(60):
                d2 = (px - field_x) ** 2 + (py - field_y) ** 2
                if d2 < best_d2:
                    best_d2 = d2
                    best = canvas_curve
        return best

    def find_start_at(self, screen_x: float, screen_y: float) -> bool:
        if self._start_pose is None:
            return False
        x, y, _ = self._start_pose
        px, py = self.field_to_screen(x, y)
        radius = POINT_HIT_RADIUS_PX if self._start_selected else 16.0
        return hypot(screen_x - px, screen_y - py) <= radius

    def find_point_at(self, screen_x: float, screen_y: float):
        """Return (target, point_index) for a draggable handle, if any."""
        if self._start_selected and self._start_action is not None and self._start_pose is not None:
            x, y, _ = self._start_pose
            px, py = self.field_to_screen(x, y)
            if hypot(screen_x - px, screen_y - py) <= POINT_HIT_RADIUS_PX:
                return self._start_action, 0

        for canvas_curve, selected in self._curves:
            if not selected:
                continue
            for i, point in enumerate(canvas_curve.curve.points):
                px, py = self.field_to_screen(*point)
                if hypot(screen_x - px, screen_y - py) <= POINT_HIT_RADIUS_PX:
                    return canvas_curve, i
        return None

    def clear(self):
        self._start_pose = None
        self._start_action = None
        self._start_selected = False
        self._curves = []
        self._drag_state = None
        self._render()

    # --- drawing helpers ------------------------------------------------

    def _dashed_line_shapes(self, p0, p1, paint):
        x0, y0 = p0
        x1, y1 = p1
        length = hypot(x1 - x0, y1 - y0)
        if length < 1e-6:
            return []

        dash, gap = 5.0, 5.0
        shapes = []
        dist = 0.0
        while dist < length:
            t0 = dist / length
            t1 = min((dist + dash) / length, 1.0)
            sx0 = x0 + (x1 - x0) * t0
            sy0 = y0 + (y1 - y0) * t0
            sx1 = x0 + (x1 - x0) * t1
            sy1 = y0 + (y1 - y0) * t1
            shapes.append(
                cv.Path(
                    [cv.Path.MoveTo(sx0, sy0), cv.Path.LineTo(sx1, sy1)],
                    paint=paint,
                )
            )
            dist += dash + gap
        return shapes

    def _render(self):
        shapes = []
        # Robot bodies and handles go on the top canvas, above the robot picture.
        top = []
        handles = []
        robot_poses = []

        for canvas_curve, selected in self._curves:
            shapes.extend(self._curve_shapes(canvas_curve, selected))
            if selected:
                handles.extend(self._curve_handle_shapes(canvas_curve))

        if self._visualize_active:
            shapes.extend(self._visualize_path_shapes())
            if self._desired_pose is not None:
                robot_poses.append(self._desired_pose)
                top.extend(
                    self._robot_body_shapes(self._desired_pose, color=DESIRED_ROBOT_COLOR)
                )
                top.extend(
                    self._tracking_point_shapes(
                        self._desired_pose,
                        selected=True,
                        color=DESIRED_ROBOT_COLOR,
                    )
                )
            if self._actual_pose is not None:
                robot_poses.append(self._actual_pose)
                top.extend(
                    self._robot_body_shapes(self._actual_pose, color=ACTUAL_ROBOT_COLOR)
                )
                top.extend(
                    self._tracking_point_shapes(
                        self._actual_pose,
                        selected=True,
                        color=ACTUAL_ROBOT_COLOR,
                    )
                )
        else:
            robot_pose = self._playback_pose if self._playback_pose is not None else self._start_pose
            if robot_pose is not None:
                robot_poses.append(robot_pose)
                top.extend(self._robot_body_shapes(robot_pose))
                top.extend(
                    self._tracking_point_shapes(
                        robot_pose,
                        selected=self._playback_pose is not None or self._start_selected,
                    )
                )

        # Handles stay above the robot so they can still be seen and grabbed.
        top.extend(handles)
        self.canvas.shapes = shapes
        self._top_canvas.shapes = top
        for canvas in (self.canvas, self._top_canvas):
            if _is_mounted(canvas):
                canvas.update()
        self._place_robot_images(robot_poses)

    def _place_robot_images(self, poses):
        scale = self._scale()
        for i, control in enumerate(self._robot_image_controls):
            pose = poses[i] if i < len(poses) and self._size > 0 else None
            box = None
            if pose is not None and self._robot_image is not None:
                box = image_box(self._robot_image, self._robot_specs, pose, self.field_to_screen, scale)
            place_image(control, self._robot_image, box)
            if _is_mounted(control):
                control.update()

    def _visualize_path_shapes(self):
        if len(self._visualize_path) < 2 or self._size <= 0:
            return []
        elements = []
        first = True
        for x, y in self._visualize_path:
            sx, sy = self.field_to_screen(x, y)
            if first:
                elements.append(cv.Path.MoveTo(sx, sy))
                first = False
            else:
                elements.append(cv.Path.LineTo(sx, sy))
        return [
            cv.Path(
                elements,
                paint=ft.Paint(
                    stroke_width=3,
                    style=ft.PaintingStyle.STROKE,
                    color=ft.Colors.with_opacity(0.85, ACTUAL_PATH_COLOR),
                    stroke_join=ft.StrokeJoin.ROUND,
                    stroke_cap=ft.StrokeCap.ROUND,
                ),
            )
        ]

    def _robot_body_shapes(self, pose: tuple[float, float, float], *, color: str | None = None):
        """Translucent body rectangle from length/width.

        The pose is the turning center, so the body is shifted back by the
        configured turn offset.
        """
        x, y, heading = pose
        length = max(0.0, self._robot_specs.length)
        width = max(0.0, self._robot_specs.width)
        if length <= 0 or width <= 0 or self._size <= 0:
            return []

        body = color or ft.Colors.GREEN_ACCENT_400
        h = radians(heading)
        fx, fy = cos(h), sin(h)
        rx, ry = -sin(h), cos(h)
        ox = self._robot_specs.turn_offset_x
        oy = self._robot_specs.turn_offset_y
        x -= fx * ox + rx * oy
        y -= fy * ox + ry * oy
        hl, hw = length / 2.0, width / 2.0
        corners = [
            self.field_to_screen(x + fx * hl + rx * hw, y + fy * hl + ry * hw),
            self.field_to_screen(x + fx * hl - rx * hw, y + fy * hl - ry * hw),
            self.field_to_screen(x - fx * hl - rx * hw, y - fy * hl - ry * hw),
            self.field_to_screen(x - fx * hl + rx * hw, y - fy * hl + ry * hw),
        ]
        elements = [
            cv.Path.MoveTo(*corners[0]),
            cv.Path.LineTo(*corners[1]),
            cv.Path.LineTo(*corners[2]),
            cv.Path.LineTo(*corners[3]),
            cv.Path.Close(),
        ]
        # Faint fill when a robot picture sits underneath, so it stays visible.
        fill_alpha = 0.08 if self._robot_image is not None else 0.22
        fill = ft.Colors.with_opacity(fill_alpha * self._body_opacity, body)
        stroke = ft.Colors.with_opacity(0.85 * self._body_opacity, body)
        return [
            cv.Path(
                elements,
                paint=ft.Paint(style=ft.PaintingStyle.FILL, color=fill),
            ),
            cv.Path(
                elements,
                paint=ft.Paint(
                    stroke_width=2,
                    style=ft.PaintingStyle.STROKE,
                    color=stroke,
                ),
            ),
        ]

    def _tracking_point_shapes(
        self,
        pose: tuple[float, float, float],
        *,
        selected: bool,
        color: str | None = None,
    ):
        x, y, heading = pose
        cx, cy = self.field_to_screen(x, y)
        point_color = color or ft.Colors.GREEN_ACCENT_400
        radius = POINT_RADIUS_PX if selected else 5.0

        shapes = [
            cv.Circle(
                cx, cy, radius,
                paint=ft.Paint(style=ft.PaintingStyle.FILL, color=point_color),
            ),
        ]
        if selected:
            shapes.append(
                cv.Circle(
                    cx, cy, radius,
                    paint=ft.Paint(
                        stroke_width=2,
                        style=ft.PaintingStyle.STROKE,
                        color=ft.Colors.WHITE,
                    ),
                )
            )

        h = radians(heading)
        tip_x, tip_y = self.field_to_screen(x + cos(h) * 10, y + sin(h) * 10)
        shapes.append(
            cv.Path(
                [cv.Path.MoveTo(cx, cy), cv.Path.LineTo(tip_x, tip_y)],
                paint=ft.Paint(
                    stroke_width=3,
                    style=ft.PaintingStyle.STROKE,
                    color=point_color,
                ),
            )
        )
        return shapes

    def _start_pose_shapes(self):
        # Lightweight start marker for editing hit-testing; robot body is drawn separately.
        x, y, _ = self._start_pose
        cx, cy = self.field_to_screen(x, y)
        color = ft.Colors.GREEN_ACCENT_400
        radius = POINT_RADIUS_PX if self._start_selected else 5.0
        shapes = [
            cv.Circle(
                cx, cy, radius,
                paint=ft.Paint(style=ft.PaintingStyle.FILL, color=color),
            ),
        ]
        if self._start_selected:
            shapes.append(
                cv.Circle(
                    cx, cy, radius,
                    paint=ft.Paint(
                        stroke_width=2,
                        style=ft.PaintingStyle.STROKE,
                        color=ft.Colors.WHITE,
                    ),
                )
            )
        return shapes

    def _curve_shapes(self, canvas_curve: CanvasCurve, selected: bool):
        points = canvas_curve.curve.points
        p0, p1, p2, p3 = (self.field_to_screen(*p) for p in points)
        color = canvas_curve.canvas_color(selected)
        if canvas_curve.dimmed:
            color = ft.Colors.with_opacity(DIMMED_CURVE_OPACITY, color)
        width = 5.0 if selected else 3.0

        return [
            cv.Path(
                [
                    cv.Path.MoveTo(*p0),
                    cv.Path.CubicTo(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]),
                ],
                paint=ft.Paint(
                    stroke_width=width,
                    style=ft.PaintingStyle.STROKE,
                    color=color,
                ),
            )
        ]

    def _curve_handle_shapes(self, canvas_curve: CanvasCurve):
        """Tangent lines and the four control points of a selected curve."""
        p0, p1, p2, p3 = (self.field_to_screen(*p) for p in canvas_curve.curve.points)
        handle_paint = ft.Paint(
            stroke_width=1.5,
            style=ft.PaintingStyle.STROKE,
            color=ft.Colors.GREY_500,
        )
        shapes = self._dashed_line_shapes(p0, p1, handle_paint)
        shapes.extend(self._dashed_line_shapes(p3, p2, handle_paint))

        for i, (px, py) in enumerate((p0, p1, p2, p3)):
            shapes.append(
                cv.Circle(
                    px, py, POINT_RADIUS_PX,
                    paint=ft.Paint(style=ft.PaintingStyle.FILL, color=POINT_COLORS[i]),
                )
            )
            shapes.append(
                cv.Circle(
                    px, py, POINT_RADIUS_PX,
                    paint=ft.Paint(
                        stroke_width=2,
                        style=ft.PaintingStyle.STROKE,
                        color=ft.Colors.WHITE,
                    ),
                )
            )
        return shapes

    # --- events ---------------------------------------------------------

    def _handle_resize(self, e: cv.CanvasResizeEvent):
        if self._size > 0:
            # Keep the same part of the field in view.
            ratio = e.width / self._size
            self._pan = (self._pan[0] * ratio, self._pan[1] * ratio)
        self._size = e.width
        self._set_pan(*self._pan)
        self._apply_view()

    def _handle_scale_start(self, e: ft.ScaleStartEvent):
        print(f"[trackpad-debug] scale_start pointers={e.pointer_count}", flush=True)  # TEMP
        self._gesture_scale = 1.0
        self._gesture_active = True

    def _handle_scale_end(self, e):
        print("[trackpad-debug] scale_end", flush=True)  # TEMP
        self._gesture_active = False

    def _handle_scale_update(self, e: ft.ScaleUpdateEvent):
        """Middle-button drag pans; a trackpad pinch also zooms at its centre."""
        print(  # TEMP
            f"[trackpad-debug] scale_update delta={e.focal_point_delta} scale={e.scale} "
            f"pointers={e.pointer_count} zoom={self._zoom:.2f} pan={self._pan}",
            flush=True,
        )
        if self._size <= 0:
            return
        d = e.focal_point_delta
        if d is not None:
            self._set_pan(self._pan[0] - d.x, self._pan[1] - d.y)
        scale = e.scale or 1.0
        focal = e.local_focal_point
        if focal is not None and abs(scale - self._gesture_scale) > 1e-4:
            factor = scale / self._gesture_scale
            self._gesture_scale = scale
            self._zoom_at(factor, focal.x, focal.y)
        else:
            self._apply_view()

    def _handle_scroll(self, e: ft.ScrollEvent):
        """Scroll zooms at the cursor; shift scrolls sideways, ⌘/⌥ scrolls up/down."""
        d = e.scroll_delta
        print(f"[trackpad-debug] scroll delta={d} gesture_active={self._gesture_active}", flush=True)  # TEMP
        if d is None or self._size <= 0:
            return
        # A trackpad swipe can also arrive as a scale gesture, which already pans.
        if self._gesture_active:
            return
        if keys.shift_held():
            # macOS may already turn shift+wheel into a horizontal delta.
            self._pan_by(d.x + d.y, 0.0)
        elif keys.command_held() or keys.alt_held():
            self._pan_by(d.x, d.y)
        elif d.y and e.local_position is not None:
            if d.x:
                self._set_pan(self._pan[0] + d.x, self._pan[1])
            self._zoom_at(exp(-d.y * ZOOM_PER_SCROLL_PX), e.local_position.x, e.local_position.y)
        elif d.x:
            self._pan_by(d.x, 0.0)

    def _handle_tap(self, e: ft.TapEvent):
        if e.local_position is None or self._drag_state is not None:
            return
        # Shift keeps the current selection, e.g. while dragging a multi-selection's points.
        if keys.shift_held():
            return
        field_x, field_y = self.screen_to_field(e.local_position.x, e.local_position.y)

        hit_point = self.find_point_at(e.local_position.x, e.local_position.y)
        if hit_point is not None:
            target, _ = hit_point
            if self.on_motion_select is not None:
                self.on_motion_select(target)
            return

        if self.find_start_at(e.local_position.x, e.local_position.y):
            if self._start_action is not None and self.on_motion_select is not None:
                self.on_motion_select(self._start_action)
            return

        hit = self.find_curve_at(field_x, field_y)
        if hit is not None and self.on_motion_select is not None:
            self.on_motion_select(hit)
        elif self.on_click is not None:
            self.on_click(field_x, field_y)

    def _handle_pan_start(self, e: ft.DragStartEvent):
        print(f"[trackpad-debug] pan_start kind={e.kind}", flush=True)  # TEMP
        if e.local_position is None:
            return
        # A two-finger trackpad swipe arrives as a drag; it pans rather than moving points.
        if e.kind == ft.PointerDeviceType.TRACKPAD:
            self._swipe_pos = (e.local_position.x, e.local_position.y)
            return
        hit = self.find_point_at(e.local_position.x, e.local_position.y)
        if hit is not None:
            self._drag_state = hit

    def _handle_pan_update(self, e: ft.DragUpdateEvent):
        if self._swipe_pos is not None:
            if e.local_position is not None:
                x, y = e.local_position.x, e.local_position.y
                self._pan_by(self._swipe_pos[0] - x, self._swipe_pos[1] - y)
                self._swipe_pos = (x, y)
            return
        if self._drag_state is None or e.local_position is None:
            return
        target, point_index = self._drag_state
        field_x, field_y = self._clamp_field(
            *self.screen_to_field(e.local_position.x, e.local_position.y)
        )
        if isinstance(target, CanvasCurve):
            target.curve.points[point_index] = (field_x, field_y)
        elif hasattr(target, "x"):
            target.x = field_x
            target.y = field_y
            self._start_pose = target.end_pose()
        # Let the action list apply G1 with full previous-pose context, then redraw.
        if self.on_point_drag is not None:
            self.on_point_drag(target, point_index, field_x, field_y)
        self._render()

    def _handle_pan_end(self, e: ft.DragEndEvent):
        self._swipe_pos = None
        if self._drag_state is None:
            return
        target, _ = self._drag_state
        self._drag_state = None
        if self.on_point_drag_end is not None:
            self.on_point_drag_end(target)
