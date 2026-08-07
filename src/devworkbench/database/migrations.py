"""Migrator — versioned, transactional SQLite schema migrations.

Migrations are a flat list of ``(version, sql)`` tuples applied in order and
recorded in ``schema_version``. Each migration runs in its own transaction,
so a failed migration rolls back cleanly.
"""

from __future__ import annotations

import logging
import sqlite3

from devworkbench.database.connection import ConnectionManager

logger = logging.getLogger("devworkbench.database.migrations")

_MIGRATION_0001_CREATE_CORE = """
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    type       TEXT NOT NULL DEFAULT 'str',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plugins (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    version      TEXT NOT NULL,
    api_version  TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'local',
    enabled      INTEGER NOT NULL DEFAULT 1,
    state        TEXT NOT NULL DEFAULT 'discovered',
    config_json  TEXT NOT NULL DEFAULT '{}',
    installed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recent_files (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    module    TEXT NOT NULL,
    path      TEXT NOT NULL,
    opened_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS command_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    command     TEXT NOT NULL,
    module      TEXT NOT NULL DEFAULT '',
    executed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_MIGRATION_0002_WORKSPACE = """
CREATE TABLE IF NOT EXISTS projects (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    path           TEXT NOT NULL UNIQUE,
    description    TEXT NOT NULL DEFAULT '',
    tags           TEXT NOT NULL DEFAULT '[]',
    last_opened_at TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name);

CREATE TABLE IF NOT EXISTS recent_folders (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    path      TEXT NOT NULL,
    opened_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_recent_folders_opened_at ON recent_folders(opened_at);

CREATE TABLE IF NOT EXISTS repositories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    path           TEXT NOT NULL UNIQUE,
    remote_url     TEXT NOT NULL DEFAULT '',
    default_branch TEXT NOT NULL DEFAULT 'main',
    last_opened_at TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_repositories_name ON repositories(name);

CREATE TABLE IF NOT EXISTS ssh_servers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    host        TEXT NOT NULL,
    user        TEXT NOT NULL DEFAULT 'dev',
    port        INTEGER NOT NULL DEFAULT 22,
    key_path    TEXT NOT NULL DEFAULT '',
    auth_method TEXT NOT NULL DEFAULT 'key',   -- key | password | agent
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS favorites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,                 -- file | folder | project | repository | ssh
    ref        TEXT NOT NULL,                 -- path for file/folder; id for project/repository/ssh
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_favorites_kind ON favorites(kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_favorites_unique ON favorites(kind, ref);
"""

_MIGRATION_0003_FAVORITE_GROUPS = """
ALTER TABLE favorites ADD COLUMN group_name TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_favorites_group ON favorites(kind, group_name);
"""

# version -> SQL. Append new migrations; never edit existing ones.
MIGRATIONS: list[tuple[int, str]] = [
    (1, _MIGRATION_0001_CREATE_CORE),
    (2, _MIGRATION_0002_WORKSPACE),
    (3, _MIGRATION_0003_FAVORITE_GROUPS),
]


class Migrator:
    """Applies pending schema migrations on startup."""

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._manager = connection_manager

    # -- inspection -------------------------------------------------------------------

    def current_version(self) -> int:
        """The highest applied migration version (0 if none)."""
        def _query(connection: sqlite3.Connection) -> int:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        return self._manager.write(_query)

    def pending(self) -> list[tuple[int, str]]:
        current = self.current_version()
        return [(version, sql) for version, sql in MIGRATIONS if version > current]

    # -- application ---------------------------------------------------------------------

    def apply(self) -> list[int]:
        """Apply all pending migrations; returns the applied version numbers."""
        applied: list[int] = []
        current = self.current_version()
        for version, sql in MIGRATIONS:
            if version <= current:
                continue

            def _run(connection: sqlite3.Connection, _version=version, _sql=sql) -> None:
                # Note: executescript implicitly commits any pending transaction
                # before running; on failure the outer `with connection:` block
                # still rolls the partial DDL back (SQLite DDL is transactional).
                connection.executescript(_sql)
                connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (_version,)
                )

            self._manager.write(_run)
            applied.append(version)
            logger.info("applied migration %d", version)
        return applied

    def verify(self) -> bool:
        """True when every known migration is applied (used in tests)."""
        return self.current_version() >= (MIGRATIONS[-1][0] if MIGRATIONS else 0)
