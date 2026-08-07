"""SettingsRepository — persistence for ``app_settings``."""

from __future__ import annotations

from typing import Any

from devworkbench.database.repositories.base import BaseRepository

def _cast_bool(value: str) -> bool:
    """Parse a stored boolean.

    ``bool("0")`` would be True (any non-empty string is), so booleans need
    an explicit parse — this mirrors ConfigurationService._coerce, keeping
    stored ``0``/``1`` values readable as False/True.
    """

    return str(value).strip().lower() in ("1", "true", "yes", "on")


_TYPE_CASTS: dict[str, Any] = {"str": str, "int": int, "float": float, "bool": _cast_bool}


class SettingsRepository(BaseRepository):
    """Key/value settings backed by the ``app_settings`` table."""

    def get(self, key: str, default: Any = None) -> Any:
        row = self._fetch_one("SELECT value, type FROM app_settings WHERE key = ?", (key,))
        if row is None:
            return default
        cast = _TYPE_CASTS.get(row["type"], str)
        try:
            return cast(row["value"])
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value: Any) -> None:
        if isinstance(value, bool):
            type_name = "bool"
            encoded = "1" if value else "0"  # a bare bool() would store "1" for False
        else:
            type_name = type(value).__name__
            encoded = str(value)
        self._execute(
            "INSERT INTO app_settings (key, value, type) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "type = excluded.type, updated_at = datetime('now')",
            (key, encoded, type_name),
        )

    def all(self) -> dict[str, Any]:
        return {row["key"]: row["value"] for row in self._fetch_all("SELECT key, value FROM app_settings")}

    def delete(self, key: str) -> bool:
        return self._execute("DELETE FROM app_settings WHERE key = ?", (key,)) > 0
