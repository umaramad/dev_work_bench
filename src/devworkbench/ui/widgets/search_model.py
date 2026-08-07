"""Pure search-match logic for the diff viewer.

A query is resolved into a flat list of matches across a pane's lines; the
viewer paints them and navigates with ``next``/``previous``. Plain-text search
(``str.find``) keeps 100K-line scans fast (a few ms in C). Case-insensitive
search uses ``str.lower`` — unlike ``casefold`` it never changes string length
(``ß`` stays one character), so match columns stay valid for ``replace_*``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Match:
    line: int
    col: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.col


def find_matches(lines: list[str], query: str, case_sensitive: bool = True) -> list[Match]:
    """All occurrences of ``query`` across ``lines``, in document order."""
    if not query:
        return []
    matches: list[Match] = []
    if case_sensitive:
        needle = query
        for line_index, line in enumerate(lines):
            col = line.find(needle)
            while col != -1:
                matches.append(Match(line_index, col, col + len(needle)))
                col = line.find(needle, col + 1)
    else:
        needle = query.lower()
        for line_index, line in enumerate(lines):
            lowered = line.lower()
            col = lowered.find(needle)
            while col != -1:
                matches.append(Match(line_index, col, col + len(needle)))
                col = lowered.find(needle, col + 1)
    return matches


def match_count(matches: list[Match]) -> int:
    return len(matches)


def next_index(matches: list[Match], current: int, forward: bool, wrap: bool = True) -> int | None:
    """Index of the next/previous match relative to ``current``; wraps."""
    if not matches:
        return None
    if forward:
        if current < len(matches) - 1:
            return current + 1
        return 0 if wrap else None
    if current > 0:
        return current - 1
    return len(matches) - 1 if wrap else None
