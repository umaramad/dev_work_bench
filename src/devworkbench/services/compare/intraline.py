"""Intra-line diffing — word-level by default, character-level on demand.

When a line pair is marked "changed" (delete+insert), the engine re-diffs the
*two* lines against each other to highlight exactly which words (or characters)
differ. This mirrors the "fine detail" mode of professional diff tools.

Both levels reuse the same Myers engine from ``myers.py`` — the word tokens
(or characters) are just another pair of sequences, so the algorithm and the
difflib fallback apply unchanged.
"""

from __future__ import annotations

from devworkbench.services.compare.languages import tokenize_line
from devworkbench.services.compare.models import IntraLineSegments
from devworkbench.services.compare.myers import diff_sequences

# Word/char diff on very long lines (minified bundles, base64 blobs) is
# O(tokens²) at best — cap it so a single 100 KB line cannot stall the worker.
_MAX_INTRALINE_CHARS = 8_000


def word_diff(left: str, right: str, kind: str) -> IntraLineSegments:
    """Diff two lines at the word level; returns highlight segments.

    Whitespace tokens are tagged ``equal`` when the surrounding words match,
    so spacing changes do not flood the highlight.
    """
    left_tokens = tokenize_line(left, kind)
    right_tokens = tokenize_line(right, kind)
    ops = diff_sequences(left_tokens, right_tokens, engine="auto")
    return _segments_from_tokens(left_tokens, right_tokens, ops)


def char_diff(left: str, right: str) -> IntraLineSegments:
    """Diff two lines character by character (single-character tokens)."""
    ops = diff_sequences(list(left), list(right), engine="auto")
    return _segments_from_tokens(list(left), list(right), ops)


def _segments_from_tokens(left: list[str], right: list[str], ops) -> IntraLineSegments:
    left_segments: list[tuple[str, str]] = []
    right_segments: list[tuple[str, str]] = []
    li = ri = 0
    for op in ops:
        kind = op.kind.value  # equal | delete | insert
        if kind == "equal":
            left_segments.append(("equal", left[op.left_index]))
            right_segments.append(("equal", right[op.right_index]))
            li, ri = op.left_index + 1, op.right_index + 1
        elif kind == "delete":
            left_segments.append(("removed", left[op.left_index]))
            li = op.left_index + 1
        else:  # insert
            right_segments.append(("added", right[op.right_index]))
            ri = op.right_index + 1
    return IntraLineSegments(left=left_segments, right=right_segments)


def intraline_for(
    left: str,
    right: str,
    kind: str,
    word_level: bool,
    char_level: bool,
) -> IntraLineSegments:
    """Choose the finest detail level for a changed line pair.

    Word level is preferred; when the word-level diff is degenerate (every
    word differs, i.e. the lines share no words) and ``char_level`` is on,
    fall back to a character diff so the user still sees the minimal change.
    """
    if not word_level and not char_level:
        return IntraLineSegments(
            left=[("removed", left)] if left else [],
            right=[("added", right)] if right else [],
        )
    # Degenerate-cost guard: beyond the cap, report the whole line as changed
    # rather than attempting an intra-line diff on thousands of tokens.
    if len(left) + len(right) > _MAX_INTRALINE_CHARS:
        return IntraLineSegments(
            left=[("removed", left)] if left else [],
            right=[("added", right)] if right else [],
        )
    if word_level:
        segments = word_diff(left, right, kind)
        has_match = any(state == "equal" for state, _ in segments.left) or any(
            state == "equal" for state, _ in segments.right
        )
        if has_match or not char_level:
            return segments
    return char_diff(left, right)
