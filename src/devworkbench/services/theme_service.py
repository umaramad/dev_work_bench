"""ThemeService — module-safe façade over the UI theme manager.

Modules never import ``ui`` directly; they ask this service for colors,
fonts, and the active scheme. (This is the one service that deliberately
bridges into ``ui`` — that is its entire job.)
"""

from __future__ import annotations

from typing import Any

from devworkbench.ui.theme import ThemeManager, current_colors


class ThemeService:
    """Read-only view of the current theme for non-UI code."""

    def __init__(self, manager: ThemeManager | None = None) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        if self._manager is not None:
            return self._manager.name
        return "dark"

    def colors(self) -> dict[str, str]:
        return dict(current_colors())

    def color(self, token: str, default: str = "#000000") -> str:
        return current_colors().get(token, default)

    def is_dark(self) -> bool:
        return self.name == "dark"

    # -- future (wired when the manager is injected) -------------------------------

    def apply(self, name: str) -> str:
        """Switch the theme; returns the new name."""
        if self._manager is not None:
            return self._manager.apply(name)
        return self.name

    def toggle(self) -> str:
        if self._manager is not None:
            return self._manager.toggle()
        return self.name
