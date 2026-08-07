"""PluginHost — the per-plugin context object.

A plugin only ever talks to the core through its ``PluginHost``: DI child
scope, event-bus façade, paths, logger, and scheduling helpers. This is the
isolation boundary of the plugin system.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from devworkbench.core.container import DependencyContainer
from devworkbench.core.events import EventBus
from devworkbench.core.paths import Paths


class PluginHost:
    """Context handed to a plugin's ``on_enable`` / ``create_view``."""

    def __init__(
        self,
        plugin_id: str,
        scope: DependencyContainer,
        event_bus: EventBus,
        paths,
        logger: logging.Logger | None = None,
    ) -> None:
        self._plugin_id = plugin_id
        self._scope = scope
        self._event_bus = event_bus
        self._paths = paths
        self._logger = logger or logging.getLogger(f"devworkbench.plugins.{plugin_id}")

    # -- identity --------------------------------------------------------------------------

    @property
    def id(self) -> str:
        return self._plugin_id

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    # -- dependencies -------------------------------------------------------------------------

    @property
    def scope(self) -> DependencyContainer:
        """The plugin's private DI child scope."""
        return self._scope

    def resolve(self, key: str) -> Any:
        return self._scope.resolve(key)

    # -- integration --------------------------------------------------------------------------

    @property
    def paths(self) -> Paths:
        return self._paths

    @property
    def plugins_dir(self) -> Path:
        return self._paths.plugins_dir

    def publish(self, topic: str, **payload) -> None:
        """Publish an event to the app-wide bus."""
        self._event_bus.publish(topic, **payload)

    def call_later(self, fn: Callable[[], None]) -> None:
        """Schedule ``fn`` on the UI thread (no-op until wired to the loop)."""
        self._logger.debug("call_later requested for %s", self._plugin_id)
        fn()

    def __repr__(self) -> str:
        return f"<PluginHost {self._plugin_id}>"
