import flet as ft

# Blue accent palette tuned for dark surfaces
ACCENT = "#3B82F6"
ACCENT_LIGHT = "#60A5FA"
ACCENT_CONTAINER = "#1E3A5F"
ACCENT_BORDER = "#2563EB"

MONO_FONT = "JetBrains Mono SemiBold"
MONO_TEXT_STYLE = ft.TextStyle(font_family=MONO_FONT, weight=ft.FontWeight.W_600)

TITLE_FONT = "Epilogue"

GREEN = "#16A34A"
GREEN_HOVER = "#15803D"
RED = "#DC2626"
RED_HOVER = "#B91C1C"
RED_LIGHT = "#EF4444"

GREEN_BUTTON_STYLE = ft.ButtonStyle(
    bgcolor=GREEN,
    color=ft.Colors.WHITE,
    overlay_color=ft.Colors.with_opacity(0.12, ft.Colors.WHITE),
)

RED_BUTTON_STYLE = ft.ButtonStyle(
    bgcolor=RED,
    color=ft.Colors.WHITE,
    overlay_color=ft.Colors.with_opacity(0.12, ft.Colors.WHITE),
)

OUTLINED_GREEN_STYLE = ft.ButtonStyle(
    color=GREEN,
    side=ft.BorderSide(1.5, GREEN),
    overlay_color=ft.Colors.with_opacity(0.12, GREEN),
)


def apply_page_theme(page: ft.Page) -> None:
    page.fonts = {
        MONO_FONT: "fonts/JetBrainsMono-SemiBold.ttf",
        TITLE_FONT: "fonts/Epilogue-BoldItalic.ttf",
    }
    page.dark_theme = ft.Theme(
        color_scheme=ft.ColorScheme(
            primary=ACCENT,
            on_primary=ft.Colors.WHITE,
            primary_container=ACCENT_CONTAINER,
            on_primary_container="#BFDBFE",
            secondary=ACCENT_LIGHT,
            on_secondary=ft.Colors.WHITE,
        ),
    )
    page.theme_mode = ft.ThemeMode.DARK


def title_text(value: str = "", *, size: int, **kwargs) -> ft.Text:
    """Panel / editor / section titles in italic Epilogue."""
    return ft.Text(
        value,
        size=size,
        font_family=TITLE_FONT,
        italic=True,
        weight=ft.FontWeight.BOLD,
        **kwargs,
    )


def green_button(text: str, **kwargs) -> ft.FilledButton:
    return ft.FilledButton(text, style=GREEN_BUTTON_STYLE, **kwargs)


def red_button(text: str, **kwargs) -> ft.FilledButton:
    return ft.FilledButton(text, style=RED_BUTTON_STYLE, **kwargs)
