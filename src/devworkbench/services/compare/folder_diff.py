"""Folder comparison — recursive walk producing one entry per relative path.

Semantics:

- Files present on *both* sides are compared by size then streaming SHA-256:
  equal → ``identical`` (with ``time_differs`` when mtimes disagree), else
  ``modified``.
- A path on only one side is ``only_left`` / ``only_right`` (the view labels
  them *deleted* / *added* relative to the right side).
- With ``detect_moves``, same-content single-side files are paired into
  ``renamed`` (same parent directory) or ``moved`` rows carrying both paths.
- Directories whose name matches ``ignore_dirs`` are pruned entirely (the
  classic .git / .idea / target / build / node_modules set by default) and
  counted in ``skipped_dirs``.
- Symlinks are followed by default (``CompareOptions.follow_symlinks``); a
  visited-set of resolved directories prevents ancestor-link cycles.
- ``max_entries`` guards pathological trees; the result stays valid with
  ``truncated=True``.

The walk is deterministic (sorted paths) so repeated runs yield stable tables
and stable diffs for tests.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from devworkbench.services.compare.models import (
    CompareOptions,
    FolderDiffEntry,
    FolderDiffResult,
)

_BLOCK = 1 << 20
_MAX_ENTRIES_DEFAULT = 200_000


def compare_folders(
    left: str | Path,
    right: str | Path,
    options: CompareOptions | None = None,
    max_entries: int = _MAX_ENTRIES_DEFAULT,
) -> FolderDiffResult:
    left_p, right_p = Path(left), Path(right)
    options = (options or CompareOptions()).normalize()
    if not left_p.exists():
        raise FileNotFoundError(f"Left folder does not exist: {left_p}")
    if not right_p.exists():
        raise FileNotFoundError(f"Right folder does not exist: {right_p}")
    result = FolderDiffResult(left=str(left_p), right=str(right_p))

    left_map, left_skipped = _index(left_p, options.follow_symlinks, options.ignore_dirs)
    right_map, right_skipped = _index(right_p, options.follow_symlinks, options.ignore_dirs)
    result.skipped_dirs = left_skipped + right_skipped

    all_relative = sorted(set(left_map) | set(right_map))
    truncated = False

    for relative in all_relative:
        if len(result.entries) >= max_entries:
            truncated = True
            break
        left_entry = left_map.get(relative)
        right_entry = right_map.get(relative)

        if left_entry is None:
            result.entries.append(
                FolderDiffEntry(
                    relative=relative,
                    kind=_kind_name(right_entry),
                    state="only_right",
                    left_size=-1,
                    right_size=right_entry.size if right_entry else -1,
                    mtime_right=right_entry.mtime if right_entry else -1.0,
                )
            )
            continue
        if right_entry is None:
            result.entries.append(
                FolderDiffEntry(
                    relative=relative,
                    kind=_kind_name(left_entry),
                    state="only_left",
                    left_size=left_entry.size,
                    right_size=-1,
                    mtime_left=left_entry.mtime,
                )
            )
            continue

        # Both sides present.
        if left_entry.is_dir or right_entry.is_dir:
            continue  # directories are implicit; only files are listed

        same = left_entry.size == right_entry.size and _hash(left_p / relative) == _hash(right_p / relative)
        result.entries.append(
            FolderDiffEntry(
                relative=relative,
                kind="file",
                state="identical" if same else "modified",
                left_size=left_entry.size,
                right_size=right_entry.size,
                identical_hash=same,
                mtime_left=left_entry.mtime,
                mtime_right=right_entry.mtime,
                time_differs=same and left_entry.mtime != right_entry.mtime,
            )
        )

    if options.detect_moves:
        _detect_moves(result, left_p, right_p)

    # Identical iff there are no differences and the walk wasn't truncated.
    result.identical = not truncated and all(entry.state == "identical" for entry in result.entries)
    return result


def _detect_moves(result: FolderDiffResult, left_p: Path, right_p: Path) -> None:
    """Pair same-content single-side files into moved/renamed rows.

    Only files of *equal size* are candidates (a moved file keeps its size),
    which keeps hashing proportional to real candidates; per-file hashes are
    cached so a file is never read twice.
    """
    left_singles = {e.relative: e for e in result.entries if e.state == "only_left" and e.kind == "file"}
    right_singles = {e.relative: e for e in result.entries if e.state == "only_right" and e.kind == "file"}
    if not left_singles or not right_singles:
        return

    right_by_size: dict[int, list[str]] = defaultdict(list)
    for relative, entry in right_singles.items():
        right_by_size[entry.right_size].append(relative)

    right_hash_cache: dict[str, str] = {}

    def right_hash(relative: str) -> str:
        cached = right_hash_cache.get(relative)
        if cached is None:
            cached = _hash(right_p / relative)
            right_hash_cache[relative] = cached
        return cached

    left_hash_cache: dict[str, str] = {}

    def left_hash(relative: str) -> str:
        cached = left_hash_cache.get(relative)
        if cached is None:
            cached = _hash(left_p / relative)
            left_hash_cache[relative] = cached
        return cached

    pairs: list[tuple[str, str]] = []  # (left_rel, right_rel)
    paired_right: set[str] = set()
    for left_rel, left_entry in left_singles.items():
        for right_rel in right_by_size.get(left_entry.left_size, ()):
            if right_rel in paired_right:
                continue
            if left_hash(left_rel) == right_hash(right_rel):
                paired_right.add(right_rel)
                pairs.append((left_rel, right_rel))
                break

    if not pairs:
        return
    right_rel_for = dict(pairs)
    rebuilt: list[FolderDiffEntry] = []
    for entry in result.entries:
        if entry.relative in right_rel_for:
            right_rel = right_rel_for[entry.relative]
            right_entry = right_singles[right_rel]
            same_dir = Path(entry.relative).parent == Path(right_rel).parent
            rebuilt.append(
                FolderDiffEntry(
                    relative=entry.relative,
                    kind="file",
                    state="renamed" if same_dir else "moved",
                    left_size=entry.left_size,
                    right_size=right_entry.right_size,
                    identical_hash=True,
                    mtime_left=entry.mtime_left,
                    mtime_right=right_entry.mtime_right,
                    time_differs=entry.mtime_left != right_entry.mtime_right,
                    pair=right_rel,
                )
            )
        elif entry.relative in paired_right:
            continue  # consumed by the move row above
        else:
            rebuilt.append(entry)
    result.entries = rebuilt


def _kind_name(entry) -> str:
    return "dir" if entry.is_dir else "file"


def _index(root: Path, follow_symlinks: bool, ignore_dirs: tuple[str, ...]):
    """Map relative path → ``_IndexEntry``; returns (mapping, skipped_dirs).

    ``visited`` tracks resolved directory identities so a symlink pointing at
    an ancestor (a common ``..`` layout) cannot make the walk descend forever.
    Directories whose (lowercased) name is in ``ignore_dirs`` are pruned.
    """
    mapping: dict[str, _IndexEntry] = {}
    skipped = 0
    if not root.exists():
        return mapping, skipped
    ignore = {name.lower() for name in ignore_dirs}
    visited: set[str] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            resolved = str(current.resolve())
        except OSError:
            resolved = str(current)
        if resolved in visited:
            continue
        visited.add(resolved)
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for child in children:
            relative = child.relative_to(root).as_posix()
            try:
                is_dir = child.is_dir() if follow_symlinks else child.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if child.name.lower() in ignore:
                    skipped += 1
                    continue
                mapping[relative] = _IndexEntry(True, 0, -1.0)
                if not child.is_symlink() or follow_symlinks:
                    stack.append(child)
            else:
                try:
                    stat = child.stat()
                    size = stat.st_size
                    mtime = stat.st_mtime
                except OSError:
                    size = -1
                    mtime = -1.0
                mapping[relative] = _IndexEntry(False, size, mtime)
    return mapping, skipped


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                block = handle.read(_BLOCK)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


class _IndexEntry:
    __slots__ = ("is_dir", "size", "mtime")

    def __init__(self, is_dir: bool, size: int, mtime: float) -> None:
        self.is_dir = is_dir
        self.size = size
        self.mtime = mtime
