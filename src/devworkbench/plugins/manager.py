"""PluginManager — the lifecycle state machine for plugins.

States: DISCOVERED → LOADED → ENABLED ⇄ DISABLED → UNLOADED → (disposed).
The manager is the only authority over transitions; it persists
enabled/disabled state through a repository when provided.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path

from devworkbench.plugins.loader import PluginLoader, PluginRecord

logger = logging.getLogger("devworkbench.plugins.manager")


class PluginState(Enum):
    DISCOVERED = auto()
    LOADED = auto()
    ENABLED = auto()
    DISABLED = auto()
    UNLOADED = auto()
    REJECTED = auto()


StateListener = Callable[[str, PluginState], None]


class PluginManager:
    """Owns plugin lifecycle, dependency order, and state persistence."""

    def __init__(
        self,
        loader: PluginLoader,
        repository=None,
        api_version: str = "1",
    ) -> None:
        self._loader = loader
        self._repository = repository
        self._api_version = api_version
        self._records: dict[str, PluginRecord] = {}
        self._states: dict[str, PluginState] = {}
        self._listeners: list[StateListener] = []

    # -- discovery --------------------------------------------------------------------

    def discover_and_load(self) -> list[PluginRecord]:
        """Scan + import every plugin; returns the loaded records."""
        loaded: list[PluginRecord] = []
        for manifest, directory in self._loader.discover():
            try:
                record = self._loader.load(manifest, directory=directory)
            except Exception as exc:  # noqa: BLE001
                logger.error("rejecting plugin %s: %s", manifest.id, exc)
                self._states[manifest.id] = PluginState.REJECTED
                continue
            self._records[manifest.id] = record
            self._states[manifest.id] = PluginState.LOADED
            loaded.append(record)
        return loaded

    # -- lifecycle ----------------------------------------------------------------------

    def enable(self, plugin_id: str) -> bool:
        """Enable a loaded plugin (respecting declared dependencies).

        The plugin host (PluginHost + on_enable/create_view wiring) lands with
        the next milestone; the manager only owns state transitions for now.
        """
        record = self._records.get(plugin_id)
        if record is None:
            return False
        for dependency in record.manifest.depends:
            if self.state_of(dependency) != PluginState.ENABLED:
                logger.warning("%s depends on %s which is not enabled", plugin_id, dependency)
                return False
        self._set_state(plugin_id, PluginState.ENABLED)
        if self._repository is not None:
            self._repository.set_enabled(plugin_id, True)
        return True

    def disable(self, plugin_id: str) -> bool:
        if self.state_of(plugin_id) != PluginState.ENABLED:
            return False
        self._set_state(plugin_id, PluginState.DISABLED)
        if self._repository is not None:
            self._repository.set_enabled(plugin_id, False)
        return True

    def unload(self, plugin_id: str) -> None:
        """Release the plugin's module reference."""
        record = self._records.pop(plugin_id, None)
        if record is not None:
            record.module = None
            record.entry_point = None
        self._set_state(plugin_id, PluginState.UNLOADED)

    def dispose_all(self) -> None:
        for plugin_id in list(self._records):
            self.unload(plugin_id)

    # -- queries ----------------------------------------------------------------------------

    def state_of(self, plugin_id: str) -> PluginState:
        return self._states.get(plugin_id, PluginState.DISCOVERED)

    def enabled_plugins(self) -> list[PluginRecord]:
        return [r for pid, r in self._records.items() if self.state_of(pid) == PluginState.ENABLED]

    def records(self) -> list[PluginRecord]:
        return list(self._records.values())

    def subscribe(self, listener: StateListener) -> StateListener:
        self._listeners.append(listener)
        return listener

    # -- internals -------------------------------------------------------------------------------

    def _set_state(self, plugin_id: str, state: PluginState) -> None:
        self._states[plugin_id] = state
        for listener in list(self._listeners):
            try:
                listener(plugin_id, state)
            except Exception:  # noqa: BLE001
                logger.exception("state listener failed for %s", plugin_id)
        if self._repository is not None:
            self._repository.set_state(plugin_id, state.name.lower())
