"""StatusBar — global app status: message, busy progress, and status pills."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QProgressBar, QStatusBar, QWidget


def _pill(text: str, state: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("statusPill")
    if state:
        label.setProperty("state", state)
    return label


class StatusBar(QStatusBar):
    def __init__(self, icons, version: str) -> None:
        super().__init__()
        self.setSizeGripEnabled(False)
        self._icons = icons

        self._message = QLabel("Ready")
        self._message.setObjectName("muted")
        self.addWidget(self._message, 1)

        self._progress = QProgressBar(self)
        self._progress.setFixedWidth(150)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setVisible(False)
        self.addPermanentWidget(self._progress)

        self._branch = _pill("main", "ok")
        self._connection = _pill("SSH · offline", "err")
        self._version = _pill(version)

        for widget in (self._branch, self._connection, self._version):
            self.addPermanentWidget(widget)

    # -- public ---------------------------------------------------------------

    def set_message(self, text: str) -> None:
        self._message.setText(text)

    def set_branch(self, branch: str, state: str = "ok") -> None:
        self._branch.setText(branch)
        self._branch.setProperty("state", state)

    def set_connection(self, text: str, state: str = "ok") -> None:
        self._connection.setText(text)
        self._connection.setProperty("state", state)

    def set_busy(self, active: bool, message: str | None = None) -> None:
        self._progress.setVisible(active)
        if message:
            self.set_message(message)
