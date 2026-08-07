"""LevelHistogram — a tiny custom-painted distribution of log levels.

Pure presentation: paints colored bars from a ``{level: count}`` dict.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

from devworkbench.ui.theme import current_colors

_LEVEL_TOKENS = {
    "TRACE": "text3",
    "DEBUG": "cyan",
    "INFO": "green",
    "WARN": "amber",
    "ERROR": "red",
}


class LevelHistogram(QWidget):
    def __init__(self, data: dict[str, int] | None = None) -> None:
        super().__init__()
        self._data: dict[str, int] = data or {}
        self.setFixedHeight(76)
        self.setMinimumWidth(200)

    def set_data(self, data: dict[str, int]) -> None:
        self._data = data
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        colors = current_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(colors["surface"]))

        total = sum(self._data.values()) or 1
        entries = list(self._data.items())
        if not entries:
            return

        margin = 12
        top = 12
        chart_h = 36
        label_h = 16
        width = self.width() - margin * 2
        slot = width / len(entries)
        bar_w = min(26.0, slot * 0.55)
        max_count = max(self._data.values()) or 1

        for i, (level, count) in enumerate(entries):
            cx = margin + slot * i + slot / 2
            height = max(3.0, chart_h * (count / max_count))
            rect = QRectF(cx - bar_w / 2, top + chart_h - height, bar_w, height)
            token = _LEVEL_TOKENS.get(level, "text3")
            color = QColor(colors[token])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 3, 3)

            painter.setPen(QColor(colors["text2"]))
            count_font = QFont(self.font())
            count_font.setPointSizeF(9.5)
            painter.setFont(count_font)
            painter.drawText(
                QRectF(cx - bar_w, top + chart_h - height - 13, bar_w * 2, 12),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                f"{count:,}" if count >= 1000 else str(count),
            )
            painter.setPen(QColor(colors["text3"]))
            painter.drawText(
                QRectF(cx - slot / 2, top + chart_h + 2, slot, label_h),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                level,
            )
        painter.end()
