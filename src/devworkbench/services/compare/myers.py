"""Myers O(ND) difference algorithm — linear-space divide and conquer.

Implementation follows Myers' "An O(ND) Difference Algorithm and Its
Variations": a dual-direction (forward + backward) search finds the middle
snake, then the problem is split around it and solved recursively, so memory
stays O(N+M) regardless of edit distance — the property that makes huge files
feasible.

Robustness notes:

- The middle-snake search can report an overlap at an *off-grid* corner point
  (a degenerate snake at (0,0) or (N,M)). Recursing on that would split the
  problem into an identical subproblem and loop forever. The recursion guard
  therefore requires the split to make strict progress; otherwise the search
  continues to the next overlap.
- A wall-clock deadline makes the algorithm degrade gracefully: on timeout the
  caller falls back to ``difflib.SequenceMatcher``.

Edits are returned as ``LineOp`` (EQUAL / DELETE / INSERT) referencing
positions in the input sequences, so the caller can map them back to original
line numbers.
"""

from __future__ import annotations

import difflib
import time

from devworkbench.services.compare.models import LineOp, OpKind


class DiffTimeoutError(Exception):
    """Raised when the Myers search exceeds its time budget."""


_MISSING = -10**9  # sentinel for "diagonal never reached" (dict-based V arrays)


def diff_sequences(
    a: list,
    b: list,
    engine: str = "auto",
    deadline: float | None = None,
) -> list[LineOp]:
    """Diff two sequences; returns a list of LineOp (EQUAL/DELETE/INSERT).

    ``engine``: ``myers`` (never falls back), ``difflib`` (always), or
    ``auto`` (Myers with difflib fallback on timeout/error).
    """
    if not a:
        return [LineOp(OpKind.INSERT, 0, j) for j in range(len(b))]
    if not b:
        return [LineOp(OpKind.DELETE, i, 0) for i in range(len(a))]
    if engine == "difflib":
        return _edits_from_difflib(a, b)
    try:
        ops: list[LineOp] = []
        _recover(a, b, 0, len(a), 0, len(b), ops, deadline)
        return ops
    except DiffTimeoutError:
        if engine == "myers":
            raise
        return _edits_from_difflib(a, b)
    except Exception:  # noqa: BLE001 — never let the engine crash the worker
        if engine == "myers":
            raise
        return _edits_from_difflib(a, b)


# ---------------------------------------------------------------------------
# Linear-space divide and conquer
# ---------------------------------------------------------------------------


def _recover(
    a,
    b,
    a0: int,
    a1: int,
    b0: int,
    b1: int,
    ops: list[LineOp],
    deadline: float | None,
) -> None:
    """Append ops diffing a[a0:a1] vs b[b0:b1] (relative indices)."""
    # Strip the common prefix and suffix first: pure extension regions (one
    # sequence a prefix/suffix of the other) would otherwise only ever offer
    # degenerate corner overlaps, forcing an O((N+M)²) search before the
    # greedy fallback. Trimming reduces them to trivial insert/delete.
    while a0 < a1 and b0 < b1 and a[a0] == b[b0]:
        ops.append(LineOp(OpKind.EQUAL, a0, b0))
        a0 += 1
        b0 += 1
    suffix: list[tuple[int, int]] = []
    while a1 > a0 and b1 > b0 and a[a1 - 1] == b[b1 - 1]:
        a1 -= 1
        b1 -= 1
        suffix.append((a1, b1))

    n = a1 - a0
    m = b1 - b0
    if n == 0 and m == 0:
        pass
    elif n == 0:  # everything is an insertion
        for j in range(b0, b1):
            ops.append(LineOp(OpKind.INSERT, a0, j))
    elif m == 0:  # everything is a deletion
        for i in range(a0, a1):
            ops.append(LineOp(OpKind.DELETE, i, b0))
    else:
        _middle_snake_search(a, b, a0, a1, b0, b1, ops, deadline)

    # Re-emit the stripped common suffix (in document order).
    for line_a, line_b in reversed(suffix):
        ops.append(LineOp(OpKind.EQUAL, line_a, line_b))


