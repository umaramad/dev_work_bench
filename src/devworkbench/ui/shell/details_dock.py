"""DetailsDock — the inspector panel on the right.

Shows key/value details for the active module (mock rows for now). Empty
state shown when nothing is selected.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from devworkbench.ui.theme import current_colors


class DetailsDock(QWidget):
    def __init__(self, icons) -> None:
        super().__init__()
        self._icons = icons
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(6)
        self._tree.setColumnCount(2)
        self._tree.setColumnWidth(0, 110)
        layout.addWidget(self._tree)
        self.clear()

    def show_entry(self, title: str, rows: list[tuple[str, str]]) -> None:
        self._tree.clear()
        colors = current_colors()
        head = QTreeWidgetItem([title])
        head.setIcon(0, self._icons.get("file", 14, color=colors["accent"]))
        bold = head.font(0)
        bold.setBold(True)
        head.setFont(0, bold)
        self._tree.addTopLevelItem(head)
        for key, value in rows:
            item = QTreeWidgetItem([key, value])
            item.setForeground(0, QColor(colors["text3"]))
            head.addChild(item)
        head.setExpanded(True)

    def clear(self) -> None:
        self._tree.clear()
        item = QTreeWidgetItem(["No selection"])
        item.setForeground(0, QColor(current_colors()["text3"]))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._tree.addTopLevelItem(item)
