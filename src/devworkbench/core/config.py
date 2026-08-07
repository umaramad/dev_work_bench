"""Configuration loader — TOML (or JSON) files merged over built-in defaults.

Uses the stdlib ``tomllib`` (Python 3.11+) or the ``tomli`` backport on
Python 3.9/3.10, so no heavy TOML dependency is needed. Missing files fall
back to defaults; dotted keys address nested values, e.g.
``get("appearance.font_size")``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:  # Python 3.11+ ships tomllib in the stdlib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.9/3.10 use the tomli backport
    import tomli as tomllib  # type: ignore[no-redef, import-not-found]


class ConfigError(Exception):
    """Raised when a configuration file cannot be parsed."""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class ConfigLoader:
    """Loads and merges application configuration."""

    def __init__(self, defaults: dict[str, Any] | None = None) -> None:
        self._defaults: dict[str, Any] = defaults or {}
        self._data: dict[str, Any] = {}

    @property
    def defaults(self) -> dict[str, Any]:
        return self._defaults

    # -- loading -----------------------------------------------------------------------

    def load(self, path: str | Path | None = None) -> dict[str, Any]:
        """Load ``path`` (TOML preferred, JSON accepted) merged over defaults.

        A missing file is not an error — defaults are returned.
        """
        data = dict(self._defaults)
        if path is not None:
            config_path = Path(path)
            if config_path.exists():
                data = _deep_merge(data, self._read(config_path))
        self._data = data
        return data

    def reload(self, path: str | Path) -> dict[str, Any]:
        """Re-read an existing config file (used by settings → reload)."""
        return self.load(path)

    def load_from_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Merge an in-memory dict over the defaults (useful for tests and
        module-provided config)."""
        self._data = _deep_merge(self._defaults, data)
        return self._data

    # -- access ---------------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return ``key`` (dots traverse nested dicts) or ``default``."""
        node: Any = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def all(self) -> dict[str, Any]:
        return dict(self._data)

    def flatten(self) -> dict[str, Any]:
        """Flatten nested config into dotted keys, e.g. ``{"appearance": {"theme": "dark"}}``
        becomes ``{"appearance.theme": "dark"}`` (used to seed typed defaults)."""
        source = self._data if self._data else self._defaults
        return _flatten(source)

    # -- persistence -----------------------------------------------------------------------

    def save(self, path: str | Path, data: dict[str, Any] | None = None) -> None:
        """Write configuration as TOML (falls back to JSON if unavailable)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = data if data is not None else self._data
        target.write_text(_dict_to_toml(payload), encoding="utf-8")
        self._data = payload

    # -- internals ----------------------------------------------------------------------------

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Recursively flatten nested dicts into dotted keys."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten(value, dotted))
        else:
            result[dotted] = value
    return result


def _dict_to_toml(data: dict[str, Any], indent: int = 0) -> str:
    """Minimal TOML serializer for flat-ish configs (no tables-of-tables)."""
    lines: list[str] = []
    pad = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}[{key}]")
            lines.append(_dict_to_toml(value, indent + 2))
        elif isinstance(value, bool):
            lines.append(f"{pad}{key} = {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{pad}{key} = {value}")
        elif isinstance(value, (list, tuple)):
            rendered = ", ".join(_toml_scalar(v) for v in value)
            lines.append(f"{pad}{key} = [{rendered}]")
        else:
            lines.append(f"{pad}{key} = {_toml_scalar(value)}")
    return "\n".join(lines)


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
