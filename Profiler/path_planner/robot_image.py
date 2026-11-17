"""Optional top-down robot picture, drawn over the body rectangle.

The body rectangle (length × width) stays what profiling and collision
thinking use; the picture shows overhangs and gaps around it. Placement is in
the robot frame: +X forward, +Y left, inches from the body's center.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field, replace
from math import cos, radians, sin
from pathlib import Path

import flet as ft
import flet.canvas as cv

_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from settings import ROBOT_IMAGE_DIR, get_robot_image, save_robot_image

from robot import RobotSpecs

IMAGE_FILE = "robot_image.png"
# Longest side kept after import; plenty for a robot a few hundred px wide.
MAX_IMAGE_PX = 1024
BODY_COLOR = ft.Colors.GREEN_ACCENT_400


@dataclass
class RobotImage:
    file: str
    aspect: float  # pixel height / width
    width_in: float  # size of the picture's horizontal edge on the field
    offset_x: float = 0.0
    offset_y: float = 0.0
    # Clockwise degrees, as seen with the robot facing right (heading 0).
    rotation: float = 0.0
    opacity: float = 0.85
    data: bytes = field(default=b"", repr=False, compare=False)

    @property
    def height_in(self) -> float:
        return self.width_in * self.aspect

    def copy(self) -> "RobotImage":
        return replace(self)

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "aspect": self.aspect,
            "width_in": self.width_in,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "rotation": self.rotation,
            "opacity": self.opacity,
        }


def load_robot_image() -> RobotImage | None:
    data = get_robot_image()
    if data is None:
        return None
    path = ROBOT_IMAGE_DIR / data["file"]
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return RobotImage(**data, data=raw)


def persist_robot_image(image: RobotImage | None) -> None:
    save_robot_image(image.to_dict() if image is not None else None)


def fit_to_body(image: RobotImage, specs: RobotSpecs) -> RobotImage:
    """Centre the picture on the body and size its forward extent to the body length."""
    quarter_turned = round(image.rotation / 90.0) % 2 == 1
    width_in = specs.length / image.aspect if quarter_turned else specs.length
    return replace(image, width_in=max(0.5, width_in), offset_x=0.0, offset_y=0.0)


def import_image(source: str | bytes, specs: RobotSpecs) -> RobotImage:
    """Copy a picture (path or bytes) into the settings folder as PNG and fit it to the body."""
    from PIL import Image

    with Image.open(source if isinstance(source, str) else io.BytesIO(source)) as img:
        img = img.convert("RGBA")
        img.thumbnail((MAX_IMAGE_PX, MAX_IMAGE_PX))
        out = io.BytesIO()
        img.save(out, format="PNG")
        width, height = img.size
    data = out.getvalue()
    (ROBOT_IMAGE_DIR / IMAGE_FILE).write_bytes(data)
    image = RobotImage(file=IMAGE_FILE, aspect=height / max(width, 1), width_in=specs.length, data=data)
    return fit_to_body(image, specs)


def image_box(image: RobotImage, specs: RobotSpecs, pose, to_screen, px_per_in: float):
    """Screen (left, top, width, height, clockwise radians) for the picture at ``pose``.

    ``pose`` is the turning center, like the body rectangle's.
    """
    x, y, heading = pose
    h = radians(heading)
    fx, fy = cos(h), sin(h)
    rx, ry = -sin(h), cos(h)
    bx = x - (fx * specs.turn_offset_x + rx * specs.turn_offset_y)
    by = y - (fy * specs.turn_offset_x + ry * specs.turn_offset_y)
    cx = bx + fx * image.offset_x + rx * image.offset_y
    cy = by + fy * image.offset_x + ry * image.offset_y
    sx, sy = to_screen(cx, cy)
    w = image.width_in * px_per_in
    hgt = image.height_in * px_per_in
    # Field angles are counter-clockwise with +Y up; Flet rotates clockwise on screen.
    return sx - w / 2, sy - hgt / 2, w, hgt, radians(image.rotation - heading)


def place_image(control: ft.Image, image: RobotImage | None, box) -> None:
    """Show ``image`` in ``control`` at ``box`` (from ``image_box``), or hide it."""
    if image is None or box is None:
        control.visible = False
        return
    left, top, w, hgt, angle = box
    if control.src is not image.data:
        control.src = image.data
    control.visible = True
    control.left, control.top, control.width, control.height = left, top, w, hgt
    control.rotate = ft.Rotate(angle, alignment=ft.Alignment(0, 0))
    control.opacity = image.opacity


def new_image_control() -> ft.Image:
    return ft.Image(src=b"", fit=ft.BoxFit.FILL, gapless_playback=True, visible=False)


PREVIEW_PX = 280  # until the preview reports its laid-out width
HANDLE_PX = 9  # corner handle square size
HANDLE_HIT_PX = 14  # grab radius around a corner
FRAME_COLOR = ft.Colors.LIGHT_BLUE_ACCENT_200
MIN_WIDTH_IN = 2.0
# (sign along picture width, sign along picture height), clockwise from top-left.
_CORNERS = ((-1, -1), (1, -1), (1, 1), (-1, 1))


class RobotImageEditor:
    """Settings section: upload a picture and line it up with the body rectangle.

    Drag inside the picture to move it; drag a corner handle to resize it
    (aspect kept, opposite corner pinned).
    """

    def __init__(self, image: RobotImage | None, get_specs, on_change, get_body_opacity=lambda: 1.0):
        self.image = image
        self._get_specs = get_specs
        self._get_body_opacity = get_body_opacity
        # on_change(image | None, commit: bool); commit means persist.
        self._on_change = on_change
        self._status = ft.Text("", size=11, color=ft.Colors.ON_SURFACE_VARIANT)
        # Preview scale is held fixed while dragging so the picture doesn't jump.
        self._frozen_k: float | None = None
        self._drag: dict | None = None

        self._size = float(PREVIEW_PX)
        self._preview_image = new_image_control()
        self._canvas = cv.Canvas(left=0, top=0, right=0, bottom=0, on_resize=self._handle_resize)
        self._gesture = ft.GestureDetector(
            left=0,
            top=0,
            right=0,
            bottom=0,
            mouse_cursor=ft.MouseCursor.MOVE,
            on_pan_start=self._handle_pan_start,
            on_pan_update=self._handle_pan_update,
            on_pan_end=self._handle_pan_end,
            on_hover=self._handle_hover,
            content=ft.Container(bgcolor=ft.Colors.TRANSPARENT),
        )
        # Square that fills the sidebar's width, however narrow it is.
        self._preview = ft.Container(
            aspect_ratio=1,
            border_radius=8,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=ft.Stack(controls=[self._preview_image, self._canvas, self._gesture]),
        )

        self._fields: dict[str, ft.TextField] = {}
        self._opacity = ft.Slider(
            min=0.1, max=1.0, value=0.85, expand=True,
            on_change=self._handle_opacity,
            on_change_end=lambda e: self._emit(commit=True),
        )
        self._upload_label = ft.Text("Upload image")
        self._upload_button = ft.OutlinedButton(
            content=self._upload_label, icon=ft.Icons.UPLOAD, on_click=self._handle_upload
        )
        self._tools = ft.Row(
            spacing=4,
            controls=[
                ft.IconButton(
                    ft.Icons.ROTATE_90_DEGREES_CW, tooltip="Rotate 90°", on_click=self._rotate
                ),
                ft.IconButton(
                    ft.Icons.FIT_SCREEN, tooltip="Fit to body length", on_click=self._fit
                ),
                ft.IconButton(
                    ft.Icons.DELETE_OUTLINE,
                    icon_color=ft.Colors.RED_300,
                    tooltip="Remove image",
                    on_click=self._remove,
                ),
            ],
        )
        self._details = ft.Column(spacing=10, tight=True)
        self.control = ft.Column(
            spacing=10,
            tight=True,
            controls=[
                ft.Text(
                    "Line a top-down picture up with the green body rectangle so "
                    "overhangs show on the field. Drag the picture to move it and "
                    "its corners to resize it. The robot faces right.",
                    size=11,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Row(spacing=8, wrap=True, controls=[self._upload_button, self._tools]),
                self._status,
                self._details,
            ],
        )
        self._build_details()

    # --- layout ---------------------------------------------------------

    def _build_details(self):
        from actions import _num_field

        self._upload_label.value = "Replace image" if self.image else "Upload image"
        self._tools.visible = self.image is not None
        if self.image is None:
            self._details.controls = []
            return

        def setter(attr: str):
            def apply(v: float):
                setattr(self.image, attr, v)
                self._emit(commit=False)

            def commit(v: float):
                apply(v)
                self._emit(commit=True)

            return apply, commit

        def num(label: str, attr: str) -> ft.Control:
            apply, commit = setter(attr)
            f = _num_field(label, round(getattr(self.image, attr), 2), apply, width=None, on_commit=commit)
            self._fields[attr] = f
            return ft.Container(expand=True, content=f)

        self._opacity.value = self.image.opacity
        self._details.controls = [
            self._preview,
            ft.Row(spacing=8, controls=[num("Offset X (in)", "offset_x"), num("Offset Y (in)", "offset_y")]),
            ft.Row(spacing=8, controls=[num("Rotation (°)", "rotation"), ft.Container(expand=True)]),
            ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[ft.Text("Image opacity", size=12), self._opacity],
            ),
        ]
        self.redraw()

    # --- preview geometry -------------------------------------------------

    def _px_per_in(self, specs: RobotSpecs) -> float:
        if self._frozen_k is not None:
            return self._frozen_k
        # Room for a 40% overhang around the body, widened if the picture reaches further.
        span = max(specs.length, specs.width, 1.0) * 1.8
        if self.image is not None:
            half_diag = 0.5 * (self.image.width_in ** 2 + self.image.height_in ** 2) ** 0.5
            reach = max(abs(self.image.offset_x), abs(self.image.offset_y)) + half_diag
            span = max(span, reach * 2 * 1.15)
        return self._size / span

    def _to_screen(self, k: float):
        mid = self._size / 2
        return lambda x, y: (mid + x * k, mid - y * k)

    def _frame(self, k: float):
        """Picture centre, width/height axes (unit, screen px) and half sizes in preview px."""
        cx, cy = self._to_screen(k)(self.image.offset_x, self.image.offset_y)
        a = radians(self.image.rotation)
        u = (cos(a), sin(a))
        v = (-sin(a), cos(a))
        return (cx, cy), u, v, self.image.width_in * k / 2, self.image.height_in * k / 2

    def _corner_points(self, k: float):
        (cx, cy), u, v, hw, hh = self._frame(k)
        return [
            (cx + su * hw * u[0] + sv * hh * v[0], cy + su * hw * u[1] + sv * hh * v[1])
            for su, sv in _CORNERS
        ]

    def _corner_at(self, x: float, y: float, k: float) -> int | None:
        # Shrink the grab zone on small pictures so the middle still moves them.
        short_side = min(self.image.width_in, self.image.height_in) * k
        best, best_d = None, min(HANDLE_HIT_PX, max(4.0, short_side / 3))
        for i, (px, py) in enumerate(self._corner_points(k)):
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = i, d
        return best

    def redraw(self):
        if self.image is None:
            return
        specs = self._get_specs()
        k = self._px_per_in(specs)
        to_screen = self._to_screen(k)

        # Turning center placed so the body's center sits mid-preview.
        pose = (specs.turn_offset_x, specs.turn_offset_y, 0.0)
        place_image(self._preview_image, self.image, image_box(self.image, specs, pose, to_screen, k))

        body_color = ft.Colors.with_opacity(max(0.15, self._get_body_opacity()), BODY_COLOR)
        hl, hw = specs.length / 2, specs.width / 2
        corners = [to_screen(hl, hw), to_screen(hl, -hw), to_screen(-hl, -hw), to_screen(-hl, hw)]
        tx, ty = to_screen(*pose[:2])
        tip = to_screen(pose[0] + min(hl, 6.0), pose[1])
        line = ft.Paint(style=ft.PaintingStyle.STROKE, stroke_width=2, color=body_color)
        shapes = [
            cv.Path(_closed(corners), paint=line),
            cv.Path([cv.Path.MoveTo(tx, ty), cv.Path.LineTo(*tip)], paint=line),
            cv.Circle(tx, ty, 4, paint=ft.Paint(style=ft.PaintingStyle.FILL, color=body_color)),
        ]

        frame = self._corner_points(k)
        shapes.append(
            cv.Path(
                _closed(frame),
                paint=ft.Paint(style=ft.PaintingStyle.STROKE, stroke_width=1.5, color=FRAME_COLOR),
            )
        )
        half = HANDLE_PX / 2
        for px, py in frame:
            shapes.append(
                cv.Rect(px - half, py - half, HANDLE_PX, HANDLE_PX,
                        paint=ft.Paint(style=ft.PaintingStyle.FILL, color=ft.Colors.WHITE))
            )
            shapes.append(
                cv.Rect(px - half, py - half, HANDLE_PX, HANDLE_PX,
                        paint=ft.Paint(style=ft.PaintingStyle.STROKE, stroke_width=1.5, color=FRAME_COLOR))
            )
        self._canvas.shapes = shapes
        for c in (self._preview_image, self._canvas):
            if _mounted(c):
                c.update()

    def _sync_fields(self):
        """Push values changed by dragging / buttons into the number fields."""
        from actions import _format_num

        for attr, f in self._fields.items():
            f.value = _format_num(round(getattr(self.image, attr), 2))
            if _mounted(f):
                f.update()

    def _refresh(self, *, rebuild: bool = False):
        if rebuild:
            self._fields = {}
            self._build_details()
            if _mounted(self.control):
                self.control.update()
        else:
            self.redraw()

    def _emit(self, *, commit: bool):
        self.redraw()
        if self._on_change is not None:
            self._on_change(self.image, commit)

    # --- events ---------------------------------------------------------

    def _handle_resize(self, e: cv.CanvasResizeEvent):
        if e.width <= 0 or abs(e.width - self._size) < 0.5:
            return
        self._size = float(e.width)
        self.redraw()

    def _handle_hover(self, e):
        if self.image is None or e.local_position is None or self._drag is not None:
            return
        k = self._px_per_in(self._get_specs())
        corner = self._corner_at(e.local_position.x, e.local_position.y, k)
        cursor = ft.MouseCursor.MOVE if corner is None else _resize_cursor(corner, self.image.rotation)
        if self._gesture.mouse_cursor != cursor:
            self._gesture.mouse_cursor = cursor
            if _mounted(self._gesture):
                self._gesture.update()

    def _handle_pan_start(self, e: ft.DragStartEvent):
        if self.image is None or e.local_position is None:
            return
        k = self._px_per_in(self._get_specs())
        self._frozen_k = k
        corner = self._corner_at(e.local_position.x, e.local_position.y, k)
        if corner is None:
            self._drag = {"mode": "move"}
            return
        # Pin the opposite corner; resize along the picture's diagonal from there.
        _, u, v, hw, hh = self._frame(k)
        su, sv = _CORNERS[corner]
        anchor = self._corner_points(k)[(corner + 2) % 4]
        diag = (su * 2 * hw * u[0] + sv * 2 * hh * v[0], su * 2 * hw * u[1] + sv * 2 * hh * v[1])
        length = max(1e-6, (diag[0] ** 2 + diag[1] ** 2) ** 0.5)
        self._drag = {
            "mode": "resize",
            "anchor": anchor,
            "dir": (diag[0] / length, diag[1] / length),
            "length": length,
            "width_in": self.image.width_in,
            "u": u, "v": v, "su": su, "sv": sv,
        }

    def _handle_pan_update(self, e: ft.DragUpdateEvent):
        if self.image is None or self._drag is None:
            return
        k = self._px_per_in(self._get_specs())
        if self._drag["mode"] == "move":
            if e.local_delta is None:
                return
            self.image.offset_x += e.local_delta.x / k
            self.image.offset_y -= e.local_delta.y / k
        else:
            if e.local_position is None:
                return
            d = self._drag
            ax, ay = d["anchor"]
            along = (e.local_position.x - ax) * d["dir"][0] + (e.local_position.y - ay) * d["dir"][1]
            width_in = max(MIN_WIDTH_IN, d["width_in"] * along / d["length"])
            self.image.width_in = width_in
            hw = width_in * k / 2
            hh = width_in * self.image.aspect * k / 2
            u, v, su, sv = d["u"], d["v"], d["su"], d["sv"]
            cx = ax + su * hw * u[0] + sv * hh * v[0]
            cy = ay + su * hw * u[1] + sv * hh * v[1]
            mid = self._size / 2
            self.image.offset_x = (cx - mid) / k
            self.image.offset_y = (mid - cy) / k
        self._sync_fields()
        self._emit(commit=False)

    def _handle_pan_end(self, e):
        if self._drag is None:
            return
        self._drag = None
        self._frozen_k = None
        self._emit(commit=True)

    def _handle_opacity(self, e):
        if self.image is None:
            return
        self.image.opacity = float(e.control.value)
        self._emit(commit=False)

    def _rotate(self, e):
        if self.image is None:
            return
        self.image.rotation = (self.image.rotation + 90.0) % 360.0
        self._sync_fields()
        self._emit(commit=True)

    def _fit(self, e):
        if self.image is None:
            return
        self.image = fit_to_body(self.image, self._get_specs())
        self._sync_fields()
        self._emit(commit=True)

    def _remove(self, e):
        self.image = None
        self._status.value = ""
        self._refresh(rebuild=True)
        if self._on_change is not None:
            self._on_change(None, True)

    async def _handle_upload(self, e):
        page = self.control.page if _mounted(self.control) else None
        files = await ft.FilePicker().pick_files(
            dialog_title="Robot image",
            file_type=ft.FilePickerFileType.IMAGE,
            with_data=bool(page is not None and page.web),
        )
        if not files:
            return
        picked = files[0]
        source = picked.path or picked.bytes
        try:
            if source is None:
                raise ValueError("no file data")
            image = import_image(source, self._get_specs())
        except Exception as err:  # PIL raises several unrelated types for bad files
            self._status.value = f"Couldn't read {picked.name}: {err}"
            self._status.color = ft.Colors.ERROR
            if _mounted(self._status):
                self._status.update()
            return
        if self.image is not None:
            image.opacity = self.image.opacity
        self.image = image
        self._status.value = f"Using {picked.name}"
        self._status.color = ft.Colors.ON_SURFACE_VARIANT
        self._refresh(rebuild=True)
        if self._on_change is not None:
            self._on_change(self.image, True)


def _closed(points) -> list:
    return [cv.Path.MoveTo(*points[0]), *(cv.Path.LineTo(*p) for p in points[1:]), cv.Path.Close()]


def _resize_cursor(corner: int, rotation: float) -> ft.MouseCursor:
    """Diagonal resize arrow matching the corner's on-screen direction."""
    # Unrotated, corner 0 (top-left) points 45° below the +x screen axis.
    angle = (45.0 + 90.0 * corner + rotation) % 180.0
    if angle < 22.5 or angle >= 157.5:
        return ft.MouseCursor.RESIZE_LEFT_RIGHT
    if angle < 67.5:
        return ft.MouseCursor.RESIZE_UP_LEFT_DOWN_RIGHT
    if angle < 112.5:
        return ft.MouseCursor.RESIZE_UP_DOWN
    return ft.MouseCursor.RESIZE_UP_RIGHT_DOWN_LEFT


def _mounted(control) -> bool:
    try:
        return control.page is not None
    except RuntimeError:
        return False
