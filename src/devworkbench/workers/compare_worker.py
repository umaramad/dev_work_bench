"""Compare worker — runs the comparison engine off the UI thread.

One worker per request; the UI constructs it on the UI thread (signal
delivery contract in ``workers/base.py``) and **retains a reference until
finished/error fires** — a QRunnable held only by the pool is destroyed
right after ``run()`` and its queued result signal is dropped.
"""

from __future__ import annotations

from devworkbench.services.compare.encoding import read_lines
from devworkbench.services.compare.engine import CompareEngine
from devworkbench.services.compare.models import CompareOptions
from devworkbench.workers.base import Worker


class CompareWorker(Worker):
    """Compares two paths (files or folders) or two in-memory texts.

    ``mode``: ``files`` (auto-detect text vs binary), ``folders``, or
    ``texts`` (with explicit ``left``/``right`` strings and a ``kind``).
    ``texts_with_file`` mixes the two: one side is an in-memory text
    (possibly empty) and the other a file path that is **read on the worker
    thread** — used for one-sided folder entries, so a huge file never
    blocks the UI. ``file_side`` names which side holds the path.
    """

    def __init__(
        self,
        left: str = "",
        right: str = "",
        mode: str = "files",
        options: CompareOptions | None = None,
        kind: str = "text",
        file_side: str = "right",
    ) -> None:
        super().__init__()
        self._left = left
        self._right = right
        self._mode = mode
        self._options = options or CompareOptions()
        self._kind = kind
        self._file_side = file_side
        self._engine = CompareEngine()

    def work(self):
        if self._mode == "folders":
            return self._engine.compare_folders(self._left, self._right, self._options)
        if self._mode == "texts":
            return self._engine.compare_texts(
                self._left, self._right, kind=self._kind, options=self._options
            )
        if self._mode == "texts_with_file":
            # Read one line past the limit so compare_texts can detect the
            # overflow itself and report truncated=True (same trick as
            # CompareEngine.compare_files) instead of silently cutting off.
            if self._file_side == "left":
                lines, _encoding = read_lines(self._left, limit=self._options.max_lines + 1)
                return self._engine.compare_texts(
                    "\n".join(lines), self._right, kind=self._kind, options=self._options
                )
            lines, _encoding = read_lines(self._right, limit=self._options.max_lines + 1)
            return self._engine.compare_texts(
                self._left, "\n".join(lines), kind=self._kind, options=self._options
            )
        return self._engine.compare_files(self._left, self._right, self._options)
