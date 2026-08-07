"""Logging setup — rotating file log + console output.

All modules log through ``logging.getLogger("devworkbench.<module>")``; this
module configures the ``devworkbench`` root logger once at startup.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT_LOGGER_NAME = "devworkbench"
_FORMAT = "[%(asctime)s] %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(
    log_dir: str | Path | None = None,
    level: int = logging.INFO,
    console: bool = True,
    filename: str = "devworkbench.log",
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure the application logger. Safe to call more than once.

    Returns the ``devworkbench`` root logger.
    """
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    if console:
        if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
            stream = logging.StreamHandler()
            stream.setFormatter(formatter)
            logger.addHandler(stream)

    if log_dir is not None:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
            file_handler = RotatingFileHandler(
                directory / filename,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    logger.debug("logging initialised (level=%s, dir=%s)", level, log_dir)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Convenience: ``get_logger("git")`` -> ``devworkbench.git`` logger."""
    if name.startswith(ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
