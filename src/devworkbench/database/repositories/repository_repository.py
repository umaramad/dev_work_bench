"""RepositoriesRepository — versioned working copies in the ``repositories`` table."""

from __future__ import annotations

from devworkbench.database.orm import CrudRepository
from devworkbench.models.persistence import RepositoryRecord


class RepositoriesRepository(CrudRepository[RepositoryRecord]):
    """CRUD plus lookups over tracked git working copies."""

    model = RepositoryRecord

    def by_path(self, path: str) -> RepositoryRecord | None:
        row = self._fetch_one("SELECT * FROM repositories WHERE path = ?", (path,))
        return self.model.from_row(row)

    def by_name(self, name: str) -> RepositoryRecord | None:
        row = self._fetch_one("SELECT * FROM repositories WHERE name = ?", (name,))
        return self.model.from_row(row)

    def touch(self, repository_id: int) -> None:
        """Mark a working copy as recently opened."""
        self._execute(
            "UPDATE repositories SET last_opened_at = datetime('now') WHERE id = ?",
            (repository_id,),
        )
