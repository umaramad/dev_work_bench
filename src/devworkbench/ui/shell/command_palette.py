"""CommandPalette — a fuzzy command finder (⌘⇧P).

Commands are ``(label, icon_key, callable)``; typing filters the list,
Enter runs the highlighted command.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from devworkbench.ui.theme import current_colors

_COMMAND_ROLE = Qt.ItemDataRole.UserRole


class CommandPalette(QDialog):
    def __init__(self, icons, commands: list[tuple[str, str, Callable[[], None]]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command Palette")
        self.setModal(True)
        self.resize(560, 360)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self._icons = icons
        self._commands = commands

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        search_row = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(icons.get("search", 15).pixmap(15, 15))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Type a command…")
        self._search.setClearButtonEnabled(True)
        search_row.addWidget(icon)
        search_row.addWidget(self._search, 1)
        layout.addLayout(search_row)

        self._list = QListWidget()
        self._list.setFrameStyle(0)
        layout.addWidget(self._list, 1)

        hint = QLabel("↑↓ navigate · ↵ run · esc close")
        hint.setObjectName("tiny")
        layout.addWidget(hint)

        self._search.textChanged.connect(self._filter)
        self._list.itemActivated.connect(self._execute)
        self._list.currentRowChanged.connect(self._ensure_visible)

        QShortcut(QKeySequence("Return"), self, activated=self._run_current)
        QShortcut(QKeySequence("Enter"), self, activated=self._run_current)
        QShortcut(QKeySequence("Escape"), self, activated=self.reject)

        self._filter("")
        self._search.setFocus()

    # -- internals -------------------------------------------------------------

    def _filter(self, text: str) -> None:
        self._list.clear()
        needle = text.strip().lower()
        for label, icon_key, _fn in self._commands:
            if needle and needle not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setIcon(self._icons.get(icon_key, 15, color=current_colors()["text2"]))
            item.setData(_COMMAND_ROLE, _fn)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _run_current(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._execute(item)

    def _execute(self, item: QListWidgetItem) -> None:
        fn: Callable[[], None] | None = item.data(_COMMAND_ROLE)
        if fn is not None:
            self.accept()
            fn()

    def _ensure_visible(self, row: int) -> None:
        if row >= 0:
            self._list.scrollToItem(self._list.item(row))
