"""PluginRepository — persistence for installed plugin state.

Typed over ``PluginState`` (the ``plugins`` table). ``plugins.id`` is a
caller-supplied text key, so inserts include it (``auto_key = False``).
"""

from __future__ import annotations

import json
from typing import Any

from devworkbench.database.orm import CrudRepository
from devworkbench.models.persistence import PluginState


class PluginRepository(CrudRepository[PluginState]):
    """Installed-plugin records backed by the ``plugins`` table."""

    model = PluginState

    def list_plugins(self) -> list[PluginState]:
        return self.list(order_by="name")

    def upsert(
        self,
        plugin_id: str,
        *,
        name: str,
        version: str,
        api_version: str,
        source: str = "local",
        enabled: bool = True,
        state: str = "discovered",
        config: dict[str, Any] | None = None,
    ) -> None:
        """Insert or update a plugin row (upsert keeps the original key)."""
        self.upsert_model(
            PluginState(
                id=plugin_id,
                name=name,
                version=version,
                api_version=api_version,
                source=source,
                enabled=enabled,
                state=state,
                config_json=json.dumps(config or {}),
            )
        )

    def upsert_model(self, model: PluginState) -> None:
        existing = self.get(model.id)
        if existing is None:
            self.insert(model)
        else:
            self.update(model)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        self._execute(
            "UPDATE plugins SET enabled = ? WHERE id = ?", (int(enabled), plugin_id)
        )

    def set_state(self, plugin_id: str, state: str) -> None:
        self._execute("UPDATE plugins SET state = ? WHERE id = ?", (state, plugin_id))

    def remove(self, plugin_id: str) -> bool:
        return self.delete(plugin_id)
