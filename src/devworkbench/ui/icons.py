"""Programmatic icon set — flat glyphs painted with QPainter.

Why: no binary assets, zero external files, and pixel-perfect **Retina**
rendering. Every glyph is drawn into a pixmap at ``size * devicePixelRatio``
and the pixmap carries that ratio, so Qt picks the crisp version natively on
high-DPI displays.

Icons are drawn in the active theme's color (default: secondary text color)
so they blend with dark/light themes; ``color`` can be overridden per use
(e.g. accent-colored when a nav item is selected).
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtCore import QLineF

from devworkbench.ui.theme import current_colors


class IconProvider:
    """Draws and caches themed glyph icons."""

    def __init__(self, base_size: int = 16) -> None:
        self._base = base_size
        self._cache: dict[tuple[str, int, str], QIcon] = {}

    # -- public -------------------------------------------------------------

    def get(self, name: str, size: int | None = None, color: str | None = None) -> QIcon:
        """Return a cached QIcon for ``name``.

        ``color`` may be a hex string like ``"#5b8def"`` or one of the theme
        token keys (``text``, ``text2``, ``text3``, ``accent``, ...).
        """
        size = size or self._base
        color = self._resolve_color(color)
        key = (name, size, color)
        icon = self._cache.get(key)
        if icon is None:
            icon = self._render(name, size, color)
            self._cache[key] = icon
        return icon

    def colors(self, key: str) -> str:
        return current_colors().get(key, key)

    # -- internals ------------------------------------------------------------

    def _resolve_color(self, color: str | None) -> str:
        if color is None:
            return self.colors("text2")
        if color in current_colors():
            return self.colors(color)
        return color

    def _render(self, name: str, size: int, color: str) -> QIcon:
        draw = _GLYPHS.get(name, _glyph_circle)
        dpr = 2  # draw at 2x on every display; Qt downscales for 1x screens
        pm = QPixmap(size * dpr, size * dpr)
        pm.fill(Qt.GlobalColor.transparent)
        pm.setDevicePixelRatio(dpr)

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            draw(p, size, QColor(color))
        finally:
            p.end()
        return QIcon(pm)


# --------------------------------------------------------------------------
# Drawing helpers
# --------------------------------------------------------------------------

def _pen(color: QColor, size: float = 1.6) -> QPen:
    pen = QPen(color, size)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _centered(size: float, box: float = 16.0) -> QRectF:
    """Return a square rect of ``size`` centered in a ``box`` viewBox."""
    margin = (box - size) / 2.0
    return QRectF(margin, margin, size, size)


def _glyph_circle(p: QPainter, s: int, c: QColor) -> None:
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(_centered(s * 0.7, s))
    p.drawPoint(QPointF(s / 2, s / 2))


def _glyph_app(p: QPainter, s: int, c: QColor) -> None:
    """App logo: a rounded workbench tile holding a terminal window with a
    ``>_`` prompt and a muted code line — reads as a developer console at
    any size (16px menu glyph up to the 1024px app icon). Theme tokens are
    read live, so the icon keeps the app's accent color."""
    colors = current_colors()
    accent = QColor(colors.get("accent", c.name()))
    bg = QColor(colors.get("bg", "#16171c"))
    surface = QColor(colors.get("surface", "#1f2126"))
    surface2 = QColor(colors.get("surface2", "#262a32"))
    text3 = QColor(colors.get("text3", "#6d7686"))

    # -- tile: subtle vertical gradient + a faint accent rim -----------------
    m = s * 0.055
    tile = QRectF(m, m, s - 2 * m, s - 2 * m)
    path = QPainterPath()
    path.addRoundedRect(tile, s * 0.21, s * 0.21)
    gradient = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    gradient.setColorAt(0.0, surface2)
    gradient.setColorAt(1.0, surface)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(gradient))
    p.drawPath(path)
    rim = QColor(accent)
    rim.setAlpha(85)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(rim, max(1.0, s * 0.012)))
    p.drawPath(path)

    # -- terminal window ------------------------------------------------------
    win = QRectF(s * 0.24, s * 0.30, s * 0.52, s * 0.40)
    win_path = QPainterPath()
    win_path.addRoundedRect(win, s * 0.06, s * 0.06)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawPath(win_path)
    border = QColor(text3)
    border.setAlpha(120)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(border, max(1.0, s * 0.010)))
    p.drawPath(win_path)

    # traffic lights (red / amber / green)
    dot_r = s * 0.022
    dot_y = win.top() + s * 0.055
    p.setPen(Qt.PenStyle.NoPen)
    for i, key in enumerate(("red", "amber", "green")):
        dot = QColor(colors.get(key, "#888888"))
        p.setBrush(dot)
        p.drawEllipse(QPointF(win.left() + s * 0.075 + i * s * 0.055, dot_y), dot_r, dot_r)

    # ``>`` prompt chevron in the accent color
    prompt = QPen(accent, max(1.2, s * 0.042))
    prompt.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(prompt)
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx = win.left() + s * 0.115
    cy = s * 0.49
    arm = s * 0.035
    p.drawLine(QPointF(cx, cy - arm), QPointF(cx + arm * 1.5, cy))
    p.drawLine(QPointF(cx, cy + arm), QPointF(cx + arm * 1.5, cy))
    # ``_`` cursor
    p.drawLine(QPointF(win.left() + s * 0.205, cy), QPointF(win.left() + s * 0.30, cy))

    # muted code line
    code = QPen(text3, max(1.0, s * 0.026))
    code.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(code)
    p.drawLine(QPointF(win.left() + s * 0.115, s * 0.615), QPointF(win.left() + s * 0.335, s * 0.615))


