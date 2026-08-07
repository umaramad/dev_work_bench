"""Task executor — the only place threads are created.

Wraps Qt's ``QThreadPool`` so the UI layer can schedule ``core.tasks.Task``
objects without ever touching threads directly. This is the *scheduler*;
long-running workers (parsers, indexers, SSH sessions) subclass classes in
``devworkbench.workers``.
"""

from __future__ import annotations

from PySide6.QtCore import QRunnable, QThreadPool

from devworkbench.core.tasks import Task


class _TaskRunnable(QRunnable):
    """QRunnable adapter that executes a Task on the pool."""

    def __init__(self, task: Task) -> None:
        super().__init__()
        self._task = task
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: N802 (Qt naming)
        self._task.run()


class TaskExecutor:
    """Bounded background scheduler built on ``QThreadPool``."""

    def __init__(self, max_threads: int | None = None) -> None:
        self._pool = QThreadPool.globalInstance()
        if max_threads is not None:
            self._pool.setMaxThreadCount(max_threads)
        self._pool.setExpiryTimeout(30_000)

    def submit(self, task: Task) -> None:
        """Schedule ``task`` on a worker thread."""
        self._pool.start(_TaskRunnable(task))

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Wait for pending tasks; returns True if the pool drained in time."""
        return self._pool.waitForDone(timeout_ms)

    @property
    def active_thread_count(self) -> int:
        return self._pool.activeThreadCount()

    @property
    def max_thread_count(self) -> int:
        return self._pool.maxThreadCount()
