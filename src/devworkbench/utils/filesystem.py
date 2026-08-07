"""Filesystem helpers — naming, sizes, encodings."""

from __future__ import annotations

import re
from pathlib import Path

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str, fallback: str = "untitled") -> str:
    """Sanitize a string into a valid single-path component."""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip().strip(".")
    return cleaned or fallback


def ensure_directory(path: str | Path) -> Path:
    """Create ``path`` (and parents) if missing; returns it."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def human_size(num_bytes: int | float, suffix: str = "B") -> str:
    """Format a byte count: ``1536 -> '1.5 KB'``."""
    value = float(num_bytes)
    for unit in ("", "K", "M", "G", "T", "P"):
        if value < 1024 or unit == "P":
            if unit:
                return f"{value:.1f} {unit}{suffix}"
            return f"{int(value)} {suffix}"
        value /= 1024
    return f"{value:.1f} P{suffix}"


def detect_encoding(path: str | Path, default: str = "utf-8") -> str:
    """Heuristic encoding detection: BOMs first, then the given default."""
    raw = Path(path).read_bytes()[:4]
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe\x00\x00"):
        return "utf-32-le"
    if raw.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32-be"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"
    return default
