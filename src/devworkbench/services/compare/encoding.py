"""File decoding — UTF-8 first, BOM aware, with graceful fallbacks.

Huge-file strategy: instead of loading a multi-hundred-MB file into memory as
one giant string, we decode line by line from the raw bytes (UTF-8 is
self-synchronizing, so decoding per line is safe). If even the byte stream is
too large to hold, ``read_lines`` streams from the file handle so memory use
stays proportional to the number of lines, not the file size.
"""

from __future__ import annotations

import codecs
import os
from pathlib import Path
from typing import Iterator

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

# Fallback order matters: latin-1 must precede utf-16. Any byte pair is a
# valid utf-16 code unit, so an *even-length* latin-1 file decodes as utf-16
# without error (silent mojibake); real utf-16 files carry BOMs, which
# detect_bom catches before this chain runs.
_ENCODING_FALLBACKS = ("utf-8", "latin-1", "utf-16")

# Files above this size are streamed line-by-line instead of being loaded
# whole, keeping memory proportional to the number of lines read rather than
# the file size. 8 MB is large enough that decoding overhead per line is
# negligible while still capping a 1 GB input at ~8 MB of transient bytes.
_STREAM_THRESHOLD = 8 << 20  # 8 MiB


class DecodeError(Exception):
    """Raised when a file cannot be decoded by any known encoding."""


def detect_bom(raw: bytes) -> str:
    """Return the encoding implied by a leading BOM, or ``utf-8``."""
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            return encoding
    return "utf-8"


def read_lines(path: str | Path, limit: int | None = None) -> tuple[list[str], str]:
    """Read a text file as lines; returns (lines, encoding_used).

    ``limit`` caps the number of lines read (huge-file truncation handled by
    the engine). Small files (< 8 MB) take the fast whole-file path, which
    allows strict encoding fallback (UTF-8 → UTF-16 → Latin-1); large files
    are streamed so memory stays bounded no matter the file size.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise DecodeError(f"cannot stat {p}: {exc}") from exc
    if size > _STREAM_THRESHOLD:
        return list(stream_lines(p, limit)), _stream_encoding(p)

    raw = _read_bytes(path)
    encoding = detect_bom(raw)
    text = _decode(raw, encoding)
    lines = text.splitlines()
    if limit is not None:
        lines = lines[:limit]
    return lines, encoding


def _stream_encoding(path: Path) -> str:
    """Pick the encoding for a large file before streaming it.

    Mirrors the small-file path: BOM wins, otherwise UTF-8 with a strict
    fallback check against UTF-16/Latin-1 on the head — so a large Latin-1
    file keeps decoding cleanly instead of silently mojibaking.
    """
    with open(path, "rb") as handle:
        head = handle.read(64 << 10)
    if not head:
        return "utf-8"
    encoding = detect_bom(head)
    if encoding != "utf-8":
        return encoding
    for enc in ("utf-8", "latin-1", "utf-16"):
        try:
            head.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"


def stream_lines(path: str | Path, limit: int | None = None) -> Iterator[str]:
    """Stream lines from a file without loading it whole (binary-safe on newlines).

    Decodes with the BOM-aware encoding; malformed bytes in the middle degrade
    to ``errors="replace"`` so a huge file never aborts the comparison.
    ``limit`` stops the iterator after that many lines.
    """
    p = Path(path)
    encoding = _stream_encoding(p)
    with open(p, "rb") as handle:
        reader = codecs.getreader(encoding)(handle, errors="replace")
        count = 0
        for line in reader:
            yield line.rstrip("\r\n")
            count += 1
            if limit is not None and count >= limit:
                return


def _read_bytes(path: str | Path) -> bytes:
    p = Path(path)
    try:
        return p.read_bytes()
    except OSError as exc:
        raise DecodeError(f"cannot read {p}: {exc}") from exc


def _decode(raw: bytes, encoding: str) -> str:
    for enc in (encoding, *[e for e in _ENCODING_FALLBACKS if e != encoding]):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise DecodeError("no encoding matched; file appears binary")


def file_size(path: str | Path) -> int:
    return os.path.getsize(path)


def looks_like_binary_bytes(raw: bytes) -> bool:
    return b"\x00" in raw[:4096]