def _glyph_compare(p: QPainter, s: int, c: QColor) -> None:
    # two side-by-side file panes with a swap arrow between them
    pen = _pen(c, 1.4)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    half = s * 0.28
    gap = s * 0.10
    lx = s * 0.18
    rx = s - s * 0.18 - half
    for x in (lx, rx):
        r = QRectF(x, s * 0.16, half, s * 0.68)
        p.drawRoundedRect(r, s * 0.06, s * 0.06)
        # inner line
        p.drawLine(QPointF(x + half * 0.28, s * 0.40), QPointF(x + half * 0.72, s * 0.40))
    # double arrow in the middle gap
    mid = s / 2
    arrow_pen = _pen(c, 1.3)
    p.setPen(arrow_pen)
    p.drawLine(QPointF(lx + half + gap * 0.3, mid - s * 0.14), QPointF(rx - gap * 0.3, mid - s * 0.14))
    p.drawLine(QPointF(rx - gap * 0.3, mid - s * 0.14), QPointF(rx - gap * 0.3 - s * 0.07, mid - s * 0.14 + s * 0.06))
    p.drawLine(QPointF(rx - gap * 0.3, mid - s * 0.14), QPointF(rx - gap * 0.3 - s * 0.07, mid - s * 0.14 - s * 0.06))
    p.drawLine(QPointF(lx + half + gap * 0.3, mid + s * 0.14), QPointF(rx - gap * 0.3, mid + s * 0.14))
    p.drawLine(QPointF(lx + half + gap * 0.3, mid + s * 0.14), QPointF(lx + half + gap * 0.3 + s * 0.07, mid + s * 0.14 + s * 0.06))
    p.drawLine(QPointF(lx + half + gap * 0.3, mid + s * 0.14), QPointF(lx + half + gap * 0.3 + s * 0.07, mid + s * 0.14 - s * 0.06))


def _glyph_git(p: QPainter, s: int, c: QColor) -> None:
    # git branch: tip circle, trunk, two curves
    pen = _pen(c, 1.4)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # trunk
    p.drawLine(QPointF(s * 0.30, s * 0.28), QPointF(s * 0.30, s * 0.62))
    # left curve to top branch
    path = QPainterPath(QPointF(s * 0.30, s * 0.62))
    path.cubicTo(QPointF(s * 0.30, s * 0.78), QPointF(s * 0.70, s * 0.74), QPointF(s * 0.70, s * 0.60))
    path.moveTo(s * 0.30, s * 0.62)
    path.cubicTo(QPointF(s * 0.30, s * 0.46), QPointF(s * 0.70, s * 0.42), QPointF(s * 0.70, s * 0.26))
    p.drawPath(path)
    # tip circle
    p.setBrush(c)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(s * 0.30, s * 0.28), s * 0.09, s * 0.09)
    p.drawEllipse(QPointF(s * 0.70, s * 0.60), s * 0.07, s * 0.07)
    p.drawEllipse(QPointF(s * 0.70, s * 0.26), s * 0.07, s * 0.07)


