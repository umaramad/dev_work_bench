"""HistoryRepository — recent files, recent folders, command history.

Typed over ``RecentFile`` / ``RecentFolder`` / ``HistoryEntry``; ordering is
insertion-based (ids descend) because ``opened_at`` has second granularity.
"""

from __future__ import annotations

from devworkbench.database.orm import CrudRepository
from devworkbench.models.persistence import HistoryEntry, RecentFile, RecentFolder


class HistoryRepository:
    """Recent files / folders and command-palette history (three tables)."""

    def __init__(self, connection_manager) -> None:
        self._recent_files = _RecentFilesRepository(connection_manager)
        self._recent_folders = _RecentFoldersRepository(connection_manager)
        self._commands = _CommandsRepository(connection_manager)

    # -- recent files -----------------------------------------------------------------

    def add_recent(self, module: str, path: str) -> None:
        self._recent_files.insert(RecentFile(module=module, path=path))

    def recent(self, module: str, limit: int = 10) -> list[RecentFile]:
        return self._recent_files.recent(module, limit)

    def clear_recent(self, module: str | None = None) -> None:
        if module is None:
            self._recent_files._execute("DELETE FROM recent_files")
        else:
            self._recent_files._execute(
                "DELETE FROM recent_files WHERE module = ?", (module,)
            )

    # -- recent folders -----------------------------------------------------------------

    def add_recent_folder(self, path: str) -> None:
        self._recent_folders.insert(RecentFolder(path=path))

    def recent_folders(self, limit: int = 10) -> list[RecentFolder]:
        return self._recent_folders.recent(limit)

    def clear_recent_folders(self) -> None:
        self._recent_folders._execute("DELETE FROM recent_folders")

    # -- command history -----------------------------------------------------------------

    def add_command(self, command: str, module: str = "") -> None:
        self._commands.insert(HistoryEntry(command=command, module=module))

    def recent_commands(self, limit: int = 20) -> list[HistoryEntry]:
        return self._commands.recent(limit)

    def clear_commands(self, module: str | None = None) -> None:
        if module is None:
            self._commands._execute("DELETE FROM command_history")
        else:
            self._commands._execute(
                "DELETE FROM command_history WHERE module = ?", (module,)
            )


class _RecentFilesRepository(CrudRepository[RecentFile]):
    model = RecentFile

    def recent(self, module: str, limit: int = 10) -> list[RecentFile]:
        rows = self._fetch_all(
            "SELECT * FROM recent_files WHERE module = ? ORDER BY id DESC LIMIT ?",
            (module, limit),
        )
        return [self.model.from_row(row) for row in rows]


class _RecentFoldersRepository(CrudRepository[RecentFolder]):
    model = RecentFolder

    def recent(self, limit: int = 10) -> list[RecentFolder]:
        rows = self._fetch_all(
            "SELECT * FROM recent_folders ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [self.model.from_row(row) for row in rows]


class _CommandsRepository(CrudRepository[HistoryEntry]):
    model = HistoryEntry

    def recent(self, limit: int = 20) -> list[HistoryEntry]:
        rows = self._fetch_all(
            "SELECT * FROM command_history ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [self.model.from_row(row) for row in rows]
