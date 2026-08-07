"""The application object — QApplication subclass with app-wide defaults.

UI-scaffold scope: high-DPI policy, Fusion base style, metadata, and a
top-level exception guard. Single-instance guard and crash handling arrive
with the core framework (docs/architecture/).
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from devworkbench import APP_ID, APP_NAME, APP_ORG, __version__


class DevWorkbenchApplication(QApplication):
    """QApplication configured for DevWorkbench.

    The high-DPI rounding policy must be set *before* the QGuiApplication
    instance exists, so it happens at the top of ``__init__``.
    """

    def __init__(self, argv: list[str] | None = None) -> None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        super().__init__(list(argv) if argv is not None else sys.argv)

        self.setApplicationName(APP_NAME)
        self.setApplicationDisplayName(APP_NAME)
        self.setOrganizationName(APP_ORG)
        self.setApplicationVersion(__version__)
        self.setOrganizationDomain(APP_ID)
        # Fusion gives our QSS themes deterministic, cross-platform behavior
        # (the native macOS style ignores most stylesheet properties).
        self.setStyle("Fusion")
        self.setQuitOnLastWindowClosed(True)
