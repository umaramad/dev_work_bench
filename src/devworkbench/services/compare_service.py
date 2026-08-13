"""CompareService — Qt-free async compare + folder sync for the Flet UI.

Mirrors ``CompareWorker`` / ``FolderSyncWorker`` without QRunnable: each
operation runs the existing ``CompareEngine`` (or folder sync helpers) in a
thread via ``asyncio.to_thread`` so the Flet event loop stays responsive.
"""

from __future__ import annotations

import asyncio

from devworkbench.services.compare.encoding import read_lines
from devworkbench.services.compare.engine import CompareEngine
from devworkbench.services.compare.folder_sync import sync_folder_entries
from devworkbench.services.compare.models import CompareOptions, FolderDiffEntry


class CompareService:
    """Async facade over the shared compare engine and folder sync."""

    def __init__(self, engine: CompareEngine | None = None) -> None:
        self._engine = engine or CompareEngine()

    async def compare(
        self,
        left: str = "",
        right: str = "",
        mode: str = "files",
        options: CompareOptions | None = None,
        kind: str = "text",
        file_side: str = "right",
    ):
        """Run a compare in a worker thread. Modes match ``CompareWorker``."""
        opts = options or CompareOptions()
        return await asyncio.to_thread(
            self._compare_sync, left, right, mode, opts, kind, file_side
        )

    async def sync(
        self,
        entries: list[FolderDiffEntry],
        operation: str,
        left_root: str,
        right_root: str,
    ) -> dict:
        """Apply folder sync off the event loop."""
        return await asyncio.to_thread(
            sync_folder_entries, entries, operation, left_root, right_root
        )

    def options_from_config(self, config) -> CompareOptions:
        """Build ``CompareOptions`` from a ``ConfigurationService`` (or None)."""
        if config is None:
            return CompareOptions()
        ignore_dirs_raw = str(config.get("compare.ignore_dirs") or "")
        ignore_dirs = tuple(
            part.strip() for part in ignore_dirs_raw.split(",") if part.strip()
        )
        return CompareOptions(
            ignore_whitespace=bool(config.get("compare.ignore_whitespace")),
            ignore_blank_lines=bool(config.get("compare.ignore_blank_lines")),
            ignore_case=bool(config.get("compare.ignore_case")),
            ignore_comments=bool(config.get("compare.ignore_comments")),
            context_lines=int(config.get("compare.context_lines") or 3),
            engine=str(config.get("compare.engine") or "auto"),
            follow_symlinks=bool(config.get("compare.follow_symlinks")),
            detect_moves=bool(config.get("compare.detect_moves")),
            ignore_dirs=ignore_dirs or CompareOptions().ignore_dirs,
        ).normalize()

    def _compare_sync(
        self,
        left: str,
        right: str,
        mode: str,
        options: CompareOptions,
        kind: str,
        file_side: str,
    ):
        if mode == "folders":
            return self._engine.compare_folders(left, right, options)
        if mode == "texts":
            return self._engine.compare_texts(left, right, kind=kind, options=options)
        if mode == "texts_with_file":
            if file_side == "left":
                lines, _encoding = read_lines(left, limit=options.max_lines + 1)
                return self._engine.compare_texts(
                    "\n".join(lines), right, kind=kind, options=options
                )
            lines, _encoding = read_lines(right, limit=options.max_lines + 1)
            return self._engine.compare_texts(
                left, "\n".join(lines), kind=kind, options=options
            )
        return self._engine.compare_files(left, right, options)
