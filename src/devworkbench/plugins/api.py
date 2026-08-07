"""Public plugin API — the contract every module/plugin implements.

Qt is referenced only under ``TYPE_CHECKING`` so this module stays
importable in headless tests. ``api_version`` gates compatibility.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from devworkbench.plugins.host import PluginHost
    from devworkbench.plugins.manifest import PluginManifest

API_VERSION = "1"


class ModulePlugin(ABC):
    """A plugin that contributes a sidebar module with a view."""

    @abstractmethod
    def manifest(self) -> "PluginManifest":
        """Return the plugin's metadata."""

    def create_view(self, ctx: "PluginHost") -> "QWidget | None":
        """Build the module's main widget (None for non-view plugins)."""
        return None

    def on_enable(self, ctx: "PluginHost") -> None:
        """Called when the plugin is enabled (after contributions collect)."""

    def on_disable(self) -> None:
        """Called when the plugin is disabled."""

    def on_unload(self) -> None:
        """Called before the plugin's module is dropped from memory."""


# ---------------------------------------------------------------------------
# Contributor protocols — small, single-purpose extension points (ISP).
# A plugin implements only the protocols it actually uses.
# ---------------------------------------------------------------------------


class CommandContributor(ABC):
    """Contributes command-palette entries."""

    @abstractmethod
    def commands(self) -> list[tuple[str, str, Callable[[], None]]]:
        """Return ``(label, icon_key, callback)`` triples."""


class SettingsContributor(ABC):
    """Contributes a preferences page."""

    @abstractmethod
    def settings_page(self) -> "QWidget":
        """Return a widget shown inside the Preferences dialog."""


class ToolbarContributor(ABC):
    """Contributes actions to a module toolbar."""

    def toolbar_actions(self) -> list[Any]:
        return []


class ContextMenuContributor(ABC):
    """Contributes context-menu items."""

    def context_menu_items(self) -> list[Any]:
        return []


class ThemeContributor(ABC):
    """Contributes theme tokens or color schemes."""

    def theme_tokens(self) -> dict[str, str]:
        return {}


class StatusBarContributor(ABC):
    """Contributes a widget to the status bar."""

    def status_widget(self) -> "QWidget | None":
        return None


class EventSubscriber(ABC):
    """Subscribes to typed event-bus topics on enable."""

    def topics(self) -> dict[str, Callable[..., None]]:
        """Map ``topic`` -> handler; subscriptions are auto-dropped on disable."""
        return {}
