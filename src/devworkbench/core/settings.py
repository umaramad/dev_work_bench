"""Settings manager — typed in-memory settings with defaults and listeners.

Persistence is delegated to a repository (``database.repositories``) once the
SQLite layer is wired; the manager itself stays storage-agnostic.
Keys are namespaced, e.g. ``plugins.compare.engine``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("devworkbench.core.settings")

Listener = Callable[[str, Any], None]


class SettingsManager:
    def __init__(self, defaults: dict[str, Any] | None = None) -> None:
        self._values: dict[str, Any] = {}
        self._defaults: dict[str, Any] = defaults or {}
        self._listeners: list[Listener] = []

    # -- defaults -----------------------------------------------------------------

    def set_defaults(self, defaults: dict[str, Any]) -> None:
        """Merge ``defaults`` without overwriting existing values.

        The first seed wins — bootstrap seeds file-config defaults before the
        schema defaults, so a ``config.toml`` value is never clobbered.
        """
        for key, value in defaults.items():
            if key not in self._defaults:
                self._defaults[key] = value

    # -- read / write ----------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for ``key`` (explicit value, then default)."""
        if key in self._values:
            return self._values[key]
        if key in self._defaults:
            return self._defaults[key]
        return default

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` and notify listeners on change."""
        previous = self._values.get(key, self._defaults.get(key))
        if previous == value:
            return
        self._values[key] = value
        for listener in list(self._listeners):
            try:
                listener(key, value)
            except Exception:  # noqa: BLE001 — listeners must not break writes
                logger.exception("listener failed for %r", key)

    def reset(self, key: str) -> None:
        """Drop the explicit value, falling back to the default."""
        self._values.pop(key, None)

    # -- bulk / observers ---------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Merge of defaults and explicit values."""
        merged = dict(self._defaults)
        merged.update(self._values)
        return merged

    def subscribe(self, listener: Listener) -> Listener:
        """Register a ``(key, value)`` change listener; returns it for cleanup."""
        self._listeners.append(listener)
        return listener

    def unsubscribe(self, listener: Listener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass
