"""FavoriteRepository — starred items in the ``favorites`` table."""

from __future__ import annotations

from devworkbench.database.orm import CrudRepository
from devworkbench.models.persistence import Favorite


class FavoriteRepository(CrudRepository[Favorite]):
    """CRUD plus starred-item queries (deduped on kind + ref)."""

    model = Favorite

    def by_kind(self, kind: str, limit: int | None = None) -> list[Favorite]:
        """Favorites of one kind, newest first (returns all by default).

        An explicit ``limit`` caps the newest rows; callers that need only a
        bounded slice should pass it deliberately — a hidden default cap used
        to silently drop favorites past 100 (broken groups, half-rendered
        landing lists), so "all" is the safe default.
        """
        if limit is None:
            rows = self._fetch_all(
                "SELECT * FROM favorites WHERE kind = ? ORDER BY id DESC",
                (kind,),
            )
        else:
            rows = self._fetch_all(
                "SELECT * FROM favorites WHERE kind = ? ORDER BY id DESC LIMIT ?",
                (kind, limit),
            )
        return [self.model.from_row(row) for row in rows]

    def is_favorite(self, kind: str, ref: str) -> bool:
        row = self._fetch_one(
            "SELECT 1 FROM favorites WHERE kind = ? AND ref = ?", (kind, ref)
        )
        return row is not None

    def find(self, kind: str, ref: str) -> Favorite | None:
        row = self._fetch_one(
            "SELECT * FROM favorites WHERE kind = ? AND ref = ?", (kind, ref)
        )
        return self.model.from_row(row)

    def toggle(self, kind: str, ref: str, label: str = "") -> bool:
        """Star an item; returns True when it was newly added (False = removed)."""
        existing = self.find(kind, ref)
        if existing is not None:
            self.delete(existing.id)
            return False
        self.insert(Favorite(kind=kind, ref=ref, label=label))
        return True

    def remove_ref(self, kind: str, ref: str) -> bool:
        return self._execute(
            "DELETE FROM favorites WHERE kind = ? AND ref = ?", (kind, ref)
        ) > 0
