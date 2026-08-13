"""Composition root — wires the core framework, services and UI shell together.

Startup order: application → paths → logging → DI container → config →
settings → SQLite (connect + migrate) → Keychain → configuration service →
theme → icon set → main window. This is the *only* place concrete classes are
wired; modules reach services through the container via ``ModuleContext``.
"""

from __future__ import annotations

import logging
import sys

from devworkbench import APP_NAME, __version__
from devworkbench.app import DevWorkbenchApplication
from devworkbench.core.config import ConfigLoader
from devworkbench.core.container import DependencyContainer
from devworkbench.core.crash import (
    install_excepthook,
    install_faulthandler,
    install_qt_message_handler,
)
from devworkbench.core.events import EventBus
from devworkbench.core.logging import setup_logging
from devworkbench.core.paths import Paths
from devworkbench.core.settings import SettingsManager
from devworkbench.database.connection import ConnectionManager
from devworkbench.database.migrations import Migrator
from devworkbench.database.repositories.favorite_repository import FavoriteRepository
from devworkbench.database.repositories.history_repository import HistoryRepository
from devworkbench.database.repositories.plugin_repository import PluginRepository
from devworkbench.database.repositories.project_repository import ProjectRepository
from devworkbench.database.repositories.repository_repository import RepositoriesRepository
from devworkbench.database.repositories.settings_repository import SettingsRepository
from devworkbench.database.repositories.ssh_repository import SshServerRepository
from devworkbench.modules import MODULES
from devworkbench.modules.base import ModuleContext
from devworkbench.services.ai import AIProviderFactory
from devworkbench.services.compare.engine import CompareEngine
from devworkbench.services.configuration_service import ConfigurationService
from devworkbench.services.keychain_service import KeychainService
from devworkbench.ui.icons import IconProvider
from devworkbench.ui.main_window import MainWindow
from devworkbench.ui.theme import THEMES, ThemeManager

logger = logging.getLogger("devworkbench.bootstrap")

DEFAULT_CONFIG: dict = {
    "appearance": {"theme": "dark", "font_size": 13},
    "startup": {"restore_workspace": True, "module": "compare"},
    "updates": {"check": True, "channel": "stable"},
    "database": {"path": None},  # None -> default location from Paths
}


def build_container(paths: Paths) -> DependencyContainer:
    """Composition-root wiring for the core framework."""
    container = DependencyContainer()
    container.register_singleton("core.paths", paths)
    container.register_singleton("core.events", EventBus())
    container.register_singleton("core.settings", SettingsManager())
    container.register_singleton("core.config", ConfigLoader(DEFAULT_CONFIG))
    container.register_singleton("database.connection", ConnectionManager(paths.database_path))
    return container


def main(argv: list[str] | None = None) -> int:
    app = DevWorkbenchApplication(argv)

    # --- infrastructure -----------------------------------------------------
    paths = Paths()
    paths.ensure()
    setup_logging(log_dir=paths.log_dir)

    # --- crash handling (after logging: crash files land in the log dir) ----
    install_excepthook(paths.log_dir)
    install_faulthandler(paths.log_dir)
    install_qt_message_handler()

    container = build_container(paths)
    config: ConfigLoader = container.resolve("core.config")
    config.load(paths.config_path)
    settings: SettingsManager = container.resolve("core.settings")
    # Flattened file config seeds the typed setting defaults.
    settings.set_defaults(config.flatten())

    # --- persistence -----------------------------------------------------------
    database: ConnectionManager = container.resolve("database.connection")
    database.connect()
    Migrator(database).apply()

    # --- repositories (the only SQLite access) -----------------------------------
    container.register_singleton("database.repositories.settings", SettingsRepository(database))
    container.register_singleton("database.repositories.history", HistoryRepository(database))
    container.register_singleton("database.repositories.plugins", PluginRepository(database))
    container.register_singleton("database.repositories.projects", ProjectRepository(database))
    container.register_singleton("database.repositories.repositories", RepositoriesRepository(database))
    container.register_singleton("database.repositories.ssh", SshServerRepository(database))
    container.register_singleton("database.repositories.favorites", FavoriteRepository(database))

    keychain = KeychainService(service=APP_NAME, fallback_path=paths.app_support / "secrets.json")
    events: EventBus = container.resolve("core.events")
    config_service = ConfigurationService(
        settings=settings,
        repository=container.resolve("database.repositories.settings"),
        keychain=keychain,
        events=events,
        service_name=APP_NAME,
    )
    container.register_singleton("services.keychain", keychain)
    container.register_singleton("services.configuration", config_service)
    container.register_singleton("services.ai.factory", AIProviderFactory(config_service))
    container.register_singleton("services.compare.engine", CompareEngine())
    config_service.load()

    # Validate the configured theme — a stray value must never crash startup.
    requested_theme = str(config_service.get("appearance.theme") or "dark")
    if requested_theme == "system":
        requested_theme = "dark"  # resolved to dark/light by the UI layer
    if requested_theme not in THEMES:
        logger.warning("unknown theme %r, falling back to 'dark'", requested_theme)
        requested_theme = "dark"
    theme = ThemeManager.install(app, name=requested_theme)
    logger.info("%s %s starting (config=%s)", APP_NAME, __version__, paths.config_path)

    # --- UI ------------------------------------------------------------------
    icons = IconProvider()
    ctx = ModuleContext(container)
    window = MainWindow(modules=MODULES, icons=icons, theme_manager=theme, ctx=ctx)
    # Window + Dock icon: prefer the packaged .icns so a dev run matches the
    # bundled .app; fall back to the programmatic logo glyph when the asset
    # is unavailable (e.g. a stripped checkout).
    app_icon = paths.resources_dir / "icons" / "DevWorkbench.icns"
    if app_icon.exists():
        from PySide6.QtGui import QIcon

        application_icon = QIcon(str(app_icon))
        app.setWindowIcon(application_icon)
        window.setWindowIcon(application_icon)
    else:
        window.setWindowIcon(icons.get("app", 32))
    window.show()
    app.aboutToQuit.connect(database.close)
    code = app.exec()
    logger.info("%s exited with code %d", APP_NAME, code)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