def _middle_snake_search(
    a,
    b,
    a0: int,
    a1: int,
    b0: int,
    b1: int,
    ops: list[LineOp],
    deadline: float | None,
) -> None:
    """Linear-space middle-snake divide & conquer for a prefix/suffix-free
    region; appends ops in order. Falls back to exact greedy on degenerate
    regions (correct, bounded, and rare after prefix/suffix stripping)."""
    n = a1 - a0
    m = b1 - b0
    if n == 0 and m == 0:
        return
    if n == 0:  # everything is an insertion
        for j in range(b0, b1):
            ops.append(LineOp(OpKind.INSERT, a0, j))
        return
    if m == 0:  # everything is a deletion
        for i in range(a0, a1):
            ops.append(LineOp(OpKind.DELETE, i, b0))
        return

    length = n + m
    max_h = length // 2 + (length % 2 != 0)
    delta = n - m
    forward: dict[int, int] = {1: 0}
    backward: dict[int, int] = {1: 0}

    for h in range(max_h + 1):
        if deadline is not None and h % 4 == 0 and time.monotonic() > deadline:
            raise DiffTimeoutError("diff exceeded its time budget")

        # ---- forward pass (paths from the top-left) ---------------------
        for k in range(-h, h + 1, 2):
            if k == -h or (k != h and forward.get(k - 1, _MISSING) < forward.get(k + 1, _MISSING)):
                x = forward.get(k + 1, 0)
            else:
                x = forward.get(k - 1, 0) + 1
            y = x - k
            s, t = x, y
            while x < n and y < m and a[a0 + x] == b[b0 + y]:
                x += 1
                y += 1
            forward[k] = x
            z = delta - k
            if (
                length % 2 == 1
                and -(h - 1) <= z <= h - 1
                and forward[k] + backward.get(z, _MISSING) >= n
            ):
                # Overlap found in the forward pass: middle snake is
                # (s, t) -> (x, y), total path length D = 2h - 1.
                if _proper_split(s, t, x, y, n, m) and (2 * h - 1 > 1 or (s != x and t != y)):
                    _recover(a, b, a0, a0 + s, b0, b0 + t, ops, deadline)
                    _emit_snake(ops, a0 + s, b0 + t, a0 + x, b0 + y)
                    _recover(a, b, a0 + x, a1, b0 + y, b1, ops, deadline)
                    return

        # ---- backward pass (paths from the bottom-right) -------------------
        for k in range(-h, h + 1, 2):
            if k == -h or (k != h and backward.get(k - 1, _MISSING) < backward.get(k + 1, _MISSING)):
                x = backward.get(k + 1, 0)
            else:
                x = backward.get(k - 1, 0) + 1
            y = x - k
            s, t = x, y
            while x < n and y < m and a[a1 - 1 - x] == b[b1 - 1 - y]:
                x += 1
                y += 1
            backward[k] = x
            z = delta - k
            if (
                length % 2 == 0
                and -h <= z <= h
                and backward[k] + forward.get(z, _MISSING) >= n
            ):
                # Overlap found in the backward pass: the snake runs from
                # (n - x, m - y) to (n - s, m - t); total path length D = 2h.
                u, v = n - x, m - y
                u2, v2 = n - s, m - t
                if _proper_split(u, v, u2, v2, n, m) and (2 * h > 1 or (u != u2 and v != v2)):
                    _recover(a, b, a0, a0 + u, b0, b0 + v, ops, deadline)
                    _emit_snake(ops, a0 + u, b0 + v, a0 + u2, b0 + v2)
                    _recover(a, b, a0 + u2, a1, b0 + v2, b1, ops, deadline)
                    return

    # No valid middle snake was found (degenerate corner cases). Emit the
    # greedy traceback for this region — exact, minimal, always terminates.
    _edits_greedy_region(a, b, a0, a1, b0, b1, ops)


