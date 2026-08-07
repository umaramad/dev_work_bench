"""BaseRepository — shared row-mapping helpers for SQLite repositories."""

from __future__ import annotations

import sqlite3
from typing import Any

from devworkbench.database.connection import ConnectionManager


class BaseRepository:
    """Provides typed query helpers over a ``ConnectionManager``."""

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._manager = connection_manager

    # -- query helpers -----------------------------------------------------------------

    def _fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        # Reads go through read(), not write(): no transaction, no writer lock.
        with self._manager.read() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._manager.read() as connection:
            row = connection.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        def _run(connection: sqlite3.Connection) -> int:
            cursor = connection.execute(sql, params)
            return cursor.rowcount

        return self._manager.write(_run)

    def _execute_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        def _run(connection: sqlite3.Connection) -> None:
            connection.executemany(sql, rows)

        self._manager.write(_run)
