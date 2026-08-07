"""OutputDock — the bottom panel with Terminal / Command Log / Tasks tabs.

All content is mock display data (no execution).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devworkbench.ui.theme import current_colors


class _TerminalPane(QPlainTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFrameStyle(0)
        font = QFont()
        font.setFamilies(["SF Mono", "Menlo", "monospace"])
        font.setPointSizeF(11.5)
        self.setFont(font)
        self.setPlainText(
            "$ git status\n"
            "On branch main\n"
            "Your branch is up to date with 'origin/main'.\n"
            "\n"
            "Changes not staged for commit:\n"
            "  modified:   src/devworkbench/ui/main_window.py\n"
            "\n"
            "$ git diff --stat\n"
            " src/devworkbench/ui/main_window.py | 12 ++++++++--\n"
            "$ "
        )

    def append_line(self, text: str) -> None:
        """Append ``text`` below a fresh prompt line (terminal-style)."""
        self.moveCursor(QTextCursor.MoveOperation.End)
        self.insertPlainText(text + "\n$ ")
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class _CommandLogPane(QListWidget):
    def __init__(self, icons) -> None:
        super().__init__()
        self.setFrameStyle(0)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        colors = current_colors()
        rows = [
            ("git status", "0.4s", "check", "ok"),
            ("git fetch origin", "1.1s", "check", "ok"),
            ("pip install PySide6-Essentials", "18.2s", "check", "ok"),
            ("diff app.py app.py.bak", "0.2s", "check", "ok"),
            ("indexer: rebuild log_index", "4.6s", "alert", "err"),
        ]
        state_color = {"ok": "green", "err": "red"}
        for command, duration, icon, state in rows:
            item = QListWidgetItem(f"{command}   ·   {duration}")
            item.setIcon(icons.get(icon, 14, color=colors[state_color[state]]))
            self.addItem(item)


class _TasksPane(QTableWidget):
    def __init__(self) -> None:
        super().__init__(0, 3)
        self.setHorizontalHeaderLabels(["Task", "Status", "Progress"])
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setColumnWidth(0, 260)
        self.setColumnWidth(1, 90)
        tasks = [
            ("Update log index", "Running", 64),
            ("Fetch origin", "Done", 100),
            ("Transfer access.log", "Running", 64),
            ("Diff scan: docs/", "Queued", 0),
        ]
        for name, status, value in tasks:
            self.append_task(name, status, value)
        self.setRowHeight(0, 22)
        self.setRowHeight(1, 22)
        self.setRowHeight(2, 22)

    def append_task(self, name: str, status: str, value: int) -> int:
        """Insert a task row; returns its row index (status/progress updateable)."""
        row = self.rowCount()
        self.insertRow(row)
        self.setItem(row, 0, QTableWidgetItem(name))
        status_color = {"Done": "green", "Running": "amber", "Queued": "amber"}.get(status, "green")
        status_item = QTableWidgetItem(status)
        status_item.setForeground(QColor(current_colors()[status_color]))
        self.setItem(row, 1, status_item)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(value)
        bar.setTextVisible(True)
        bar.setFixedHeight(14)
        self.setCellWidget(row, 2, bar)
        self.setRowHeight(row, 22)
        return row


class OutputDock(QWidget):
    def __init__(self, icons) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.terminal = _TerminalPane()
        self.command_log = _CommandLogPane(icons)
        self.tasks = _TasksPane()
        self.tabs.addTab(self.terminal, "Terminal")
        self.tabs.addTab(self.command_log, "Command Log")
        self.tabs.addTab(self.tasks, "Tasks")

    # -- task lifecycle (driven by the Run/Stop toolbar actions) ---------------

    def append_terminal(self, text: str) -> None:
        """Stream one output line into the Terminal pane."""
        self.terminal.append_line(text)

    def begin_task(self, title: str) -> int:
        """Add a running task row; returns its row index."""
        return self.tasks.append_task(title, "Running", 0)

    def set_task_progress(self, row: int, value: int) -> None:
        bar = self.tasks.cellWidget(row, 2)
        if isinstance(bar, QProgressBar):
            bar.setValue(int(value))

    def finish_task(self, row: int, status: str = "Done") -> None:
        """Mark a task row complete/cancelled and fill its progress bar."""
        item = self.tasks.item(row, 1)
        if item is not None:
            item.setText(status)
            color = current_colors()["green"] if status == "Done" else current_colors()["amber"]
            item.setForeground(QColor(color))
        bar = self.tasks.cellWidget(row, 2)
        if isinstance(bar, QProgressBar):
            bar.setValue(100 if status == "Done" else bar.value())