def _proper_split(x1: int, y1: int, x2: int, y2: int, n: int, m: int) -> bool:
    """The split [0:x]x[0:y] / [u:N]x[v:M] must strictly shrink the region.

    Guards against the degenerate off-grid overlap: when the overlap point
    sits on the boundary (0,0) or (N,M), one of the two halves equals the
    whole problem, which would recurse forever.
    """
    if x1 < 0 or y1 < 0 or x2 < 0 or y2 < 0:
        return False
    if x1 > n or y1 > m or x2 > n or y2 > m:
        return False
    first_smaller = x1 < n or y1 < m
    second_smaller = x2 > 0 or y2 > 0
    return first_smaller and second_smaller


def _emit_snake(ops: list[LineOp], x0: int, y0: int, x1: int, y1: int) -> None:
    """Append EQUAL ops for the diagonal run (x0,y0) -> (x1,y1)."""
    x, y = x0, y0
    while x < x1 and y < y1:
        ops.append(LineOp(OpKind.EQUAL, x, y))
        x += 1
        y += 1


# ---------------------------------------------------------------------------
# Greedy traceback (exact Myers; used as the linear-space base case)
# ---------------------------------------------------------------------------


def _edits_greedy_region(
    a,
    b,
    a0: int,
    a1: int,
    b0: int,
    b1: int,
    ops: list[LineOp],
) -> None:
    """Exact greedy Myers with traceback for a bounded region (not linear
    space, but always correct and terminating — ideal for the small corner
    regions the linear-space split declines)."""
    sub_a = list(a[a0:a1])
    sub_b = list(b[b0:b1])
    ops.extend(_edits_greedy(sub_a, sub_b, a0, b0))


def _edits_greedy(a: list, b: list, a_offset: int = 0, b_offset: int = 0) -> list[LineOp]:
    """Greedy Myers with full traceback; exact shortest edit script.

    Memory is O(D^2) in the edit distance D; used only for the small
    degenerate regions the linear-space algorithm defers.
    """
    n, m = len(a), len(b)
    max_d = n + m
    v: dict[int, int] = {1: 0}
    trace: list[dict[int, int]] = []
    done_d = -1
    done_k = 0

    for d in range(max_d + 1):
        trace.append(dict(v))
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v.get(k - 1, _MISSING) < v.get(k + 1, _MISSING)):
                x = v.get(k + 1, 0)
            else:
                x = v.get(k - 1, 0) + 1
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            v[k] = x
            if x >= n and y >= m:
                done_d, done_k = d, k
                break
        if done_d >= 0:
            break

    # Backtrack from (n, m) through the trace.
    ops: list[LineOp] = []
    x, y = n, m
    for d in range(done_d, -1, -1):
        v_d = trace[d]
        k = x - y
        if k == -d or (k != d and v_d.get(k - 1, _MISSING) < v_d.get(k + 1, _MISSING)):
            prev_k = k + 1
        else:
            prev_k = k - 1
        prev_x = v_d.get(prev_k, 0)
        prev_y = prev_x - prev_k
        while x > prev_x and y > prev_y:
            ops.append(LineOp(OpKind.EQUAL, a_offset + x - 1, b_offset + y - 1))
            x -= 1
            y -= 1
        if d > 0:
            if x == prev_x:
                ops.append(LineOp(OpKind.INSERT, a_offset + prev_x, b_offset + prev_y))
            else:
                ops.append(LineOp(OpKind.DELETE, a_offset + prev_x, b_offset + prev_y))
            x, y = prev_x, prev_y
    ops.reverse()
    return ops


# ---------------------------------------------------------------------------
# difflib fallback
# ---------------------------------------------------------------------------


def _edits_from_difflib(a, b) -> list[LineOp]:
    ops: list[LineOp] = []
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=True)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            for i in range(i1, i2):
                ops.append(LineOp(OpKind.DELETE, i, j1))
        if tag in ("insert", "replace"):
            for j in range(j1, j2):
                ops.append(LineOp(OpKind.INSERT, i1, j))
        if tag == "equal":
            for k in range(i2 - i1):
                ops.append(LineOp(OpKind.EQUAL, i1 + k, j1 + k))
    return ops
