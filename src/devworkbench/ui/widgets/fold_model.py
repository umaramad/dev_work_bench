"""Pure fold (collapsible-section) logic for the diff viewer.

Folding hides *unchanged* runs (lines whose state is ``""`` or ``header``) so
a 100K-line file collapses to its interesting parts. A fold ``(start, end)``
keeps the *header* line (``start``) visible and hides ``start+1 … end-1``.

The mapping functions are pure and O(folds) per call — folds are few (one per
unchanged stretch), so even a huge file navigates instantly.
"""

from __future__ import annotations

from dataclasses import dataclass

FOLD_MIN_RUN = 8  # a run must be at least this long to be collapsible


@dataclass(frozen=True)
class Fold:
    start: int  # inclusive, the header line (stays visible)
    end: int  # exclusive


class FoldModel:
    """Tracks foldable runs and active folds for one pane."""

    def __init__(self) -> None:
        self._num_lines = 0
        self._foldables: list[Fold] = []
        self._active: dict[int, Fold] = {}

    # -- content -----------------------------------------------------------

    def reset(self, num_lines: int, states: list[str]) -> None:
        """Recompute foldable runs from line states (call on new content)."""
        self._num_lines = num_lines
        self._foldables = []
        self._active = {}
        run_start = None
        for index in range(num_lines):
            foldable = states[index] in ("", "header") if index < len(states) else True
            if foldable and run_start is None:
                run_start = index
            elif not foldable and run_start is not None:
                self._add_run(run_start, index)
                run_start = None
        if run_start is not None:
            self._add_run(run_start, num_lines)

    def _add_run(self, start: int, end: int) -> None:
        if end - start >= FOLD_MIN_RUN:
            self._foldables.append(Fold(start, end))

    # -- query ---------------------------------------------------------------

    @property
    def foldables(self) -> list[Fold]:
        return list(self._foldables)

    def foldable_at(self, line: int) -> Fold | None:
        """The foldable run whose header is exactly ``line`` (if any)."""
        for fold in self._foldables:
            if fold.start == line:
                return fold
        return None

    def is_folded(self, line: int) -> bool:
        return line in self._active

    @property
    def folded_count(self) -> int:
        return len(self._active)

    @property
    def hidden_count(self) -> int:
        return sum(fold.end - fold.start - 1 for fold in self._active.values())

    @property
    def visible_count(self) -> int:
        return self._num_lines - self.hidden_count

    def sorted_active(self) -> list[Fold]:
        return sorted(self._active.values(), key=lambda f: f.start)

    # -- toggling ---------------------------------------------------------------

    def toggle(self, line: int) -> bool:
        """Fold/unfold the run headed at ``line``; returns the new state."""
        if line in self._active:
            del self._active[line]
            return False
        fold = self.foldable_at(line)
        if fold is None:
            return False
        self._active[line] = fold
        return True

    def unfold_containing(self, line: int) -> None:
        """Unfold any active fold whose hidden span contains ``line``."""
        for key, fold in list(self._active.items()):
            if fold.start < line < fold.end:
                del self._active[key]

    def unfold_all(self) -> None:
        self._active.clear()

    # -- mapping (logical line <-> visual position) ------------------------------

    def hidden_before(self, index: int) -> int:
        """Number of hidden lines with visual position before ``index``."""
        hidden = 0
        for fold in self._active.values():
            if fold.start < index:
                hidden += min(index, fold.end) - fold.start - 1
        return hidden

    def visual_of(self, index: int) -> int:
        return index - self.hidden_before(index)

    def logical_of(self, visual: int) -> int:
        """First visible logical line at visual position ``visual``."""
        hidden = 0
        for fold in sorted(self._active.values(), key=lambda f: f.start):
            header_visual = fold.start - hidden
            if visual < header_visual:
                return visual + hidden
            # Header line itself occupies ``header_visual``; anything at or
            # beyond the collapsed span moves past this fold.
            if visual == header_visual:
                return fold.start
            hidden += fold.end - fold.start - 1
        return visual + hidden
