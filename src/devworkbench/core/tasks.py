"""Background work unit — a ``Task`` wraps a callable for the worker pool.

The executor (``core.workers.TaskExecutor``) runs tasks off the UI thread;
results are reported through callbacks. Exception safety: a failing task
never crashes a worker — it becomes a ``TaskResult`` with ``ok=False``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class TaskError(Exception):
    """Raised when a task is used incorrectly (e.g. run twice)."""


@dataclass
class TaskResult:
    """Outcome of a finished task."""

    task: "Task"
    ok: bool
    value: Any = None
    error: BaseException | None = None
    duration: float = 0.0

    def raise_if_failed(self) -> Any:
        """Return the value or re-raise the captured error."""
        if not self.ok and self.error is not None:
            raise self.error
        return self.value


class Task:
    """One unit of background work with cooperative cancellation.

    Callbacks are invoked from the worker thread — UI code must marshal
    results back to the main thread (queued Qt signals).
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_ready: Callable[[TaskResult], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        **kwargs: Any,
    ) -> None:
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancelled = False
        self._done = False
        self._result: TaskResult | None = None
        self._on_ready = on_ready
        self._on_error = on_error

    # -- lifecycle --------------------------------------------------------------

    def run(self) -> TaskResult:
        """Execute synchronously; safe to call from any thread exactly once."""
        if self._done:
            raise TaskError("task already ran")
        self._done = True
        started = time.monotonic()
        try:
            value = self._fn(*self._args, **self._kwargs)
            result = TaskResult(self, ok=True, value=value, duration=time.monotonic() - started)
        except Exception as exc:  # noqa: BLE001 — tasks must not kill workers
            result = TaskResult(self, ok=False, error=exc, duration=time.monotonic() - started)
        self._result = result
        if result.ok and self._on_ready is not None:
            self._on_ready(result)
        elif not result.ok and self._on_error is not None:
            self._on_error(result.error)  # type: ignore[arg-type]
        return result

    def cancel(self) -> None:
        """Request cooperative cancellation (checked by long-running work)."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def done(self) -> bool:
        return self._done

    @property
    def result(self) -> TaskResult | None:
        return self._result

    def __repr__(self) -> str:
        fn = getattr(self._fn, "__name__", repr(self._fn))
        return f"<Task {fn} cancelled={self._cancelled} done={self._done}>"
