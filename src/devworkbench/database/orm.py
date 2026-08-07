"""Lightweight ORM — typed rows over the SQLite layer.

Two pieces:

- ``PersistentModel`` — a dataclass that declares its ``table`` and
  ``columns`` so a row can become a typed object and back. JSON columns are
  transparently encoded/decoded.
- ``CrudRepository`` — a generic repository (the ``Repository`` pattern) that
  provides ``list / get / insert / update / upsert / delete / count`` for any
  ``PersistentModel`` without per-table boilerplate. Concrete repositories add
  domain queries on top.

This module deliberately imports nothing from the ``repositories`` package
(or the ``models`` package): persistence models import *it*, so any upward
dependency would create an import cycle. It composes ``ConnectionManager``
directly with the same query helpers ``BaseRepository`` exposes.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from typing import Any, ClassVar, Generic, TypeVar

from devworkbench.database.connection import ConnectionManager

ModelT = TypeVar("ModelT", bound="PersistentModel")


class PersistentModel:
    """A domain model bound to one SQLite table.

    Subclasses declare ``table`` and ``columns``; JSON columns are listed in
    ``json_columns`` and are encoded with ``json.dumps`` on write and decoded
    on read, so callers always see native Python objects.
    """

    table: ClassVar[str] = ""
    columns: ClassVar[tuple[str, ...]] = ()
    json_columns: ClassVar[frozenset[str]] = frozenset()
    primary_key: ClassVar[str] = "id"
    # True: the DB assigns the key (INTEGER PRIMARY KEY AUTOINCREMENT) so
    # inserts omit it. False: the key is supplied by the caller (TEXT
    # primary keys such as app_settings.key / plugins.id) so inserts keep it.
    auto_key: ClassVar[bool] = True

    # -- dict mapping (mirrors models.base.Model) ------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersistentModel":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in fields})

    # -- row mapping --------------------------------------------------------------

    _bool_fields: ClassVar[frozenset[str] | None] = None

    @classmethod
    def from_row(cls, row: dict[str, Any] | None) -> ModelT | None:
        """Build a model from a raw row dict (``None`` in, ``None`` out)."""
        if row is None:
            return None
        data = dict(row)
        for column in cls.json_columns:
            raw = data.get(column)
            if isinstance(raw, str):
                try:
                    data[column] = json.loads(raw)
                except (TypeError, ValueError):
                    data[column] = []
            elif raw is None:
                data[column] = []
        # Bool columns are stored as 0/1; coerce back to Python bools.
        if cls._bool_fields is None:
            cls._bool_fields = frozenset(
                f.name
                for f in dataclasses.fields(cls)
                if f.type is bool or f.type == "bool"  # string under future-annotations
            )
        for column in cls._bool_fields:
            if column in data and data[column] is not None:
                data[column] = bool(data[column])
        return cls(**{key: value for key, value in data.items() if key in cls.columns})

    def to_row(self) -> dict[str, Any]:
        """Serialize to a row dict (JSON columns encoded)."""
        row: dict[str, Any] = {}
        for column in self.columns:
            value = getattr(self, column)
            if column in self.json_columns:
                row[column] = json.dumps(value)
            else:
                row[column] = value
        return row

    # -- equality ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PersistentModel):
            return NotImplemented
        return self.to_row() == other.to_row()

    def __hash__(self) -> int:  # needed when __eq__ is defined
        return id(self)


class CrudRepository(Generic[ModelT]):
    """Generic repository implementing the standard CRUD surface.

    ``model`` is set on the concrete subclass. Columns come from the model's
    ``columns`` declaration; ``primary_key`` drives get/update/upsert/delete.
    """

    model: ClassVar[type[ModelT]]

    # table -> columns that carry a DB-side DEFAULT (cached from PRAGMA).
    _db_defaults: ClassVar[dict[str, frozenset[str]]] = {}

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._manager = connection_manager

    # -- query helpers (same contract as BaseRepository) -----------------------------

    def _fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
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

    # -- reads ---------------------------------------------------------------------

    def list(self, order_by: str | None = None, limit: int | None = None) -> list[ModelT]:
        sql = f"SELECT * FROM {self.model.table}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [self.model.from_row(row) for row in self._fetch_all(sql)]  # type: ignore[misc]

    def get(self, primary_key: Any) -> ModelT | None:
        row = self._fetch_one(
            f"SELECT * FROM {self.model.table} WHERE {self.model.primary_key} = ?",
            (primary_key,),
        )
        return self.model.from_row(row)

    def count(self) -> int:
        row = self._fetch_one(f"SELECT COUNT(*) AS n FROM {self.model.table}")
        return int(row["n"]) if row else 0

    # -- writes ---------------------------------------------------------------------

    def insert(self, model: ModelT) -> int:
        """Insert and return the new primary key (also set on ``model``).

        Columns whose value is still the dataclass default *and* that carry a
        DB-side DEFAULT (e.g. ``created_at``/``opened_at``) are omitted so
        SQLite's ``datetime('now')`` applies instead of storing an empty
        string.
        """
        row = model.to_row()
        columns = [c for c in self.model.columns if c != self.model.primary_key]
        if not self.model.auto_key:
            columns = list(self.model.columns)

        db_defaults = self._db_defaulted_columns()
        omitted = ()
        if db_defaults:
            field_defaults = {
                f.name: f.default for f in dataclasses.fields(self.model)
            }
            omitted = tuple(
                c
                for c in columns
                if c in db_defaults and row[c] == field_defaults.get(c)
            )
            columns = [c for c in columns if c not in omitted]

        def _run(connection: sqlite3.Connection) -> int:
            cursor = connection.execute(
                f"INSERT INTO {self.model.table} ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(row[c] for c in columns),
            )
            return int(cursor.lastrowid)

        primary_key = self._manager.write(_run)
        if self.model.auto_key:
            setattr(model, self.model.primary_key, primary_key)
        # Re-read the row when SQLite filled DB-defaulted columns (timestamps)
        # so the in-memory model mirrors what was actually stored.
        if omitted:
            fresh = self.get(primary_key)
            if fresh is not None:
                for column in omitted:
                    setattr(model, column, getattr(fresh, column))
        return primary_key

    def _db_defaulted_columns(self) -> frozenset[str]:
        """Columns of ``model.table`` with a non-trivial SQL DEFAULT."""
        table = self.model.table
        cached = CrudRepository._db_defaults.get(table)
        if cached is not None:
            return cached
        with self._manager.read() as connection:
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        defaulted = frozenset(
            str(row[1]) for row in rows if row[4] and row[4] != "0"
        )
        CrudRepository._db_defaults[table] = defaulted
        return defaulted

    def update(self, model: ModelT) -> bool:
        """Update every column except the primary key; True if a row matched."""
        row = model.to_row()
        columns = [c for c in self.model.columns if c != self.model.primary_key]
        assignments = ", ".join(f"{c} = ?" for c in columns)
        values = tuple(row[c] for c in columns) + (getattr(model, self.model.primary_key),)
        sql = f"UPDATE {self.model.table} SET {assignments} WHERE {self.model.primary_key} = ?"
        return self._execute(sql, values) > 0

    def upsert(self, model: ModelT) -> None:
        """Insert, or update when the primary key already exists."""
        existing = self.get(getattr(model, self.model.primary_key))
        if existing is None:
            self.insert(model)
        else:
            self.update(model)

    def delete(self, primary_key: Any) -> bool:
        return (
            self._execute(
                f"DELETE FROM {self.model.table} WHERE {self.model.primary_key} = ?",
                (primary_key,),
            )
            > 0
        )
