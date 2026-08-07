"""Compare-module models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from devworkbench.models.base import Model

DiffState = Literal["added", "removed", "changed", "context", "header"]


@dataclass
class DiffLine(Model):
    text: str = ""
    state: DiffState = "context"


@dataclass
class DiffHunk(Model):
    header: str = ""
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class DiffFile(Model):
    left_path: str = ""
    right_path: str = ""
    hunks: list[DiffHunk] = field(default_factory=list)


@dataclass
class DiffStats(Model):
    added: int = 0
    removed: int = 0
    changed: int = 0
    hunks: int = 0


@dataclass
class CompareSession(Model):
    left: Path | None = None
    right: Path | None = None
    engine: str = "unified"
    result: DiffFile | None = None
    stats: DiffStats | None = None
