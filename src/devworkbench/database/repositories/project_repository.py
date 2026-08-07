"""ProjectRepository — workspace projects backed by the ``projects`` table."""

from __future__ import annotations

from typing import Any

from devworkbench.database.orm import CrudRepository
from devworkbench.models.persistence import Project


class ProjectRepository(CrudRepository[Project]):
    """CRUD plus project-specific queries (unique path, name search)."""

    model = Project

    def by_path(self, path: str) -> Project | None:
        row = self._fetch_one("SELECT * FROM projects WHERE path = ?", (path,))
        return self.model.from_row(row)

    def search(self, term: str, limit: int = 20) -> list[Project]:
        pattern = f"%{term}%"
        rows = self._fetch_all(
            "SELECT * FROM projects WHERE name LIKE ? OR path LIKE ? OR description LIKE ? "
            "ORDER BY name LIMIT ?",
            (pattern, pattern, pattern, limit),
        )
        return [self.model.from_row(row) for row in rows]

    def touch(self, project_id: int) -> None:
        """Mark a project as recently opened."""
        self._execute(
            "UPDATE projects SET last_opened_at = datetime('now'), "
            "updated_at = datetime('now') WHERE id = ?",
            (project_id,),
        )

    def insert(self, model: Project) -> int:
        model.updated_at = model.created_at = _now()
        return super().insert(model)

    def update(self, model: Project) -> bool:
        model.updated_at = _now()
        return super().update(model)


def _now() -> str:
    import datetime

    return datetime.datetime.now().isoformat(timespec="seconds")
