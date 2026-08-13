"""Folder synchronization — Qt-free copy/delete for compare results.

Shared by the PySide6 ``FolderSyncWorker`` and the Flet ``CompareService``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from devworkbench.services.compare.models import FolderDiffEntry


def sync_folder_entries(
    entries: list[FolderDiffEntry],
    operation: str,
    left_root: str,
    right_root: str,
) -> dict:
    """Apply one sync operation to a batch of comparison entries.

    Returns ``{copied, overwritten, deleted, failed[]}``.
    """
    report: dict = {"copied": 0, "overwritten": 0, "deleted": 0, "failed": []}
    left = Path(left_root)
    right = Path(right_root)
    for entry in entries:
        try:
            _apply(entry, operation, left, right, report)
        except OSError as exc:
            report["failed"].append(f"{entry.relative}: {exc}")
    return report


def _apply(
    entry: FolderDiffEntry,
    operation: str,
    left_root: Path,
    right_root: Path,
    report: dict,
) -> None:
    relative = _safe_relative(entry.relative)
    pair = _safe_relative(entry.pair) if entry.pair else None
    left_path = left_root / relative
    right_path = right_root / relative
    state = entry.state

    if operation == "copy_left_to_right":
        if state == "only_left":
            _copy(left_path, right_path)
            report["copied"] += 1
        elif state == "modified":
            _copy(left_path, right_path)
            report["copied"] += 1
            report["overwritten"] += 1
        elif state in ("moved", "renamed") and pair:
            _copy(left_path, right_root / pair)
            report["copied"] += 1

    elif operation == "copy_right_to_left":
        if state == "only_right":
            _copy(right_path, left_path)
            report["copied"] += 1
        elif state == "modified":
            _copy(right_path, left_path)
            report["copied"] += 1
            report["overwritten"] += 1
        elif state in ("moved", "renamed") and pair:
            _copy(right_root / pair, left_path)
            report["copied"] += 1

    elif operation == "delete":
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
    shutil.copy2(source, target)


def _delete(path: Path) -> None:
    if path.exists() and path.is_file():
        path.unlink()
