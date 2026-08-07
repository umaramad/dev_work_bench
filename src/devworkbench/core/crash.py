"""Crash handling — unhandled exceptions and native crashes never go silent.

Three layers, all wired in ``bootstrap.main`` after logging is up:

- ``install_excepthook`` — unhandled Python exceptions are logged with a full
  traceback to ``<log_dir>/crash-<timestamp>.log`` (and a copy to the console
  when a console is attached, e.g. debug builds).
- ``faulthandler`` — native segfaults/aborts dump the Python stack to
  ``<log_dir>/faulthandler.log`` (registered at start, flushed on SIGUSR1).
- ``install_qt_message_handler`` — Qt warnings/criticals (``qWarning``,
  ``qCritical``, ``qFatal``) are routed into the Python logger so they land in
  the same rotating log instead of vanishing to stderr.

Crash files are intentionally separate from the rotating app log so a crash
is never rotated away before it is examined.
"""

from __future__ import annotations

import faulthandler
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any

logger = logging.getLogger("devworkbench.crash")


def install_excepthook(log_dir: str | Path) -> None:
    """Replace ``sys.excepthook`` with one that writes a crash report file.

    Only installed when the previous hook is the interpreter default or the
    ``exceptiongroup`` backport's transparent wrapper (Python 3.9/3.10 — see
    :func:`_is_default_hook`), i.e. not already customised by a test runner /
    debugger, so pytest and IDEs keep their own error reporting.
    """
    previous = sys.excepthook
    if not _is_default_hook(previous):
        logger.debug("excepthook already customised; not wrapping")
        return

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)

    def hook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        lines = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        path = directory / f"crash-{timestamp}.log"
        try:
            path.write_text(lines, encoding="utf-8")
        except OSError:
            logger.error("could not write crash report to %s", path)
        # Always reach the console / test output too — delegate to whatever
        # hook was in place before us, keeping wrapper chains intact.
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = hook
    logger.debug("excepthook installed -> %s", directory)


def _is_default_hook(hook: Any) -> bool:
    """True when ``hook`` is safe to wrap: the interpreter default, or the
    ``exceptiongroup`` backport's transparent wrapper.

    On Python 3.9/3.10 the ``exceptiongroup`` package (pulled in by pytest)
    eagerly replaces ``sys.excepthook`` on import; its wrapper delegates to
    the default for non-ExceptionGroup exceptions, so wrapping it behaves
    exactly like wrapping the default. Anything else (a debugger, IDE or
    test-runner hook) is left untouched.
    """
    if hook is sys.__excepthook__:
        return True
    return getattr(hook, "__name__", "") == "exceptiongroup_excepthook"


def install_faulthandler(log_dir: str | Path) -> None:
    """Dump the Python stack to a file on native crashes (segfault, abort).

    ``all_threads=True`` captures worker-thread stacks, which is where diff /
    sync crashes would show up. The log file is flushed on SIGUSR1 so a hung
    app can be diagnosed without killing it.
    """
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "faulthandler.log"
    try:
        handle = open(path, "w", encoding="utf-8")  # noqa: PTH123 — faulthandler needs a real fd
    except OSError:
        logger.warning("faulthandler: cannot open %s", path)
        return
    faulthandler.enable(handle, all_threads=True)
    try:
        faulthandler.register(30, all_threads=True)  # SIGUSR1 (macOS)
    except (ValueError, OSError):
        pass  # signal registration unavailable in this environment
    logger.debug("faulthandler installed -> %s", path)


def install_qt_message_handler() -> None:
    """Route Qt's qWarning/qCritical/qFatal into the Python logging system."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler  # local import: Qt-only

    # PySide6 hands the handler a QtMsgType enum (``str(kind) == "1"``), not a
    # string name — a string-keyed lookup would fall through to ERROR for every
    # message. Key the map on the enum values directly.
    _LEVELS = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(kind: QtMsgType, context: Any, message: str) -> None:
        # context is a QMessageLogContext; avoid importing QtGui here.
        level = _LEVELS.get(kind, logging.ERROR)
        qlogger = logging.getLogger("devworkbench.qt")
        qlogger.log(level, "%s", message)

    qInstallMessageHandler(handler)
    logger.debug("Qt message handler installed")
