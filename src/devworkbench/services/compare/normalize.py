"""Line normalization — what ``CompareOptions`` mean at the line level.

Normalization is a *sequential* pass: multi-line block comments need state
across lines, and comment markers must not fire inside string literals
(``"http://…"``, ``'it#'``). The result is one comparison key per line plus
a flag saying whether the line is blank (for ``ignore_blank_lines``).

The diff runs on keys; the UI still renders the original text.
"""

from __future__ import annotations

import re

from devworkbench.services.compare.languages import LanguageProfile, profile_for
from devworkbench.services.compare.models import CompareOptions

_ALL_WS = re.compile(r"\s+")


class _CommentStripper:
    """Stateful stripper: removes comments while respecting quotes."""

    def __init__(self, profile: LanguageProfile) -> None:
        self._profile = profile
        self._in_block = False

    def strip(self, line: str) -> str:
        profile = self._profile
        line_comments = profile.line_comment
        block = profile.block_comment

        out: list[str] = []
        i = 0
        length = len(line)
        while i < length:
            if self._in_block and block is not None:
                end = line.find(block[1], i)
                if end == -1:
                    return "".join(out).rstrip()
                i = end + len(block[1])
                self._in_block = False
                continue

            if profile.quote_aware and line[i] in "\"'":
                close = _find_quote_end(line, i)
                if close != -1:
                    out.append(line[i : close + 1])
                    i = close + 1
                    continue

            line_pos = _first_comment_pos(line, i, line_comments)
            block_pos = line.find(block[0], i) if block is not None else -1
            if line_pos != -1 and (block_pos == -1 or line_pos <= block_pos):
                out.append(line[i:line_pos])
                return "".join(out).rstrip()
            if block_pos != -1:
                out.append(line[i:block_pos])
                i = block_pos + len(block[0])
                self._in_block = True
                continue
            out.append(line[i:])
            return "".join(out)
        return "".join(out)


def _find_quote_end(line: str, start: int) -> int:
    """Index of the closing quote (handling backslash escapes) or -1."""
    quote = line[start]
    i = start + 1
    while i < len(line):
        if line[i] == "\\":
            i += 2
            continue
        if line[i] == quote:
            return i
        i += 1
    return -1


def _first_comment_pos(line: str, start: int, markers: tuple[str, ...]) -> int:
    positions = [line.find(marker, start) for marker in markers]
    positions = [pos for pos in positions if pos != -1]
    return min(positions) if positions else -1


class LineNormalizer:
    """Sequential normalizer producing comparison keys for a line stream."""

    def __init__(self, kind: str, options: CompareOptions) -> None:
        self._profile = profile_for(kind)
        self._options = options
        self._stripper = _CommentStripper(self._profile)

    def key(self, line: str) -> str:
        """The comparison key for ``line`` (context depends on prior lines)."""
        text = line
        if self._options.ignore_comments:
            text = self._stripper.strip(text)
        if self._options.ignore_whitespace:
            # "Ignore whitespace" = git -w semantics: whitespace differences
            # anywhere in the line are unimportant, so drop it entirely.
            text = _ALL_WS.sub("", text)
        if self._options.ignore_case:
            text = text.lower()
        return text

    def is_blank(self, line: str) -> bool:
        return not line.strip()

    def reset(self) -> None:
        self._stripper = _CommentStripper(self._profile)


def build_keys(
    lines: list[str],
    kind: str,
    options: CompareOptions,
    keep_blanks: bool,
) -> tuple[list[str], list[int]]:
    """Return (keys, original_indices) for lines that participate in the diff.

    With ``keep_blanks=False``, blank lines are excluded (``ignore_blank_lines``).
    """
    normalizer = LineNormalizer(kind, options)
    keys: list[str] = []
    indices: list[int] = []
    for index, line in enumerate(lines):
        key = normalizer.key(line)
        # Blank lines (and comment-only lines under ignore_comments) drop out
        # of the comparison entirely — they can never be a diff.
        if not keep_blanks and normalizer.is_blank(line):
            continue
        if options.ignore_comments and not key.strip():
            continue
        keys.append(key)
        indices.append(index)
    return keys, indices
