"""Log Analyzer module models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from devworkbench.models.base import Model


@dataclass
class LogPattern(Model):
    name: str = ""
    regex: str = ""
    compiled: re.Pattern | None = None

    def __post_init__(self) -> None:
        if self.regex and self.compiled is None:
            self.compiled = re.compile(self.regex)


@dataclass
class LogEntry(Model):
    timestamp: str = ""
    level: str = "INFO"       # TRACE | DEBUG | INFO | WARN | ERROR
    source: str = ""
    message: str = ""
    line_number: int = 0


@dataclass
class LogFile(Model):
    path: str = ""
    size: int = 0
    lines: int = 0
    indexed: bool = False


@dataclass
class LogFilter(Model):
    name: str = ""
    levels: tuple[str, ...] = ("INFO", "WARN", "ERROR")
    query: str = ""
    patterns: list[LogPattern] = field(default_factory=list)
