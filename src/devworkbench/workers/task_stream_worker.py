"""TaskStreamWorker — a cancellable, output-streaming task for the Run/Stop
toolbar actions.

The scaffold has no real build pipeline yet, so this worker *simulates* one:
it streams labelled lines into the Output dock terminal while reporting
progress to the Tasks table. Cancellation is cooperative (``cancel()`` is
checked between steps), which is exactly the contract a real task runner will
need — swap the ``steps`` payload for real subprocess output later without
touching the UI.

Signals contract: construct on the UI thread, keep a reference until
``finished``/``error`` fires (see ``workers/base.py``).
"""

from __future__ import annotations

import time

from devworkbench.workers.base import Worker

# (line, percent-complete) — the simulated pipeline.
_DEFAULT_STEPS: tuple[tuple[str, int], ...] = (
    ("$ ./scripts/dev.sh checks", 0),
    ("✔ syntax — 0 errors", 30),
    ("✔ tests — 181 passed, 1 skipped", 70),
    ("✔ package — bundle ok", 90),
    ("All checks passed — 12.4s", 100),
)

_STEP_PAUSE_SECONDS = 0.35


class TaskStreamWorker(Worker):
    """Streams a mock pipeline to the Output dock; ``cancel()`` stops it."""

    def __init__(self, title: str = "Run checks", steps: tuple[tuple[str, int], ...] | None = None) -> None:
        super().__init__()
        self._title = title
        self._steps = steps or _DEFAULT_STEPS

    def work(self):
        """Emit each step as a line + progress point, then return a summary."""
        for line, percent in self._steps:
            if self._cancelled:
                return "cancelled"
            self.signals.line.emit(line)
            self.report(percent)
            time.sleep(_STEP_PAUSE_SECONDS)
        if self._cancelled:
            return "cancelled"
        return "completed"
