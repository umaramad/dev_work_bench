"""Path resolution — single source of truth for file locations.

Works in two modes: frozen (PyInstaller bundle) and development (source
tree). On macOS the standard locations are used:

    App Support  ~/Library/Application Support/DevWorkbench
    Logs         ~/Library/Logs/DevWorkbench
    Caches       ~/Library/Caches/DevWorkbench
    Plugins      <App Support>/plugins
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "DevWorkbench"


class Paths:
    def __init__(self, app_name: str = APP_NAME, frozen: bool | None = None) -> None:
        self.app_name = app_name
        self._frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        # src/devworkbench/core/paths.py -> project root
        self._source_root = Path(__file__).resolve().parents[3]
        self._home = Path.home()

    # -- base directories ------------------------------------------------------------

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def app_support(self) -> Path:
        if self._frozen:
            return self._home / "Library" / "Application Support" / self.app_name
        return self._source_root / "data"

    @property
    def cache_dir(self) -> Path:
        if self._frozen:
            return self._home / "Library" / "Caches" / self.app_name
        return self._source_root / "data" / "cache"

    @property
    def log_dir(self) -> Path:
        if self._frozen:
            return self._home / "Library" / "Logs" / self.app_name
        return self._source_root / "data" / "logs"

    @property
    def plugins_dir(self) -> Path:
        return self.app_support / "plugins"

    @property
    def config_path(self) -> Path:
        return self.app_support / "config.toml"

    @property
    def database_path(self) -> Path:
        return self.app_support / "devworkbench.db"

    @property
    def resources_dir(self) -> Path:
        if self._frozen:
            return Path(sys.executable).resolve().parent.parent / "Resources"
        return self._source_root / "resources"

    # -- helpers ----------------------------------------------------------------------

    def ensure(self) -> None:
        """Create every directory that must exist at startup."""
        for directory in (self.app_support, self.cache_dir, self.log_dir, self.plugins_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return (
            f"Paths(app_support={self.app_support}, log_dir={self.log_dir}, "
            f"plugins_dir={self.plugins_dir}, frozen={self._frozen})"
        )
