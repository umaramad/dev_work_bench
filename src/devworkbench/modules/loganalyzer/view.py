"""Log Analyzer screen — level histogram, color-coded log table, detail pane.

Presentation only: mock rows, filter chips, FTS-style search field, and a
detail pane showing the selected entry's trace.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devworkbench.modules.base import Module
from devworkbench.ui.samples import LOG_DETAIL, LOG_FILE, LOG_HISTOGRAM, LOG_ROWS
from devworkbench.ui.theme import current_colors
from devworkbench.ui.widgets.common import (
    button,
    combo,
    icon_button,
    make_chips,
    panel,
    search_field,
    splitter,
    styled_label,
)

_LEVEL_COLOR = {"TRACE": "text3", "DEBUG": "cyan", "INFO": "text2", "WARN": "amber", "ERROR": "red"}
_LEVEL_BG = {"WARN": ("amber", 0.10), "ERROR": ("red", 0.10)}


def _log_table(colors) -> QTableWidget:
    table = QTableWidget(len(LOG_ROWS), 4)
    table.setHorizontalHeaderLabels(["Time", "Level", "Source", "Message"])
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setColumnWidth(0, 96)
    table.setColumnWidth(1, 64)
    table.setColumnWidth(2, 76)
    table.horizontalHeader().setStretchLastSection(True)
    for row, (time, level, source, message) in enumerate(LOG_ROWS):
        table.setItem(row, 0, QTableWidgetItem(time))
        level_item = QTableWidgetItem(level)
        token = _LEVEL_COLOR.get(level, "text2")
        level_item.setForeground(QColor(colors[token]))
        level_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, 1, level_item)
        table.setItem(row, 2, QTableWidgetItem(source))
        table.setItem(row, 3, QTableWidgetItem(message))
        bg_token = _LEVEL_BG.get(level)
        if bg_token:
            brush_color = QColor(colors[bg_token[0]])
            brush_color.setAlphaF(bg_token[1])
            table.item(row, 0).setBackground(brush_color)
            level_item.setBackground(brush_color)
            table.item(row, 2).setBackground(brush_color)
            table.item(row, 3).setBackground(brush_color)
    table.setCurrentCell(5, 0)
    return table


def build_view(icons, ctx=None) -> QWidget:
    colors = current_colors()
    root = QWidget()
    layout = QVBoxLayout(root)
    layout.setContentsMargins(10, 10, 10, 8)
    layout.setSpacing(8)

    # ---- toolbar ---------------------------------------------------------------------
    toolbar = QWidget()
    toolbar_layout = QHBoxLayout(toolbar)
    toolbar_layout.setContentsMargins(0, 0, 0, 0)
    toolbar_layout.setSpacing(8)
    toolbar_layout.addWidget(icon_button(icons, "open", "Open log file…"))
    toolbar_layout.addWidget(styled_label(LOG_FILE, "tiny"))
    toolbar_layout.addSpacing(6)
    toolbar_layout.addWidget(combo(["Last hour", "Since noon", "Today", "All time"], 3))
    toolbar_layout.addStretch(1)
    toolbar_layout.addWidget(search_field("Search in log (FTS5)…"))
    layout.addWidget(toolbar)

    # ---- level chips ------------------------------------------------------------------
    chips, chips_widget = make_chips(icons, ["TRACE", "DEBUG", "INFO", "WARN", "ERROR"])
    for chip in chips[2:]:
        chip.setChecked(True)
    layout.addWidget(chips_widget)

    # ---- histogram + table + detail ------------------------------------------------------
    from devworkbench.ui.widgets.histogram import LevelHistogram

    histogram = LevelHistogram(LOG_HISTOGRAM)
    layout.addWidget(histogram)

    bottom_split = splitter(Qt.Orientation.Vertical)
    table = _log_table(colors)
    bottom_split.addWidget(table)

    detail = panel()
    detail_layout = QVBoxLayout(detail)
    detail_layout.setContentsMargins(10, 8, 10, 8)
    detail_layout.setSpacing(6)
    detail_header = QHBoxLayout()
    detail_header.addWidget(styled_label("Selected entry", "muted"))
    detail_header.addStretch(1)
    detail_header.addWidget(button("Copy", "ghost"))
    detail_layout.addLayout(detail_header)
    trace = QLabel(f"<pre style='font-family:Menlo; font-size:11px; color:{colors['text2']}'>{LOG_DETAIL}</pre>")
    trace.setTextFormat(Qt.TextFormat.RichText)
    trace.setWordWrap(True)
    detail_layout.addWidget(trace)
    bottom_split.addWidget(detail)
    bottom_split.setSizes([360, 140])
    layout.addWidget(bottom_split, 1)

    return root


loganalyzer_module = Module(
    id="loganalyzer",
    title="Log Analyzer",
    icon="log",
    build=build_view,
    navigator=(
        ("Recent files", ("server.log", "worker-1.log", "nginx.error.log")),
        ("Saved filters", ("500 errors", "slow queries")),
    ),
    details=(
        ("File", "server.log"),
        ("Size", "1.2 MB"),
        ("Lines", "18,420"),
        ("Errors", "264"),
    ),
    status="Log Analyzer · server.log · 18,420 lines",
)
