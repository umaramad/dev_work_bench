"""Binary file comparison — size, then content hash, then byte scan.

Binary compare is deliberately cheap in the common case: files that differ in
size are "different" immediately; files of equal size are compared by
streaming both in blocks and stopping at the first differing byte. A SHA-256
hash is computed so the UI can cache "identical by hash" verdicts for repeated
folder scans.

``sample`` is a short hex dump of the bytes around the first difference,
enough for the UI to show *where* they diverge without loading either file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from devworkbench.services.compare.models import BinaryDiffResult

_BLOCK = 1 << 20  # 1 MiB read blocks


def compare_binary(left: str | Path, right: str | Path) -> BinaryDiffResult:
    left_p, right_p = Path(left), Path(right)
    result = BinaryDiffResult(
        left_path=str(left_p),
        right_path=str(right_p),
        left_size=left_p.stat().st_size,
        right_size=right_p.stat().st_size,
    )
    if result.left_size != result.right_size:
        result.identical = False
        return result

    left_hash = hashlib.sha256()
    right_hash = hashlib.sha256()
    first_offset = -1
    sample_start = -1

    with open(left_p, "rb") as lh, open(right_p, "rb") as rh:
        offset = 0
        while True:
            left_block = lh.read(_BLOCK)
            right_block = rh.read(_BLOCK)
            if not left_block and not right_block:
                break
            left_hash.update(left_block)
            right_hash.update(right_block)
            if first_offset == -1 and left_block != right_block:
                first_offset = offset + _first_difference(left_block, right_block)
                sample_start = max(0, first_offset - 8)
                result.sample = _hex_sample(left_p, right_p, sample_start)
                result.identical = False

            offset += len(left_block)
            if not left_block:
                break

    result.left_hash = left_hash.hexdigest()
    result.right_hash = right_hash.hexdigest()
    if result.left_hash != result.right_hash and first_offset == -1:
        # Equal-size, equal-blocks read but different tails (can't happen
        # given the loop above, but keep the invariant explicit).
        result.identical = False
    if first_offset == -1:
        result.identical = result.left_hash == result.right_hash
    result.first_difference_offset = first_offset
    return result


def _first_difference(left: bytes, right: bytes) -> int:
    length = min(len(left), len(right))
    for i in range(length):
        if left[i] != right[i]:
            return i
    return length


def _hex_sample(left_p: Path, right_p: Path, start: int) -> str:
    """Hex dump of 16 bytes starting at ``start`` from both files."""
    def _dump(path: Path) -> str:
        with open(path, "rb") as handle:
            handle.seek(start)
            chunk = handle.read(16)
        return " ".join(f"{byte:02x}" for byte in chunk)

    try:
        return f"{start:#x}:  {_dump(left_p)}  vs  {_dump(right_p)}"
    except OSError:
        return ""
