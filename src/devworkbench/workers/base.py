"""Worker base — long-running work with cooperative cancellation."""

from __future__ import annotations

from abc import abstractmethod

from PySide6.QtCore import QRunnable, Signal, QObject


class _WorkerSignals(QObject):
    """Thread-safe signal carrier for worker results (delivered on UI thread)."""

    finished = Signal(object)
    error = Signal(object)
    progress = Signal(int, object)
    line = Signal(str)


class Worker(QRunnable):
    """Base class for long-running workers executed by ``TaskExecutor``.

    Subclasses implement ``work()``; ``run`` guards it so exceptions are
    emitted as signals instead of crashing the thread pool.

    Threading contract: construct workers **on the UI thread** so the
    signals object lives there — then ``emit`` from a worker thread is
    delivered to UI receivers via a queued connection. Creating a worker
    on a worker thread would make those emits direct calls.

    Retention contract: callers **must keep a reference to the worker until
    its finished/error signal fires**. A QRunnable held only by the thread
    pool is auto-deleted right after ``run()``, which destroys the signals
    QObject — a queued event targeting it is then dropped silently.
    """

    def __init__(self) -> None:
        super().__init__()
        self._cancelled = False
        self.signals = _WorkerSignals()

    # -- public API --------------------------------------------------------------

    def cancel(self) -> None:
        """Request cooperative cancellation (checked by long operations)."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:  # noqa: N802 (Qt naming)
        if self._cancelled:
            return
        try:
            result = self.work()
            if not self._cancelled:
                self.signals.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 — emit, don't crash the pool
            self.signals.error.emit(exc)

    # -- subclass contract ------------------------------------------------------------

    @abstractmethod
    def work(self):
        """Do the work; return a result (or None)."""

    def report(self, percent: int, payload=None) -> None:
        """Emit a progress update (0–100)."""
        self.signals.progress.emit(percent, payload)
