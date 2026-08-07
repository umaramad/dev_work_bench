"""Comparison engine facade — the single entry point for all comparisons.

``CompareEngine`` wires the whole pipeline:

1. **Kind detection** — from extension and content (``detect_kind``).
2. **Decode** — UTF-8-first with BOM awareness; binary detection.
3. **Canonicalize** — JSON/XML/YAML/Properties parse + re-emit (falls back to
   text when parsing fails, so a broken JSON file still shows *something*).
4. **Normalize** — apply ignore options (whitespace / case / comments /
   blank lines) producing comparison keys.
5. **Diff** — Myers with a wall-clock deadline, difflib fallback.
6. **Render** — states per original line, hunks, stats, intra-line detail.

Huge-file strategy: beyond ``max_lines`` the engine stops reading and
produces a *coarse* result (per-line states only, no intra-line work), and
reports ``truncated=True`` so the UI can say so instead of silently giving a
half answer. ``compare_files`` also special-cases binary inputs.

Every public method is pure (no Qt, no I/O beyond reading the inputs), so it
runs identically in a worker thread or in tests.
"""

from __future__ import annotations

from pathlib import Path

from devworkbench.services.compare.binary_diff import compare_binary
from devworkbench.services.compare.encoding import (
    DecodeError,
    looks_like_binary_bytes,
    read_lines,
)
from devworkbench.services.compare.folder_diff import compare_folders as _compare_folders
from devworkbench.services.compare.intraline import intraline_for
from devworkbench.services.compare.models import (
    BinaryDiffResult,
    CompareOptions,
    DiffHunk,
    DiffLine,
    DiffResult,
    DiffStats,
    FolderDiffResult,
    LineOp,
    OpKind,
    detect_kind,
)
from devworkbench.services.compare.myers import diff_sequences
from devworkbench.services.compare.normalize import build_keys
from devworkbench.services.compare.structured import canonicalize


