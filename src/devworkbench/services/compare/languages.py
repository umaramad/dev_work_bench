"""Language profiles for comment-aware and word-aware comparison.

Each profile describes *how to normalize* lines of that language: which
prefixes start a line comment, an optional block-comment pair, and the word
tokenizer used for intra-line (word-level) diffs. Structured kinds (JSON,
XML, YAML, Properties) are canonicalized separately in ``structured.py`` and
diffed as text; their profiles here are only used for word-level detail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DEFAULT_WORD_RE = re.compile(r"\w+|[^\w\s]|\s+")
_PYTHON_WORD_RE = re.compile(
    r"[A-Za-z_]\w*|0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?|"
    r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|"(?:[^"\\]|\\.)*$|#[^\n]*|'
    r"[^\w\s]|\s+"
)


@dataclass(frozen=True)
class LanguageProfile:
    name: str
    line_comment: tuple[str, ...] = ()
    block_comment: tuple[str, str] | None = None  # (start, end)
    word_re: re.Pattern = _DEFAULT_WORD_RE
    # Whether the word tokenizer should be string-literal aware when
    # stripping comments (protects "http://…" and '# inside "quotes"').
    quote_aware: bool = True


PROFILES: dict[str, LanguageProfile] = {
    "text": LanguageProfile("text"),
    "python": LanguageProfile("python", line_comment=("#",), word_re=_PYTHON_WORD_RE),
    "java": LanguageProfile("java", line_comment=("//",), block_comment=("/*", "*/")),
    "dart": LanguageProfile("dart", line_comment=("//",), block_comment=("/*", "*/")),
    "sql": LanguageProfile("sql", line_comment=("--",), block_comment=("/*", "*/")),
    "markdown": LanguageProfile("markdown", block_comment=("<!--", "-->"), quote_aware=False),
    "json": LanguageProfile("json"),
    "xml": LanguageProfile("xml", block_comment=("<!--", "-->"), quote_aware=False),
    "yaml": LanguageProfile("yaml", line_comment=("#",)),
    "properties": LanguageProfile("properties", line_comment=("#", "!")),
}

# Aliases so detect_kind output maps straight into PROFILES.
PROFILE_ALIASES: dict[str, str] = {
    "text": "text",
    "json": "json",
    "xml": "xml",
    "yaml": "yaml",
    "properties": "properties",
    "sql": "sql",
    "java": "java",
    "python": "python",
    "dart": "dart",
    "markdown": "markdown",
}


def profile_for(kind: str) -> LanguageProfile:
    return PROFILES.get(PROFILE_ALIASES.get(kind, "text"), PROFILES["text"])


def tokenize_line(text: str, kind: str) -> list[str]:
    """Split a line into tokens for word-level diffing."""
    return profile_for(kind).word_re.findall(text)