def _glyph_ai(p: QPainter, s: int, c: QColor) -> None:
    # sparkle: four-point star + small plus
    def star(cx: float, cy: float, r: float) -> None:
        path = QPainterPath()
        path.moveTo(cx, cy - r)
        path.quadTo(cx + r * 0.18, cy - r * 0.18, cx + r, cy)
        path.quadTo(cx + r * 0.18, cy + r * 0.18, cx, cy + r)
        path.quadTo(cx - r * 0.18, cy + r * 0.18, cx - r, cy)
        path.quadTo(cx - r * 0.18, cy - r * 0.18, cx, cy - r)
        p.fillPath(path, c)

    star(s * 0.66, s * 0.38, s * 0.34)
    p.setPen(_pen(c, 1.3))
    p.drawLine(QPointF(s * 0.28, s * 0.62), QPointF(s * 0.28, s * 0.86))
    p.drawLine(QPointF(s * 0.16, s * 0.74), QPointF(s * 0.40, s * 0.74))
    star(s * 0.30, s * 0.74, s * 0.10)


def _glyph_terminal(p: QPainter, s: int, c: QColor) -> None:
    r = QRectF(s * 0.12, s * 0.14, s * 0.76, s * 0.72)
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(r, s * 0.08, s * 0.08)
    # prompt ">"
    p.setPen(_pen(c, 1.4))
    p.drawLine(QPointF(s * 0.30, s * 0.42), QPointF(s * 0.44, s * 0.50))
    p.drawLine(QPointF(s * 0.30, s * 0.58), QPointF(s * 0.44, s * 0.50))
    # underscore cursor
    p.drawLine(QPointF(s * 0.52, s * 0.60), QPointF(s * 0.72, s * 0.60))


def _glyph_log(p: QPainter, s: int, c: QColor) -> None:
    r = QRectF(s * 0.14, s * 0.10, s * 0.56, s * 0.80)
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(r, s * 0.05, s * 0.05)
    pen = _pen(c, 1.2)
    p.setPen(pen)
    for i in range(3):
        y = s * 0.34 + i * s * 0.15
        p.drawLine(QPointF(s * 0.24, y), QPointF(s * 0.58, y))
    # magnifier over the doc
    pen2 = _pen(c, 1.3)
    p.setPen(pen2)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(s * 0.52, s * 0.44, s * 0.36, s * 0.36))
    p.drawLine(QPointF(s * 0.80, s * 0.72), QPointF(s * 0.94, s * 0.86))


def _glyph_gear(p: QPainter, s: int, c: QColor) -> None:
    center = QPointF(s / 2, s / 2)
    radius = s * 0.24
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    # teeth
    for i in range(8):
        angle = i * 45.0
        import math

        rad = math.radians(angle)
        x1 = center.x() + math.cos(rad) * (radius + s * 0.02)
        y1 = center.y() + math.sin(rad) * (radius + s * 0.02)
        x2 = center.x() + math.cos(rad) * (radius + s * 0.16)
        y2 = center.y() + math.sin(rad) * (radius + s * 0.16)
        p.setPen(_pen(c, s * 0.09))
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.drawEllipse(center, radius, radius)
    # hole
    p.setBrush(QColor(current_colors()["surface"]))
    p.drawEllipse(center, radius * 0.42, radius * 0.42)


def _glyph_plugin(p: QPainter, s: int, c: QColor) -> None:
    # puzzle piece: rounded square with a knob on top and a notch on the right
    path = QPainterPath()
    x0, y0 = s * 0.16, s * 0.24
    w = s * 0.68
    path.moveTo(x0, y0)
    # knob on top
    path.lineTo(x0 + w * 0.36, y0)
    path.arcTo(QRectF(x0 + w * 0.36, y0 - s * 0.16, s * 0.16, s * 0.16), 180, -180)
    path.lineTo(x0 + w, y0)
    # notch on right side
    path.lineTo(x0 + w, y0 + w * 0.42)
    path.arcTo(QRectF(x0 + w - s * 0.08, y0 + w * 0.42, s * 0.16, s * 0.16), -90, 180)
    path.lineTo(x0 + w, y0 + w)
    path.lineTo(x0, y0 + w)
    path.closeSubpath()
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def _glyph_folder(p: QPainter, s: int, c: QColor) -> None:
    path = QPainterPath()
    x0, y0 = s * 0.14, s * 0.30
    w, h = s * 0.72, s * 0.48
    path.moveTo(x0, y0)
    path.lineTo(x0 + w * 0.30, y0)
    path.lineTo(x0 + w * 0.38, y0 + h * 0.30)
    path.lineTo(x0 + w, y0 + h * 0.30)
    path.lineTo(x0 + w, y0 + h)
    path.lineTo(x0, y0 + h)
    path.closeSubpath()
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    # tab
    p.drawLine(QPointF(x0, y0), QPointF(x0 + w * 0.30, y0 - s * 0.06))
    p.drawLine(QPointF(x0 + w * 0.30, y0 - s * 0.06), QPointF(x0 + w * 0.44, y0))


