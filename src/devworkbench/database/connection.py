"""ConnectionManager — SQLite access with WAL and one-writer semantics.

Writes go through ``write`` (transactional, serialized by a lock); reads
through the ``read`` context manager. The connection is opened with the
pragma set that makes local SQLite fast and safe: WAL journal, foreign keys
on, and a busy timeout.

Note (scaffold): reads and writes currently share one connection with
``check_same_thread=False``; SQLite serializes at the C level, so this is
safe but means concurrent reads can observe an in-flight transaction. The
architecture's thread-local reader connections land with the workers
milestone.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator


class DatabaseError(Exception):
    """Raised when the database cannot be opened or written."""


class ConnectionManager:
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._write_lock = RLock()
        self._write_connection: sqlite3.Connection | None = None

    # -- lifecycle -----------------------------------------------------------------

    def connect(self) -> None:
        """Open the write connection and apply pragmas."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=NORMAL")
        self._write_connection = connection

    def close(self) -> None:
        """Flush and close the write connection."""
        if self._write_connection is not None:
            self._write_connection.close()
            self._write_connection = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_connected(self) -> bool:
        return self._write_connection is not None

    # -- access ----------------------------------------------------------------------

    def write(self, fn: Any) -> Any:
        """Run ``fn(connection)`` inside a transaction (serialized across threads)."""
        connection = self._require()
        with self._write_lock:
            try:
                with connection:  # commit / rollback on exit
                    return fn(connection)
            except sqlite3.Error as exc:
                raise DatabaseError(str(exc)) from exc

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Yield the connection for reads (no transaction implied)."""
        yield self._require()

    def backup(self, target: str | Path) -> None:
        """Copy the live database to ``target`` (VACUUM-free online backup)."""
        source = self._require()
        destination = Path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        dest_connection = sqlite3.connect(str(destination))
        try:
            with self._write_lock:
                source.backup(dest_connection)
        finally:
            dest_connection.close()

    # -- internals ----------------------------------------------------------------------

    def _require(self) -> sqlite3.Connection:
        if self._write_connection is None:
            raise DatabaseError("database not connected — call connect() first")
        return self._write_connection
