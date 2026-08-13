"""Design tokens and small builders for the DevWorkbench Flet shell.

Single source of truth for the visual language: the deep-slate dark
palette, radii, spacing and the shadow used by cards. Screens import
these instead of hard-coding hex values, so a palette change lands in
one place.

Helpers (``padding_all``, ``border_all``, ``radius_all``) exist because
Flet 0.86 dropped the ``ft.padding.all`` / ``ft.border.all`` convenience
functions in favor of explicit dataclass construction.
"""

from __future__ import annotations

import flet as ft

# ---------------------------------------------------------------------------
# Palette — dark first (matching the app's default theme)
# ---------------------------------------------------------------------------

# Deep slate — the app background.
BG = "#0f172a"
# Card / rail surface — elevated panels sit on this.
SURFACE = "#1e293b"
# Slightly lighter surface for hover / nested panels.
SURFACE_HOVER = "#263449"
# Accent blue — interactive highlights, selected states, primary actions.
ACCENT = "#3b82f6"
ACCENT_SOFT = "#1d4ed8"  # pressed / hover variant of the accent
# Subtle border highlight — panels, inputs, dividers.
BORDER = "#334155"
# Text.
TEXT = "#e2e8f0"
TEXT_MUTED = "#94a3b8"
TEXT_FAINT = "#64748b"
# Semantic states.
OK = "#22c55e"
WARN = "#f59e0b"
ERR = "#ef4444"

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

RADIUS = 12      # default control radius
RADIUS_LARGE = 16  # big panels / cards
PADDING = 20     # minimum page/panel padding
GAP = 12         # default spacing between stacked controls

# Soft drop shadow used on cards and floating panels.
SHADOW_COLOR = "#00000059"  # ~35% black
SHADOW_BLUR = 18
SHADOW_OFFSET_Y = 4

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def padding_all(value: float = PADDING) -> ft.Padding:
    """Symmetric padding (Flet 0.86 has no ``padding.all`` helper)."""
    return ft.Padding(left=value, top=value, right=value, bottom=value)


def padding_xy(x: float = PADDING, y: float = PADDING) -> ft.Padding:
    """Horizontal/vertical padding shorthand."""
    return ft.Padding(left=x, top=y, right=x, bottom=y)


def radius_all(value: float = RADIUS) -> ft.BorderRadius:
    """Symmetric border radius."""
    return ft.BorderRadius(
        top_left=value, top_right=value, bottom_left=value, bottom_right=value
    )


def border_all(color: str = BORDER, width: float = 1.0) -> ft.Border:
    """Symmetric 1px border in the panel-highlight color."""
    side = ft.BorderSide(width=width, color=color)
    return ft.Border(top=side, right=side, bottom=side, left=side)


def soft_shadow() -> list[ft.BoxShadow]:
    """The standard soft drop shadow for cards and floating panels."""
    return [
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=SHADOW_BLUR,
            color=SHADOW_COLOR,
            offset=ft.Offset(0, SHADOW_OFFSET_Y),
            blur_style=ft.BlurStyle.NORMAL,
        )
    ]


def card(
    content,
    *,
    radius: float = RADIUS_LARGE,
    padding: ft.Padding | None = None,
    bgcolor: str = SURFACE,
    shadow: bool = True,
    border: bool = True,
    expand: bool = False,
) -> ft.Container:
    """A standard elevated panel: surface fill, rounded corners, subtle
    border and a soft drop shadow — the shell's building block."""
    return ft.Container(
        content=content,
        bgcolor=bgcolor,
        border=border_all() if border else None,
        border_radius=radius_all(radius),
        padding=padding if padding is not None else padding_all(),
        shadow=soft_shadow() if shadow else None,
        expand=expand,
    )