def _glyph_file(p: QPainter, s: int, c: QColor) -> None:
    r = QRectF(s * 0.22, s * 0.14, s * 0.44, s * 0.72)
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(r, s * 0.05, s * 0.05)
    p.setPen(_pen(c, 1.1))
    for i in range(3):
        y = s * 0.40 + i * s * 0.14
        p.drawLine(QPointF(s * 0.30, y), QPointF(s * 0.58, y))


def _glyph_refresh(p: QPainter, s: int, c: QColor) -> None:
    pen = _pen(c, 1.5)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    rect = _centered(s * 0.62, s)
    p.drawArc(rect, 40 * 16, 280 * 16)
    # arrowhead at arc start
    tip = QPointF(rect.center().x(), rect.top() + s * 0.02)
    p.setBrush(c)
    p.setPen(Qt.PenStyle.NoPen)
    path = QPainterPath()
    path.moveTo(tip)
    path.lineTo(tip.x() - s * 0.09, tip.y() + s * 0.12)
    path.lineTo(tip.x() + s * 0.09, tip.y() + s * 0.12)
    path.closeSubpath()
    p.drawPath(path)


def _glyph_play(p: QPainter, s: int, c: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    path = QPainterPath()
    path.moveTo(s * 0.28, s * 0.20)
    path.lineTo(s * 0.80, s * 0.50)
    path.lineTo(s * 0.28, s * 0.80)
    path.closeSubpath()
    p.drawPath(path)


def _glyph_stop(p: QPainter, s: int, c: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(s * 0.26, s * 0.26, s * 0.48, s * 0.48), s * 0.06, s * 0.06)


def _glyph_search(p: QPainter, s: int, c: QColor) -> None:
    p.setPen(_pen(c, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(s * 0.16, s * 0.16, s * 0.46, s * 0.46))
    p.drawLine(QPointF(s * 0.54, s * 0.54), QPointF(s * 0.84, s * 0.84))


def _glyph_close(p: QPainter, s: int, c: QColor) -> None:
    pen = _pen(c, 1.5)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.24, s * 0.24), QPointF(s * 0.76, s * 0.76))
    p.drawLine(QPointF(s * 0.76, s * 0.24), QPointF(s * 0.24, s * 0.76))


def _glyph_chevron(p: QPainter, s: int, c: QColor, flip: bool) -> None:
    pen = _pen(c, 1.6)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    if flip:
        path.moveTo(s * 0.30, s * 0.26)
        path.lineTo(s * 0.66, s * 0.50)
        path.lineTo(s * 0.30, s * 0.74)
    else:
        path.moveTo(s * 0.70, s * 0.26)
        path.lineTo(s * 0.34, s * 0.50)
        path.lineTo(s * 0.70, s * 0.74)
    p.drawPath(path)


def _glyph_down(p: QPainter, s: int, c: QColor) -> None:
    pen = _pen(c, 1.6)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(s * 0.26, s * 0.32)
    path.lineTo(s * 0.50, s * 0.66)
    path.lineTo(s * 0.74, s * 0.32)
    p.drawPath(path)


def _glyph_up(p: QPainter, s: int, c: QColor) -> None:
    pen = _pen(c, 1.6)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(s * 0.26, s * 0.68)
    path.lineTo(s * 0.50, s * 0.34)
    path.lineTo(s * 0.74, s * 0.68)
    p.drawPath(path)


def _glyph_swap(p: QPainter, s: int, c: QColor) -> None:
    pen = _pen(c, 1.4)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.22, s * 0.40), QPointF(s * 0.78, s * 0.40))
    p.drawLine(QPointF(s * 0.62, s * 0.28), QPointF(s * 0.78, s * 0.40))
    p.drawLine(QPointF(s * 0.62, s * 0.52), QPointF(s * 0.78, s * 0.40))
    p.drawLine(QPointF(s * 0.78, s * 0.62), QPointF(s * 0.22, s * 0.62))
    p.drawLine(QPointF(s * 0.38, s * 0.74), QPointF(s * 0.22, s * 0.62))
    p.drawLine(QPointF(s * 0.38, s * 0.50), QPointF(s * 0.22, s * 0.62))


