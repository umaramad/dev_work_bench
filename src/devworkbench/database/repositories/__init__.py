"""Repositories — the only code that touches SQLite.

Shared app-level repositories live here; module-owned tables use
``BaseRepository`` / ``CrudRepository`` from their own modules.
"""

from devworkbench.database.orm import CrudRepository, PersistentModel
from devworkbench.database.repositories.base import BaseRepository
from devworkbench.database.repositories.favorite_repository import FavoriteRepository
from devworkbench.database.repositories.history_repository import HistoryRepository
from devworkbench.database.repositories.plugin_repository import PluginRepository
from devworkbench.database.repositories.project_repository import ProjectRepository
from devworkbench.database.repositories.repository_repository import RepositoriesRepository
from devworkbench.database.repositories.settings_repository import SettingsRepository
from devworkbench.database.repositories.ssh_repository import SshServerRepository

__all__ = [
    "BaseRepository",
    "CrudRepository",
    "FavoriteRepository",
    "HistoryRepository",
    "PersistentModel",
    "PluginRepository",
    "ProjectRepository",
    "RepositoriesRepository",
    "SettingsRepository",
    "SshServerRepository",
]
