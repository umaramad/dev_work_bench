#!/usr/bin/env python3
"""Profile DevWorkbench startup: phase timings, window-build time, peak RSS.

Usage:  QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/profile_startup.py

Output (macOS: ``ru_maxrss`` is bytes; on Linux it is KB, handled below):

    import devworkbench:      … ms
    import MODULES:           … ms
    import PySide6:           … ms
    import all infra:         … ms
    window constructed:       … ms after main() start
    total main():             … ms
    peak RSS:                 … MB
    build <module>:           … ms   (per-view lazy-build cost)

This is the canonical way to check the startup/memory budget
(see docs/performance.md for the current numbers and targets).
"""

from __future__ import annotations

import platform
import resource
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

PHASES: dict[str, float] = {}


def _timed(name: str):
    def wrap(fn):
        def inner(*args, **kwargs):
            start = time.perf_counter()
            result = fn(*args, **kwargs)
            PHASES[name] = time.perf_counter() - start
            return result
        return inner
    return wrap


@_timed("import devworkbench")
def _import_pkg():
    import devworkbench  # noqa: F401


@_timed("import MODULES")
def _import_modules():
    from devworkbench.modules import MODULES  # noqa: F401
    return MODULES


@_timed("import PySide6")
def _import_pyside():
    from PySide6.QtWidgets import QApplication  # noqa: F401
    return QApplication


def main() -> int:
    _import_pkg()
    MODULES = _import_modules()
    QApplication = _import_pyside()
    from PySide6.QtCore import QTimer

    @_timed("import all infra")
    def _import_infra():
        from devworkbench.core.config import ConfigLoader  # noqa: F401
        from devworkbench.core.container import DependencyContainer  # noqa: F401
        from devworkbench.core.events import EventBus  # noqa: F401
        from devworkbench.core.paths import Paths  # noqa: F401
        from devworkbench.core.settings import SettingsManager  # noqa: F401
        from devworkbench.database.connection import ConnectionManager  # noqa: F401
        from devworkbench.database.migrations import Migrator  # noqa: F401
        from devworkbench.services.ai import AIProviderFactory  # noqa: F401
        from devworkbench.services.compare.engine import CompareEngine  # noqa: F401
        from devworkbench.services.configuration_service import ConfigurationService  # noqa: F401
        from devworkbench.ui.icons import IconProvider  # noqa: F401
        from devworkbench.ui.main_window import MainWindow  # noqa: F401
        from devworkbench.ui.theme import ThemeManager  # noqa: F401

    _import_infra()

    import devworkbench.bootstrap as b

    # Measure when the window is constructed (auto-close shortly after).
    original_init = b.MainWindow.__init__
    window_built_at: list[float] = []

    def instrumented(self, *a, **k):
        original_init(self, *a, **k)
        window_built_at.append(time.perf_counter())
        QTimer.singleShot(400, self.close)

    b.MainWindow.__init__ = instrumented

    start = time.perf_counter()
    b.main(["profile"])
    total = time.perf_counter() - start

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() == "Linux":
        rss_kb = rss  # ru_maxrss is KB on Linux
    else:  # macOS / BSD: ru_maxrss is bytes
        rss_kb = rss / 1024

    print("== startup profile ==")
    for name, elapsed in PHASES.items():
        print(f"  {name:24s} {elapsed * 1000:7.0f} ms")
    print(f"  {'window constructed after main() start':24s} {(window_built_at[0] - start) * 1000:7.0f} ms")
    print(f"  {'total main()':24s} {total * 1000:7.0f} ms")
    print(f"  {'peak RSS':24s} {rss_kb / 1024:7.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