def _glyph_plus(p: QPainter, s: int, c: QColor) -> None:
    pen = _pen(c, 1.6)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.30, s * 0.50), QPointF(s * 0.70, s * 0.50))
    p.drawLine(QPointF(s * 0.50, s * 0.30), QPointF(s * 0.50, s * 0.70))


def _glyph_check(p: QPainter, s: int, c: QColor) -> None:
    pen = _pen(c, 1.7)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.22, s * 0.52), QPointF(s * 0.42, s * 0.72))
    p.drawLine(QPointF(s * 0.42, s * 0.72), QPointF(s * 0.78, s * 0.32))


def _glyph_dots(p: QPainter, s: int, c: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    for i, x in enumerate((s * 0.28, s * 0.50, s * 0.72)):
        p.drawEllipse(QPointF(x, s * 0.50), s * 0.07, s * 0.07)


def _glyph_moon(p: QPainter, s: int, c: QColor) -> None:
    path = QPainterPath()
    path.addEllipse(QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64))
    moon = QPainterPath()
    moon.addEllipse(QRectF(s * 0.40, s * 0.12, s * 0.60, s * 0.60))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawPath(path.subtracted(moon))


def _glyph_sun(p: QPainter, s: int, c: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawEllipse(QPointF(s / 2, s / 2), s * 0.22, s * 0.22)
    pen = _pen(c, 1.3)
    p.setPen(pen)
    for i in range(8):
        import math

        ang = math.radians(i * 45)
        r1, r2 = s * 0.34, s * 0.46
        p.drawLine(
            QPointF(s / 2 + math.cos(ang) * r1, s / 2 + math.sin(ang) * r1),
            QPointF(s / 2 + math.cos(ang) * r2, s / 2 + math.sin(ang) * r2),
        )


def _glyph_key(p: QPainter, s: int, c: QColor) -> None:
    p.setPen(_pen(c, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(s * 0.14, s * 0.36, s * 0.36, s * 0.36))
    p.drawLine(QPointF(s * 0.44, s * 0.62), QPointF(s * 0.82, s * 0.62))
    p.drawLine(QPointF(s * 0.70, s * 0.62), QPointF(s * 0.70, s * 0.76))
    p.drawLine(QPointF(s * 0.82, s * 0.62), QPointF(s * 0.82, s * 0.76))


def _glyph_history(p: QPainter, s: int, c: QColor) -> None:
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    rect = _centered(s * 0.68, s)
    p.drawArc(rect, 0, 360 * 16)
    pen = _pen(c, 1.3)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.50, s * 0.38), QPointF(s * 0.50, s * 0.52))
    p.drawLine(QPointF(s * 0.50, s * 0.52), QPointF(s * 0.62, s * 0.58))
    # counter-clockwise arrow
    p.drawLine(QPointF(s * 0.26, s * 0.36), QPointF(s * 0.16, s * 0.26))
    p.drawLine(QPointF(s * 0.16, s * 0.26), QPointF(s * 0.30, s * 0.22))


def _glyph_alert(p: QPainter, s: int, c: QColor) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.50, s * 0.14)
    path.lineTo(s * 0.90, s * 0.82)
    path.lineTo(s * 0.10, s * 0.82)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawPath(path)
    p.setBrush(QColor(current_colors()["bg"]))
    p.drawRect(QRectF(s * 0.46, s * 0.38, s * 0.08, s * 0.22))
    p.drawEllipse(QPointF(s * 0.50, s * 0.70), s * 0.045, s * 0.045)


def _glyph_info(p: QPainter, s: int, c: QColor) -> None:
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.68))
    pen = _pen(c, 1.5)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.50, s * 0.42), QPointF(s * 0.50, s * 0.68))
    p.drawEllipse(QPointF(s * 0.50, s * 0.34), s * 0.045, s * 0.045)


def _glyph_branch_pill(p: QPainter, s: int, c: QColor) -> None:
    _glyph_git(p, s, c)


def _glyph_plug(p: QPainter, s: int, c: QColor) -> None:
    # plug / bolt
    path = QPainterPath()
    path.moveTo(s * 0.52, s * 0.12)
    path.lineTo(s * 0.34, s * 0.50)
    path.lineTo(s * 0.44, s * 0.50)
    path.lineTo(s * 0.42, s * 0.88)
    path.lineTo(s * 0.60, s * 0.50)
    path.lineTo(s * 0.50, s * 0.50)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawPath(path)


