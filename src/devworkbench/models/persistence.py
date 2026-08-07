"""Persistence models — one ``PersistentModel`` per SQLite table.

These mirror the domain models in this package but are bound to a schema
row: each declares ``table`` / ``columns`` / ``json_columns`` so the generic
``CrudRepository`` (database/orm.py) can persist them without per-table SQL.

Table map:

    app_settings   -> SettingsEntry      (Settings)
    projects       -> Project            (Projects)
    recent_files   -> RecentFile         (RecentFiles)
    recent_folders -> RecentFolder       (RecentFolders)
    repositories   -> RepositoryRecord   (Repositories)
    ssh_servers    -> SshServerRecord    (SSHServers)
    command_history-> HistoryEntry       (History)
    favorites      -> Favorite           (Favorites)
    plugins        -> PluginState        (Plugins)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from devworkbench.database.orm import PersistentModel


@dataclass
class SettingsEntry(PersistentModel):
    """One key/value pair in ``app_settings``."""

    table = "app_settings"
    columns = ("key", "value", "type", "updated_at")
    primary_key = "key"
    auto_key = False

    key: str = ""
    value: str = ""
    type: str = "str"
    updated_at: str = ""


@dataclass
class Project(PersistentModel):
    """A workspace project in ``projects``."""

    table = "projects"
    columns = ("id", "name", "path", "description", "tags", "last_opened_at", "created_at", "updated_at")
    json_columns = frozenset({"tags"})

    id: int = 0
    name: str = ""
    path: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    last_opened_at: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class RecentFile(PersistentModel):
    """One entry in ``recent_files`` (a path opened in a module)."""

    table = "recent_files"
    columns = ("id", "module", "path", "opened_at")

    id: int = 0
    module: str = ""
    path: str = ""
    opened_at: str = ""


@dataclass
class RecentFolder(PersistentModel):
    """One entry in ``recent_folders`` (a directory opened in a module)."""

    table = "recent_folders"
    columns = ("id", "path", "opened_at")

    id: int = 0
    path: str = ""
    opened_at: str = ""


@dataclass
class RepositoryRecord(PersistentModel):
    """A version-controlled working copy in ``repositories``."""

    table = "repositories"
    columns = ("id", "name", "path", "remote_url", "default_branch", "last_opened_at", "created_at")

    id: int = 0
    name: str = ""
    path: str = ""
    remote_url: str = ""
    default_branch: str = "main"
    last_opened_at: str = ""
    created_at: str = ""


@dataclass
class SshServerRecord(PersistentModel):
    """An SSH connection profile in ``ssh_servers``.

    The private-key passphrase is deliberately *not* a column — it lives in
    the macOS Keychain (see ``KeychainService``).
    """

    table = "ssh_servers"
    columns = ("id", "name", "host", "user", "port", "key_path", "auth_method", "created_at", "updated_at")

    id: int = 0
    name: str = ""
    host: str = ""
    user: str = "dev"
    port: int = 22
    key_path: str = ""
    auth_method: str = "key"  # key | password | agent
    created_at: str = ""
    updated_at: str = ""


@dataclass
class HistoryEntry(PersistentModel):
    """One command in ``command_history`` (command palette / terminal)."""

    table = "command_history"
    columns = ("id", "command", "module", "executed_at")

    id: int = 0
    command: str = ""
    module: str = ""
    executed_at: str = ""


@dataclass
class Favorite(PersistentModel):
    """A starred item in ``favorites`` (files, folders, projects, …).

    ``group_name`` groups folder favorites on the Git home page (empty =
    ungrouped); it is free-form text, so creating a group is just typing it.
    """

    table = "favorites"
    columns = ("id", "kind", "ref", "label", "group_name", "created_at")

    id: int = 0
    kind: str = "file"  # file | folder | project | repository | ssh
    ref: str = ""       # path for file/folder; primary-key text for the rest
    label: str = ""
    group_name: str = ""
    created_at: str = ""


@dataclass
class PluginState(PersistentModel):
    """Installed-plugin row in ``plugins`` (manager UI state)."""

    table = "plugins"
    columns = (
        "id",
        "name",
        "version",
        "api_version",
        "source",
        "enabled",
        "state",
        "config_json",
        "installed_at",
    )
    auto_key = False

    id: str = ""
    name: str = ""
    version: str = ""
    api_version: str = "1"
    source: str = "local"  # builtin | local | git
    enabled: bool = True
    state: str = "discovered"
    config_json: str = "{}"
    installed_at: str = ""
