"""Plugin Manager screen — installed plugins table + details inspector.

List side shows name / version / source / status; details side shows
metadata, description, and enable/disable controls. Community plugins carry
an untrusted-source warning (policy, not logic).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devworkbench.modules.base import Module
from devworkbench.ui.samples import PLUGINS
from devworkbench.ui.theme import current_colors
from devworkbench.ui.widgets.common import button, combo, panel, search_field, styled_label


def _plugin_table(colors) -> QTableWidget:
    table = QTableWidget(len(PLUGINS), 4)
    table.setHorizontalHeaderLabels(["Name", "Version", "Source", "Status"])
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setColumnWidth(0, 220)
    table.setColumnWidth(1, 70)
    table.setColumnWidth(2, 90)
    table.horizontalHeader().setStretchLastSection(True)
    for row, (name, version, source, status, builtin) in enumerate(PLUGINS):
        table.setItem(row, 0, QTableWidgetItem(name))
        table.setItem(row, 1, QTableWidgetItem(version))
        source_item = QTableWidgetItem(source)
        source_item.setForeground(QColor(colors["cyan"] if source == "Built-in" else colors["purple"]))
        table.setItem(row, 2, source_item)
        status_item = QTableWidgetItem(status)
        status_item.setForeground(QColor(colors["green"] if status == "Enabled" else colors["text3"]))
        table.setItem(row, 3, status_item)
        for col in range(4):
            if not builtin and status == "Disabled":
                table.item(row, col).setForeground(QColor(colors["text3"]))
    table.setCurrentCell(0, 0)
    return table


def build_view(icons, ctx=None) -> QWidget:
    colors = current_colors()
    root = QWidget()
    layout = QVBoxLayout(root)
    layout.setContentsMargins(10, 10, 10, 8)
    layout.setSpacing(8)

    # ---- top bar ---------------------------------------------------------------------
    top = QWidget()
    top_layout = QHBoxLayout(top)
    top_layout.setContentsMargins(0, 0, 0, 0)
    top_layout.setSpacing(8)
    top_layout.addWidget(button("Install from folder…", "ghost"))
    top_layout.addWidget(button("Install from Git URL…", "ghost"))
    top_layout.addSpacing(8)
    top_layout.addWidget(combo(["All plugins", "Enabled", "Disabled"], 0))
    top_layout.addStretch(1)
    top_layout.addWidget(search_field("Search plugins…"))
    layout.addWidget(top)

    split = QHBoxLayout()
    split.setSpacing(8)

    table = _plugin_table(colors)
    split.addWidget(table, 3)

    # ---- details ------------------------------------------------------------------------
    details = panel()
    details_layout = QVBoxLayout(details)
    details_layout.setContentsMargins(12, 12, 12, 12)
    details_layout.setSpacing(8)

    name = QLabel("Compare")
    name.setObjectName("sectionTitle")
    details_layout.addWidget(name)

    pill_row = QWidget()
    pill_row_layout = QHBoxLayout(pill_row)
    pill_row_layout.setContentsMargins(0, 0, 0, 0)
    pill_row_layout.setSpacing(6)
    builtin_pill = QLabel("Built-in")
    builtin_pill.setObjectName("statusPill")
    builtin_pill.setProperty("state", "ok")
    enabled_pill = QLabel("Enabled")
    enabled_pill.setObjectName("statusPill")
    pill_row_layout.addWidget(builtin_pill)
    pill_row_layout.addWidget(enabled_pill)
    pill_row_layout.addStretch(1)
    details_layout.addWidget(pill_row)

    meta = QLabel(
        "ID: <b>devworkbench.compare</b><br>"
        "Version: <b>1.0.0</b> · API: <b>v1</b><br>"
        "Category: <b>module</b><br>"
        "Depends: <b>devworkbench.core</b>"
    )
    meta.setTextFormat(Qt.TextFormat.RichText)
    meta.setWordWrap(True)
    meta.setObjectName("muted")
    details_layout.addWidget(meta)

    desc = QLabel("Side-by-side and unified diffing of files and folders.")
    desc.setWordWrap(True)
    details_layout.addWidget(desc)

    details_layout.addStretch(1)

    warn = QLabel()
    warn.setObjectName("hint")
    warn.setWordWrap(True)
    warn.setText("Community plugins run with your user permissions — review the source before enabling.")
    warn.hide()
    details_layout.addWidget(warn)

    actions = QWidget()
    actions_layout = QHBoxLayout(actions)
    actions_layout.setContentsMargins(0, 0, 0, 0)
    actions_layout.setSpacing(8)
    actions_layout.addWidget(button("Disable", "primary"))
    actions_layout.addWidget(button("Uninstall", "ghost"))
    actions_layout.addWidget(button("View logs", "ghost"))
    actions_layout.addStretch(1)
    details_layout.addWidget(actions)

    split.addWidget(details, 2)
    layout.addLayout(split, 1)
    return root


plugins_module = Module(
    id="plugins",
    title="Plugin Manager",
    icon="plugins",
    build=build_view,
    navigator=(
        ("Installed", ("Core (built-in)", "Compare (built-in)", "Git (built-in)", "format-json (community)")),
        ("Sources", ("local", "github.com")),
    ),
    details=(
        ("Plugins", "10 installed"),
        ("Built-in", "7"),
        ("Community", "3"),
        ("Disabled", "2"),
    ),
    status="Plugin Manager · 10 installed",
)