class CompareEngine:
    """High-level comparison operations (text, files, folders, binary)."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compare_texts(
        self,
        left: str,
        right: str,
        kind: str = "text",
        options: CompareOptions | None = None,
    ) -> DiffResult:
        """Compare two in-memory strings; ``kind`` overrides auto-detection."""
        options = (options or CompareOptions()).normalize()
        left_lines = left.splitlines()
        right_lines = right.splitlines()

        # Canonicalize structured kinds *before* splitting, so semantic
        # equality (key order, whitespace) is diffed, not the raw bytes.
        if kind in ("json", "xml", "yaml", "properties"):
            left_c = canonicalize(kind, left)
            right_c = canonicalize(kind, right)
            if left_c.parsed and right_c.parsed:
                left_lines = left_c.text.splitlines()
                right_lines = right_c.text.splitlines()

        truncated = False
        if max(len(left_lines), len(right_lines)) > options.max_lines:
            truncated = True
            left_lines = left_lines[: options.max_lines]
            right_lines = right_lines[: options.max_lines]

        return self._diff_lines(
            left_lines,
            right_lines,
            kind=kind,
            options=options,
            left_path="",
            right_path="",
            encoding="utf-8",
            truncated=truncated,
        )

    def compare_files(
        self,
        left_path: str,
        right_path: str,
        options: CompareOptions | None = None,
    ) -> DiffResult:
        """Compare two files; auto-detects kind and binary-ness."""
        options = (options or CompareOptions()).normalize()
        left_p, right_p = Path(left_path), Path(right_path)

        # Binary short-circuit: peek the first block of both files.
        left_head = _head(left_p)
        right_head = _head(right_p)
        if looks_like_binary_bytes(left_head) or looks_like_binary_bytes(right_head):
            return self._binary_as_diff(left_path, right_path)

        kind = detect_kind(left_path, left_head[:4096].decode("utf-8", "replace"))
        encoding = "utf-8"
        try:
            left_lines, encoding = read_lines(left_p, limit=options.max_lines + 1)
        except DecodeError:
            return self._binary_as_diff(left_path, right_path)
        try:
            right_lines, _ = read_lines(right_p, limit=options.max_lines + 1)
        except DecodeError:
            return self._binary_as_diff(left_path, right_path)

        # Exact truncation: read one extra line above the limit; if we got it,
        # the file has more lines than we're willing to diff. (Memory is still
        # bounded — streaming reads stop at limit + 1 lines regardless of file
        # size.)
        truncated = len(left_lines) > options.max_lines or len(right_lines) > options.max_lines
        if truncated:
            left_lines = left_lines[: options.max_lines]
            right_lines = right_lines[: options.max_lines]
        return self._diff_lines(
            left_lines,
            right_lines,
            kind=kind,
            options=options,
            left_path=str(left_p),
            right_path=str(right_p),
            encoding=encoding,
            truncated=truncated,
        )

    def compare_folders(
        self,
        left: str,
        right: str,
        options: CompareOptions | None = None,
    ) -> FolderDiffResult:
        return _compare_folders(left, right, options)

    def compare_binary(self, left: str, right: str) -> BinaryDiffResult:
        return compare_binary(left, right)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _diff_lines(
        self,
        left_lines: list[str],
        right_lines: list[str],
        kind: str,
        options: CompareOptions,
        left_path: str,
        right_path: str,
        encoding: str,
        truncated: bool,
    ) -> DiffResult:
        result = DiffResult(
            kind=kind,
            left_path=left_path,
            right_path=right_path,
            encoding=encoding,
            truncated=truncated,
            left_lines=left_lines,
            right_lines=right_lines,
        )

        keep_blanks = not options.ignore_blank_lines
        left_keys, left_orig = build_keys(left_lines, kind, options, keep_blanks)
        right_keys, right_orig = build_keys(right_lines, kind, options, keep_blanks)

        deadline = None
        if options.timeout_seconds > 0:
            import time

            deadline = time.monotonic() + options.timeout_seconds
        ops = diff_sequences(left_keys, right_keys, engine=options.engine, deadline=deadline)
        result.ops = ops

        self._render(result, left_lines, right_lines, left_orig, right_orig, ops, kind, options)
        if not truncated and left_keys == right_keys:
            result.identical = True
        if truncated:
            result.message = (
                f"Inputs exceed the {options.max_lines:,}-line limit; "
                "showing a coarse comparison."
            )
        return result

    def _render(
        self,
        result: DiffResult,
        left_lines: list[str],
        right_lines: list[str],
        left_orig: list[int],
        right_orig: list[int],
        ops: list[LineOp],
        kind: str,
        options: CompareOptions,
    ) -> None:
        """Fill states, hunks and stats from the op list."""
        left_states = [""] * len(left_lines)
        right_states = [""] * len(right_lines)
        added = removed = 0
        change_indices: list[int] = []

        for index, op in enumerate(ops):
            if op.kind is OpKind.EQUAL:
                continue
            change_indices.append(index)
            if op.kind is OpKind.DELETE:
                removed += 1
                orig = left_orig[op.left_index]
                left_states[orig] = "removed"
            else:  # INSERT
                added += 1
                orig = right_orig[op.right_index]
                right_states[orig] = "added"

        # Pair adjacent delete+insert ops into "changed" lines: a delete at
        # (li, ri) immediately followed by an insert at (li+1, ri) replaces
        # one left line with one right line.
        changed_pairs: list[tuple[int, int]] = []
        for index, op in enumerate(ops):
            if op.kind is OpKind.DELETE and index + 1 < len(ops):
                following = ops[index + 1]
                if following.kind is OpKind.INSERT and following.right_index == op.right_index:
                    changed_pairs.append((op.left_index, following.right_index))

        result.left_states = left_states
        result.right_states = right_states

        # Hunks: group change ops whose gaps are within the context window,
        # then expand each group by ``context_lines`` equal ops on both sides.
        ctx = options.context_lines
        hunks: list[DiffHunk] = []
        groups: list[list[int]] = []
        for index in change_indices:
            if groups and index - groups[-1][-1] - 1 <= ctx * 2:
                groups[-1].append(index)
            else:
                groups.append([index])
        for group in groups:
            start = max(0, group[0] - ctx)
            end = min(len(ops) - 1, group[-1] + ctx)
            slice_ops = ops[start : end + 1]
            header = _hunk_header(slice_ops)
            hunk = DiffHunk(header=header)
            for op in slice_ops:
                if op.kind is OpKind.EQUAL:
                    hunk.lines.append(
                        DiffLine(
                            "",
                            left_orig[op.left_index],
                            right_orig[op.right_index],
                            left_lines[left_orig[op.left_index]],
                        )
                    )
                elif op.kind is OpKind.DELETE:
                    hunk.lines.append(
                        DiffLine("removed", left_orig[op.left_index], -1, left_lines[left_orig[op.left_index]])
                    )
                else:
                    hunk.lines.append(
                        DiffLine("added", -1, right_orig[op.right_index], right_lines[right_orig[op.right_index]])
                    )
            hunks.append(hunk)

        result.hunks = hunks
        result.stats = DiffStats(
            added=added, removed=removed, changed=len(changed_pairs), hunks=len(hunks)
        )

        # Inline (unified) rendering rows: equal lines once, then removed
        # lines immediately followed by their added counterparts.
        inline: list[tuple[str, str]] = []
        for op in ops:
            if op.kind is OpKind.EQUAL:
                inline.append((left_lines[left_orig[op.left_index]], ""))
            elif op.kind is OpKind.DELETE:
                inline.append((left_lines[left_orig[op.left_index]], "removed"))
            else:
                inline.append((right_lines[right_orig[op.right_index]], "added"))
        result.inline_lines = inline

        # Intra-line detail for paired change lines.
        if (options.word_level or options.char_level) and changed_pairs:
            for li, ri in changed_pairs:
                left_index = left_orig[li]
                right_index = right_orig[ri]
                segments = intraline_for(
                    left_lines[left_index],
                    right_lines[right_index],
                    kind,
                    options.word_level,
                    options.char_level,
                )
                result.intraline[(left_index, right_index)] = segments

    def _binary_as_diff(self, left_path: str, right_path: str) -> DiffResult:
        binary = compare_binary(left_path, right_path)
        result = DiffResult(
            kind="binary",
            left_path=binary.left_path,
            right_path=binary.right_path,
            identical=binary.identical,
        )
        if binary.identical:
            result.message = (
                f"Identical binary files ({binary.left_size:,} bytes, "
                f"sha256 {binary.left_hash[:12]}…)"
            )
        else:
            result.message = (
                f"Binary files differ — {binary.left_size:,} vs {binary.right_size:,} "
                f"bytes"
            )
            if binary.first_difference_offset >= 0:
                result.message += f" — first difference at {binary.first_difference_offset:#x}"
            if binary.sample:
                result.message += f"\n{binary.sample}"
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _head(path: Path) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read(4096)
    except OSError:
        return b""


def _hunk_header(slice_ops: list[LineOp]) -> str:
    """Unified-style header for a hunk: ``@@ -a,b +c,d @@``."""
    first = next((op for op in slice_ops if op.kind is not OpKind.EQUAL), None)
    if first is None:
        return "@@ @@"
    left_start = first.left_index
    right_start = first.right_index
    left_count = sum(1 for op in slice_ops if op.kind in (OpKind.DELETE, OpKind.EQUAL))
    right_count = sum(1 for op in slice_ops if op.kind in (OpKind.INSERT, OpKind.EQUAL))
    return f"@@ -{left_start + 1},{left_count} +{right_start + 1},{right_count} @@"


def compare_texts(
    left: str,
    right: str,
    kind: str = "text",
    options: CompareOptions | None = None,
) -> DiffResult:
    """Module-level convenience wrapper around ``CompareEngine``."""
    return CompareEngine().compare_texts(left, right, kind, options)


def compare_files(
    left_path: str,
    right_path: str,
    options: CompareOptions | None = None,
) -> DiffResult:
    return CompareEngine().compare_files(left_path, right_path, options)


def compare_folders(
    left: str,
    right: str,
    options: CompareOptions | None = None,
) -> FolderDiffResult:
    return CompareEngine().compare_folders(left, right, options)
