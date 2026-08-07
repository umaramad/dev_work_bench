"""NavigatorDock — the contextual left panel.

One small tree per module (recent comparisons, repositories, sessions, …),
switched as the active workspace tab changes. Content is mock display data.
"""

from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from devworkbench.ui.theme import current_colors


def _make_tree(icons, sections: list[tuple[str, list[str]]]) -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setIndentation(14)
    tree.setMinimumWidth(150)
    for title, children in sections:
        top = QTreeWidgetItem([title])
        top.setIcon(0, icons.get("folder", 14, color=current_colors()["text3"]))
        for child in children:
            item = QTreeWidgetItem([child])
            item.setIcon(0, icons.get("file", 13, color=current_colors()["text3"]))
            top.addChild(item)
        tree.addTopLevelItem(top)
        top.setExpanded(True)
    return tree


class NavigatorDock(QWidget):
    """Stack of per-module trees; ``set_module(index)`` shows one."""

    def __init__(self, icons) -> None:
        super().__init__()
        self._icons = icons
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack)
        self._trees: list[QTreeWidget] = []

    def populate(self, modules) -> None:
        for module in modules:
            tree = _make_tree(self._icons, module.navigator_items())
            self._stack.addWidget(tree)
            self._trees.append(tree)

    def set_module(self, index: int) -> None:
        if 0 <= index < self._stack.count():
            self._stack.setCurrentIndex(index)
