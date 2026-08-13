"""Folder synchronization — copies and deletions executed off the UI thread.

Thin Qt wrapper around ``services.compare.folder_sync`` so the PySide6
compare view keeps the same worker API while the logic stays Qt-free for Flet.
"""

from __future__ import annotations

from devworkbench.services.compare.folder_sync import sync_folder_entries
from devworkbench.services.compare.models import FolderDiffEntry
from devworkbench.workers.base import Worker


class FolderSyncWorker(Worker):
    """Applies one sync operation to a batch of comparison entries."""

    def __init__(
        self,
        entries: list[FolderDiffEntry],
        operation: str,
        left_root: str,
        right_root: str,
    ) -> None:
        super().__init__()
        self._entries = entries
        self._operation = operation
        self._left_root = left_root
        self._right_root = right_root

    def work(self):
        return sync_folder_entries(
            self._entries, self._operation, self._left_root, self._right_root
        )