def _glyph_eye(p: QPainter, s: int, c: QColor) -> None:
    pen = _pen(c, 1.4)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(s * 0.10, s * 0.30, s * 0.80, s * 0.40))
    p.setBrush(c)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.11, s * 0.11)


def _glyph_edit(p: QPainter, s: int, c: QColor) -> None:
    # pencil: tip bottom-left, shaft up-right, eraser cap top-right
    p.setPen(_pen(c, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(s * 0.28, s * 0.82), QPointF(s * 0.72, s * 0.38))
    p.drawLine(QPointF(s * 0.38, s * 0.84), QPointF(s * 0.80, s * 0.42))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    tip = QPainterPath()
    tip.moveTo(s * 0.18, s * 0.86)
    tip.lineTo(s * 0.30, s * 0.80)
    tip.lineTo(s * 0.38, s * 0.88)
    tip.closeSubpath()
    p.drawPath(tip)
    # eraser cap
    p.setPen(_pen(c, 1.4))
    p.drawLine(QPointF(s * 0.64, s * 0.28), QPointF(s * 0.82, s * 0.46))


def _glyph_download(p: QPainter, s: int, c: QColor) -> None:
    # arrow down into a tray — fetch / pull
    pen = _pen(c, 1.5)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(s * 0.50, s * 0.16), QPointF(s * 0.50, s * 0.54))
    p.drawLine(QPointF(s * 0.38, s * 0.42), QPointF(s * 0.50, s * 0.54))
    p.drawLine(QPointF(s * 0.62, s * 0.42), QPointF(s * 0.50, s * 0.54))
    p.drawLine(QPointF(s * 0.20, s * 0.74), QPointF(s * 0.80, s * 0.74))
    p.drawLine(QPointF(s * 0.28, s * 0.84), QPointF(s * 0.72, s * 0.84))


def _glyph_home(p: QPainter, s: int, c: QColor) -> None:
    # Simple house: roof triangle + body rectangle + door notch
    pen = _pen(c, 1.4)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    peak = QPointF(s * 0.50, s * 0.18)
    left = QPointF(s * 0.16, s * 0.46)
    right = QPointF(s * 0.84, s * 0.46)
    p.drawLine(peak, left)
    p.drawLine(peak, right)
    p.drawLine(left, right)
    body = QRectF(s * 0.24, s * 0.46, s * 0.52, s * 0.36)
    p.drawRect(body)
    door = QRectF(s * 0.42, s * 0.58, s * 0.16, s * 0.24)
    p.drawRect(door)


def _glyph_network(p: QPainter, s: int, c: QColor) -> None:
    # server: two stacked boxes
    p.setPen(_pen(c, 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i, y in enumerate((s * 0.16, s * 0.52)):
        p.drawRoundedRect(QRectF(s * 0.18, y, s * 0.64, s * 0.30), s * 0.05, s * 0.05)
    pen = _pen(c, 1.3)
    p.setPen(pen)
    for y in (s * 0.31, s * 0.67):
        p.drawEllipse(QPointF(s * 0.36, y), s * 0.035, s * 0.035)
        p.drawEllipse(QPointF(s * 0.50, y), s * 0.035, s * 0.035)


_GLYPHS: dict[str, object] = {
    "app": _glyph_app,
    "home": _glyph_home,
    "compare": _glyph_compare,
    "git": _glyph_git,
    "ai": _glyph_ai,
    "ssh": _glyph_terminal,
    "log": _glyph_log,
    "settings": _glyph_gear,
    "plugins": _glyph_plugin,
    "folder": _glyph_folder,
    "file": _glyph_file,
    "open": _glyph_folder,
    "refresh": _glyph_refresh,
    "play": _glyph_play,
    "stop": _glyph_stop,
    "search": _glyph_search,
    "close": _glyph_close,
    "chevron_left": lambda p, s, c: _glyph_chevron(p, s, c, True),
    "chevron_right": lambda p, s, c: _glyph_chevron(p, s, c, False),
    "chevron_down": _glyph_down,
    "chevron_up": _glyph_up,
    "swap": _glyph_swap,
    "plus": _glyph_plus,
    "check": _glyph_check,
    "dots": _glyph_dots,
    "moon": _glyph_moon,
    "sun": _glyph_sun,
    "key": _glyph_key,
    "history": _glyph_history,
    "alert": _glyph_alert,
    "info": _glyph_info,
    "plug": _glyph_plug,
    "eye": _glyph_eye,
    "network": _glyph_network,
    "terminal": _glyph_terminal,
    "edit": _glyph_edit,
    "download": _glyph_download,
}
