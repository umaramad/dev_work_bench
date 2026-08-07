"""Folder synchronization — copies and deletions executed off the UI thread.

Operations target a list of ``FolderDiffEntry`` from a folder comparison and
apply the user's intent per entry state:

- ``copy_left_to_right`` — only_left: create the missing right file;
  modified: overwrite the right file; moved/renamed: copy to the matched
  right path. only_right/identical are left untouched.
- ``copy_right_to_left`` — the mirror image.
- ``delete`` — removes the file on the side where it is a stray
  (only_left → left, only_right → right, moved/renamed → left source);
  modified rows remove *both* sides (the view confirms this explicitly).

Paths are validated against ``..`` / absolute escapes before any I/O. The
report counts what happened so the UI can show it and re-compare.
"""

from __future__ import annotations

import shutil
from pathlib import Path

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
        self._left_root = Path(left_root)
        self._right_root = Path(right_root)

    def work(self):
        report = {"copied": 0, "overwritten": 0, "deleted": 0, "failed": []}
        for entry in self._entries:
            try:
                self._apply(entry, report)
            except OSError as exc:
                report["failed"].append(f"{entry.relative}: {exc}")
        return report

    # -- internals -----------------------------------------------------------

    def _apply(self, entry: FolderDiffEntry, report: dict) -> None:
        relative = _safe_relative(entry.relative)
        pair = _safe_relative(entry.pair) if entry.pair else None
        left_path = self._left_root / relative
        right_path = self._right_root / relative
        state = entry.state

        if self._operation == "copy_left_to_right":
            if state == "only_left":
                _copy(left_path, right_path)
                report["copied"] += 1
            elif state == "modified":
                _copy(left_path, right_path)
                report["copied"] += 1
                report["overwritten"] += 1
            elif state in ("moved", "renamed") and pair:
                _copy(left_path, self._right_root / pair)
                report["copied"] += 1

        elif self._operation == "copy_right_to_left":
            if state == "only_right":
                _copy(right_path, left_path)
                report["copied"] += 1
            elif state == "modified":
                _copy(right_path, left_path)
                report["copied"] += 1
                report["overwritten"] += 1
            elif state in ("moved", "renamed") and pair:
                _copy(self._right_root / pair, left_path)
                report["copied"] += 1

        elif self._operation == "delete":
            if state == "only_left":
                _delete(left_path)
                report["deleted"] += 1
            elif state == "only_right":
                _delete(right_path)
                report["deleted"] += 1
            elif state in ("moved", "renamed"):
                _delete(left_path)
                report["deleted"] += 1
            elif state == "modified":
                _delete(left_path)
                _delete(right_path)
                report["deleted"] += 1


def _safe_relative(relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise OSError(f"unsafe path rejected: {relative}")
    return path


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)  # copy2 preserves mtime


def _delete(path: Path) -> None:
    if path.exists() and path.is_file():
        path.unlink()
