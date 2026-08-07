"""Comparison engine models.

These are the engine's contract: every consumer (workers, UI, tests) works
with these dataclasses and never sees the algorithm internals. ``DiffResult``
carries everything a side-by-side view needs — original lines plus per-line
states — and the raw ops for tooling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompareOptions:
    """Toggles that change *what* is compared, never the algorithms."""

    ignore_whitespace: bool = False  # strip leading/trailing whitespace per line
    ignore_blank_lines: bool = False
    ignore_case: bool = False
    ignore_comments: bool = False
    context_lines: int = 3
    engine: str = "auto"  # "auto" | "myers" | "difflib"
    word_level: bool = True  # intra-line diff for paired "changed" lines
    char_level: bool = False  # fall back to character diff when words mismatch
    timeout_seconds: float = 20.0  # budget before the myers -> difflib fallback
    max_lines: int = 300_000  # per side; beyond this a coarse summary is produced
    follow_symlinks: bool = True
    # Folder comparison.
    ignore_dirs: tuple[str, ...] = (".git", ".idea", "target", "build", "node_modules")
    detect_moves: bool = True  # pair same-content files at different paths

    def normalize(self) -> "CompareOptions":
        """Clamp option values to sane ranges."""
        return CompareOptions(
            ignore_whitespace=self.ignore_whitespace,
            ignore_blank_lines=self.ignore_blank_lines,
            ignore_case=self.ignore_case,
            ignore_comments=self.ignore_comments,
            context_lines=max(0, min(self.context_lines, 50)),
            engine=self.engine if self.engine in ("auto", "myers", "difflib") else "auto",
            word_level=self.word_level,
            char_level=self.char_level,
            timeout_seconds=max(0.1, self.timeout_seconds),
            max_lines=max(10, self.max_lines),
            follow_symlinks=self.follow_symlinks,
            ignore_dirs=tuple(name for name in self.ignore_dirs if name),
            detect_moves=self.detect_moves,
        )


# ---------------------------------------------------------------------------
# Line-level results
# ---------------------------------------------------------------------------


class OpKind(str, Enum):
    EQUAL = "equal"
    DELETE = "delete"
    INSERT = "insert"


@dataclass(frozen=True)
class LineOp:
    """One edit in the diff script.

    ``left_index`` / ``right_index`` reference positions in the *kept* line
    lists (original indices minus ignored blank lines); -1 when not applicable.
    """

    kind: OpKind
    left_index: int
    right_index: int


@dataclass(frozen=True)
class DiffStats:
    added: int = 0
    removed: int = 0
    changed: int = 0
    hunks: int = 0

    @property
    def total(self) -> int:
        return self.added + self.removed + self.changed


@dataclass
class DiffLine:
    state: str  # "", added, removed, changed, header
    left_index: int = -1
    right_index: int = -1
    text: str = ""


@dataclass
class DiffHunk:
    header: str = ""
    lines: list[DiffLine] = field(default_factory=list)


@dataclass(frozen=True)
class IntraLineSegments:
    """Word/character-level segments for one paired 'changed' line."""

    left: list[tuple[str, str]]  # (state: equal|removed, text)
    right: list[tuple[str, str]]  # (state: equal|added, text)


@dataclass
class DiffResult:
    """Full result of comparing two texts/files of any kind."""

    kind: str = "text"  # text|json|xml|yaml|properties|sql|java|python|dart|markdown|binary|folder
    left_path: str = ""
    right_path: str = ""
    encoding: str = "utf-8"
    identical: bool = False
    truncated: bool = False  # inputs exceeded max_lines; coarse summary only
    message: str = ""  # human note (binary summary, truncation, …)

    left_lines: list[str] = field(default_factory=list)
    right_lines: list[str] = field(default_factory=list)
    left_states: list[str] = field(default_factory=list)  # per original line
    right_states: list[str] = field(default_factory=list)

    ops: list[LineOp] = field(default_factory=list)
    hunks: list[DiffHunk] = field(default_factory=list)
    intraline: dict[tuple[int, int], IntraLineSegments] = field(default_factory=dict)
    # Inline (unified) rendering: interleaved (text, state) rows — removed
    # lines immediately followed by their added counterparts.
    inline_lines: list[tuple[str, str]] = field(default_factory=list)
    stats: DiffStats = field(default_factory=DiffStats)

    def set_states(self, left: list[str], right: list[str]) -> None:
        self.left_states = left
        self.right_states = right


# ---------------------------------------------------------------------------
# Binary & folder results
# ---------------------------------------------------------------------------


@dataclass
class BinaryDiffResult:
    left_path: str = ""
    right_path: str = ""
    identical: bool = False
    left_size: int = 0
    right_size: int = 0
    left_hash: str = ""  # sha256
    right_hash: str = ""
    first_difference_offset: int = -1
    sample: str = ""  # hex around the first difference


@dataclass
class FolderDiffEntry:
    """One path pair in a folder comparison.

    ``state`` ∈ {identical, modified, only_left, only_right, moved, renamed}.
    For ``moved``/``renamed`` rows ``relative`` is the *left* path and
    ``pair`` the matching *right* path (same content, different location).
    """

    relative: str
    kind: str  # file | dir
    state: str
    left_size: int = -1
    right_size: int = -1
    identical_hash: bool = True
    mtime_left: float = -1.0  # epoch seconds
    mtime_right: float = -1.0
    time_differs: bool = False  # mtimes differ despite identical content
    pair: str = ""  # counterpart path for moved/renamed rows


@dataclass
class FolderDiffResult:
    left: str = ""
    right: str = ""
    entries: list[FolderDiffEntry] = field(default_factory=list)
    identical: bool = True
    skipped_dirs: int = 0  # directories pruned by the ignore filter

    def count(self, state: str) -> int:
        return sum(1 for entry in self.entries if entry.state == state)

    @property
    def moves(self) -> int:
        return self.count("moved") + self.count("renamed")


# ---------------------------------------------------------------------------
# Kind detection
# ---------------------------------------------------------------------------

_EXTENSION_KINDS: dict[str, str] = {
    ".json": "json",
    ".jsonl": "json",
    ".xml": "xml",
    ".xsd": "xml",
    ".xsl": "xml",
    ".svg": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".properties": "properties",
    ".sql": "sql",
    ".java": "java",
    ".py": "python",
    ".pyw": "python",
    ".dart": "dart",
    ".md": "markdown",
    ".markdown": "markdown",
}

_TEXT_KINDS = {"text", "sql", "java", "python", "dart", "markdown", "json", "xml", "yaml", "properties"}


def detect_kind(path: str | Path | None, sample: str | None = None) -> str:
    """Detect the comparison kind from extension, then content heuristics."""
    if path:
        kind = _EXTENSION_KINDS.get(Path(str(path)).suffix.lower())
        if kind:
            return kind
    if sample is not None:
        head = sample[:4096]
        if "\x00" in head:
            return "binary"
        stripped = head.lstrip()
        if stripped.startswith("{"):
            return "json"
        if stripped.startswith("["):
            return "json"
        if stripped.startswith("<"):
            return "xml"
        if stripped.startswith(("---", "key:", "  ")) and "\n" in head:
            return "yaml"
    return "text"


def is_binary_sample(sample: str | None) -> bool:
    return sample is not None and "\x00" in sample[:4096]
