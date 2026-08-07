"""ExtensionRegistry — collects contributions from enabled plugins.

The UI queries this registry by extension point (``module``, ``command``,
``settings_page``, …); the registry never holds references to plugin
internals beyond the contributed values, so disabling a plugin is a clean
drop.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class ExtensionRegistry:
    """Contribution store keyed by extension point and plugin id."""

    def __init__(self) -> None:
        # extension_point -> {plugin_id -> [contributions]}
        self._contributions: dict[str, dict[str, list[Any]]] = defaultdict(dict)

    # -- registration -------------------------------------------------------------------

    def register(self, extension_point: str, plugin_id: str, *values: Any) -> None:
        self._contributions[extension_point].setdefault(plugin_id, []).extend(values)

    def unregister(self, extension_point: str, plugin_id: str) -> None:
        self._contributions[extension_point].pop(plugin_id, None)

    def clear_for(self, plugin_id: str) -> None:
        """Drop every contribution from ``plugin_id`` across all extension points."""
        for point in self._contributions:
            self._contributions[point].pop(plugin_id, None)

    # -- queries ----------------------------------------------------------------------------

    def contributions(self, extension_point: str) -> list[Any]:
        """Flattened contributions for an extension point, in plugin order."""
        flattened: list[Any] = []
        for values in self._contributions.get(extension_point, {}).values():
            flattened.extend(values)
        return flattened

    def by_plugin(self, extension_point: str) -> dict[str, list[Any]]:
        return dict(self._contributions.get(extension_point, {}))

    def extension_points(self) -> list[str]:
        return list(self._contributions)
