"""DevWorkbench — Flet shell entry point.

Run the Flet UI (the migration target; the PySide6 app stays available
via ``python -m devworkbench``):

    python main.py

Requires the optional Flet dependency::

    pip install -e ".[flet]"

Startup mirrors the PySide6 bootstrap where it matters for data flows:
paths, SQLite (connect + migrate), configuration, keychain and the
repositories are wired before the shell is built so Flet screens share
the same persistence as the desktop app. If the database cannot be
opened, the shell still starts and screens fall back to demo / degraded
modes.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# src-layout bootstrap: make ``devworkbench`` importable when running the
# script straight from a checkout (mirrors pytest's pythonpath=src).
# When frozen (flet pack / PyInstaller) the package is already bundled — skip.
if not getattr(sys, "frozen", False):
    _SRC = Path(__file__).resolve().parent / "src"
    if _SRC.is_dir() and str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))

import flet as ft  # noqa: E402

from devworkbench import APP_NAME  # noqa: E402
from devworkbench.core.events import EventBus  # noqa: E402
from devworkbench.core.paths import Paths  # noqa: E402
from devworkbench.core.settings import SettingsManager  # noqa: E402
from devworkbench.database.connection import ConnectionManager  # noqa: E402
from devworkbench.database.migrations import Migrator  # noqa: E402
from devworkbench.database.repositories.favorite_repository import FavoriteRepository  # noqa: E402
from devworkbench.database.repositories.history_repository import HistoryRepository  # noqa: E402
from devworkbench.database.repositories.settings_repository import SettingsRepository  # noqa: E402
from devworkbench.flet_ui import AppShell  # noqa: E402
from devworkbench.services.compare_service import CompareService  # noqa: E402
from devworkbench.services.configuration_service import ConfigurationService  # noqa: E402
from devworkbench.services.git import GitService  # noqa: E402
from devworkbench.services.keychain_service import KeychainService  # noqa: E402

logger = logging.getLogger("devworkbench.flet_main")


def _configure_logging(log_dir: Path) -> None:
    """Write Flet UI diagnostics under the app log directory."""
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("devworkbench")
    root.setLevel(logging.DEBUG)
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        handler = logging.FileHandler(log_dir / "flet.log", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
        logging.getLogger("devworkbench.flet_ui").setLevel(logging.DEBUG)


def build_backend() -> dict:
    """Wire the shared data layer used by Flet screens."""
    backend: dict = {
        "git": GitService(),
        "compare": CompareService(),
        "events": EventBus(),
    }
    try:
        paths = Paths()
        paths.ensure()
        _configure_logging(paths.log_dir)
        backend["paths"] = paths

        database = ConnectionManager(paths.database_path)
        database.connect()
        Migrator(database).apply()
        backend["database"] = database
        backend["favorites"] = FavoriteRepository(database)
        backend["history"] = HistoryRepository(database)
        settings_repo = SettingsRepository(database)

        keychain = KeychainService(
            service=APP_NAME,
            fallback_path=paths.app_support / "secrets.json",
        )
        backend["keychain"] = keychain

        config = ConfigurationService(
            settings=SettingsManager(),
            repository=settings_repo,
            keychain=keychain,
            events=backend["events"],
            service_name=APP_NAME,
        )
        config.load()
        backend["config"] = config

        executable = str(config.get("git.executable") or "git")
        backend["git"] = GitService(executable=executable)
    except Exception:  # noqa: BLE001 — the shell must start even without a DB
        logger.exception("failed to open the database — screens will use demo data")
    return backend


def main(page: ft.Page) -> None:
    """Compose the page through the base layout controller."""
    AppShell(page, backend=build_backend()).mount()


if __name__ == "__main__":
    ft.run(main)
