"""Lightweight syntax highlighting — pure per-line tokenizers.

Each ``highlight_line`` call returns color runs ``[(text, token), …]`` where
``token`` ∈ {default, keyword, string, comment, number, decorator, type,
func, tag, attr, bool}. The viewer draws runs lazily for *visible* lines only
and caches them, so a 100K-line file never tokenizes more than the screenful
that is actually painted.

Multi-line block comments (``/* … */`` in Java/Dart/SQL) are handled through
``block_comment_states``: the viewer scans the whole document once per
content load (O(total chars), cheap even for huge files) and passes the
per-line ``block_active`` flag into ``highlight_line`` so continuation lines
stay colored as comments.

Tokenizers are deliberately small regex passes per language — enough for a
professional diff/read view, without the cost of a full parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT = "default"
KEYWORD = "keyword"
STRING = "string"
COMMENT = "comment"
NUMBER = "number"
DECORATOR = "decorator"
TYPE = "type"
FUNC = "func"
TAG = "tag"
ATTR = "attr"
BOOL = "bool"

# Keep this in sync with the color mapping in CodeView._run_colors().
TOKENS = (DEFAULT, KEYWORD, STRING, COMMENT, NUMBER, DECORATOR, TYPE, FUNC, TAG, ATTR, BOOL)


@dataclass(frozen=True)
class _Spec:
    keywords: frozenset[str] = frozenset()
    line_comment: tuple[str, ...] = ()
    block_comment: tuple[str, str] | None = None
    quote_chars: str = "\"'"
    number_re: str | None = None
    decorator_re: str | None = None  # @name (python)
    annotation_re: str | None = None  # @name (java/dart)
    func_re: str | None = None  # name( pattern
    type_re: str | None = None


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_PY_KEYWORDS = frozenset(
    """and as assert async await break class continue def del elif else except
    finally for from global if import in is lambda nonlocal not or pass raise
    return try while with yield False None True self cls""".split()
)
_PY_SPEC = _Spec(
    keywords=_PY_KEYWORDS,
    line_comment=("#",),
    number_re=r"\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b",
    decorator_re=r"@[A-Za-z_][A-Za-z0-9_.]*",
)

_JAVA_KEYWORDS = frozenset(
    """abstract assert boolean break byte case catch char class const continue
    default do double else enum extends final finally float for goto if
    implements import instanceof int interface long native new package private
    protected public return short static strictfp super switch synchronized
    this throw throws transient try void volatile while true false null""".split()
)
_JAVA_SPEC = _Spec(
    keywords=_JAVA_KEYWORDS,
    line_comment=("//",),
    block_comment=("/*", "*/"),
    number_re=r"\b\d+(?:\.\d+)?[fFdDlL]?\b",
    annotation_re=r"@[A-Za-z_][A-Za-z0-9_.]*",
    func_re=r"[A-Za-z_][A-Za-z0-9_]*\s*\(",
    type_re=r"\b(?:String|Object|Integer|Long|Double|Float|Boolean|List|Map|Set|ArrayList|HashMap|HashSet|Optional|Runnable|Thread|Exception|RuntimeException|Throwable)\b",
)

_DART_KEYWORDS = frozenset(
    """abstract as assert async await break case catch class const continue
    covariant default deferred do dynamic else enum export extends extension
    external factory false final finally for Function get hide if implements
    import in interface is late library mixin new null on operator part required
    rethrow return set show static super switch sync this throw true try
    typedef var void while with yield""".split()
)
_DART_SPEC = _Spec(
    keywords=_DART_KEYWORDS,
    line_comment=("//",),
    block_comment=("/*", "*/"),
    number_re=r"\b\d+(?:\.\d+)?\b",
    decorator_re=r"@[A-Za-z_][A-Za-z0-9_.]*",
    func_re=r"[A-Za-z_][A-Za-z0-9_]*\s*\(",
    type_re=r"\b(?:String|int|double|bool|num|dynamic|void|List|Map|Set|Future|Stream|Object|Function|var)\b",
)

_SQL_KEYWORDS = frozenset(
    """select from where insert into values update set delete create table
    alter drop index view join inner left right outer on as and or not null
    is in between like order by group having distinct limit offset union all
    exists case when then else end primary key foreign references default
    unique check constraint begin commit rollback transaction grant revoke""".split()
)
_SQL_SPEC = _Spec(
    keywords=_SQL_KEYWORDS,
    line_comment=("--",),
    block_comment=("/*", "*/"),
    number_re=r"\b\d+(?:\.\d+)?\b",
)

_JSON_SPEC = _Spec(keywords=frozenset({"true", "false", "null"}), number_re=r"-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b")

_YAML_SPEC = _Spec(keywords=frozenset({"true", "false", "null", "yes", "no", "on", "off"}), line_comment=("#",))

_PROPERTIES_SPEC = _Spec(line_comment=("#", "!"))

_XML_SPEC = _Spec()

_SPECS: dict[str, _Spec] = {
    "text": _Spec(),
    "python": _PY_SPEC,
    "java": _JAVA_SPEC,
    "dart": _DART_SPEC,
    "sql": _SQL_SPEC,
    "json": _JSON_SPEC,
    "yaml": _YAML_SPEC,
    "properties": _PROPERTIES_SPEC,
    "xml": _XML_SPEC,
    "markdown": _Spec(),
}


def spec_for(kind: str) -> _Spec:
    return _SPECS.get(kind, _SPECS["text"])


# ---------------------------------------------------------------------------
# Shared regexes
# ---------------------------------------------------------------------------

_OPEN_QUOTE = re.compile(r'(["\'])((?:[^\\\1]|\\.)*)')
_TRIPLE = re.compile(r'("""|\'\'\')(.*?)(\1)', re.DOTALL)
_PY_STRING_PREFIX = re.compile(r"[rRbBuUfF]{0,2}(?=[\"'])")
_NUMBER_RE_CACHE: dict[str, re.Pattern] = {}
_ATTR_PAIR = re.compile(r'([A-Za-z_:][\w:.-]*)\s*=\s*("[^"]*"|\'[^\']*\')')
_XML_TAG = re.compile(r"</?([A-Za-z_:][\w:.-]*)")


def _number_re(pat: str | None) -> re.Pattern | None:
    if not pat:
        return None
    compiled = _NUMBER_RE_CACHE.get(pat)
    if compiled is None:
        compiled = re.compile(pat)
        _NUMBER_RE_CACHE[pat] = compiled
    return compiled


def highlight_line(text: str, kind: str, block_active: bool = False) -> list[tuple[str, str]]:
    """Return (text, token) runs for one line of ``kind`` code.

    ``block_active`` marks a line that *continues* a multi-line block comment
    opened on an earlier line (computed once per document by
    ``block_comment_states``); such lines are comment-colored up to the
    closing marker, then tokenized normally.
    """
    if not text:
        return []
    spec = spec_for(kind)
    if block_active and spec.block_comment:
        start, end = spec.block_comment
        close = text.find(end)
        if close == -1:
            return [(text, COMMENT)]
        rest = text[close + len(end) :]
        runs = [(text[: close + len(end)], COMMENT)]
        if rest:
            runs.extend(_highlight_generic(rest, spec, kind))
        return runs
    if kind == "xml":
        return _highlight_xml(text)
    if kind == "json":
        return _highlight_json(text)
    if kind == "yaml":
        return _highlight_yaml(text)
    if kind == "properties":
        return _highlight_properties(text)
    if kind == "markdown":
        return _highlight_markdown(text)
    return _highlight_generic(text, spec, kind)


def block_comment_states(lines: list[str], kind: str) -> list[bool]:
    """Per-line ``block_active`` flags for a document (``True`` when the line
    continues a ``/* … */`` block comment opened earlier). Quote-aware scan
    so a ``/*`` inside a string literal does not start a comment.
    """
    spec = spec_for(kind)
    if not spec.block_comment:
        return [False] * len(lines)
    start, end = spec.block_comment
    states: list[bool] = []
    in_block = False
    for line in lines:
        states.append(in_block)
        i, n = 0, len(line)
        quote: str | None = None
        while i < n:
            ch = line[i]
            if quote is not None:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                i += 1
                continue
            if ch in "\"'":
                quote = ch
                i += 1
                continue
            if not in_block and line.startswith(start, i):
                in_block = True
                i += len(start)
                continue
            if in_block and line.startswith(end, i):
                in_block = False
                i += len(end)
                continue
            i += 1
    return states


# ---------------------------------------------------------------------------
# Generic tokenizer (python / java / dart / sql / text)
# ---------------------------------------------------------------------------


def _highlight_generic(text: str, spec: _Spec, kind: str) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    i = 0
    n = len(text)

    # Block comment state is per-line: the viewer re-tokenizes each line, so a
    # multi-line block comment loses its state between lines. The viewer
    # handles this by passing a ``block_active`` flag (see CodeView) — here we
    # just close any unterminated block so the rest of the line stays colored.
    while i < n:
        ch = text[i]

        # Line comments.
        hit = _line_comment_at(text, i, spec.line_comment)
        if hit:
            runs.append((text[i:], COMMENT))
            break

        # Block comment open.
        if spec.block_comment:
            start, end = spec.block_comment
            if text.startswith(start, i):
                close = text.find(end, i + len(start))
                if close == -1:
                    runs.append((text[i:], COMMENT))
                    break
                runs.append((text[i : close + len(end)], COMMENT))
                i = close + len(end)
                continue

        # Decorator / annotation.
        annotation_re = spec.decorator_re or spec.annotation_re
        if annotation_re and ch == "@":
            m = re.match(annotation_re, text[i:])
            if m:
                runs.append((m.group(0), DECORATOR))
                i += len(m.group(0))
                continue

        # Strings (with optional prefix).
        quote_start = i
        if spec.quote_chars and ch in spec.quote_chars:
            run, consumed = _consume_quote(text, i, ch)
            runs.append((run, STRING))
            i += consumed
            continue
        prefix = _PY_STRING_PREFIX.match(text, i) if kind == "python" and text[i] in "rRbBuUfF" else None
        if prefix and i + len(prefix.group(0)) < n and text[i + len(prefix.group(0))] in spec.quote_chars:
            q = text[i + len(prefix.group(0))]
            run, consumed = _consume_quote(text, i + len(prefix.group(0)), q)
            runs.append((text[i : i + len(prefix.group(0))], STRING))
            runs.append((run, STRING))
            i += len(prefix.group(0)) + consumed
            continue

        # Numbers.
        num_re = _number_re(spec.number_re)
        if num_re:
            m = num_re.match(text, i)
            if m:
                runs.append((m.group(0), NUMBER))
                i += len(m.group(0))
                continue

        # Function calls: consume the name only, so the "(" is emitted as a
        # plain run (keeps the tokenizer lossless).
        if spec.func_re and (ch.isalnum() or ch == "_"):
            m = re.match(spec.func_re, text[i:])
            if m:
                name = m.group(0).rstrip(" \t(")
                runs.append((name, FUNC))
                i += len(name)
                continue

        # Keywords / types.
        if ch.isalpha() or ch == "_":
            m = _WORD.match(text, i)
            word = m.group(0)
            if word in spec.keywords:
                token = BOOL if word in ("true", "false", "True", "False", "None", "null") else KEYWORD
                runs.append((word, token))
            elif spec.type_re and re.match(spec.type_re, word):
                runs.append((word, TYPE))
            else:
                runs.append((word, DEFAULT))
            i += len(word)
            continue

        runs.append((ch, DEFAULT))
        i += 1

    return runs


def _line_comment_at(text: str, i: int, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        if text.startswith(marker, i):
            # A "--" is not a comment when part of a longer token like "-->".
            if marker == "--" and i + 2 < len(text) and text[i + 2] in "-=":
                continue
            return True
    return False


def _consume_quote(text: str, i: int, quote: str) -> tuple[str, int]:
    """Consume a string literal from ``text[i]``; returns (literal, length)."""
    # Triple-quoted.
    if text.startswith(quote * 3, i):
        close = text.find(quote * 3, i + 3)
        if close == -1:
            return text[i:], len(text) - i
        return text[i : close + 3], close + 3 - i
    j = i + 1
    n = len(text)
    while j < n:
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == quote:
            return text[i : j + 1], j + 1 - i
        j += 1
    return text[i:], n - i  # unterminated: color to end of line


# ---------------------------------------------------------------------------
# Structured formats
# ---------------------------------------------------------------------------


def _highlight_json(text: str) -> list[tuple[str, str]]:
    return _highlight_json_yaml(text, "json")


def _highlight_yaml(text: str) -> list[tuple[str, str]]:
    return _highlight_json_yaml(text, "yaml")


def _highlight_json_yaml(text: str, mode: str) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if mode == "yaml" and ch == "#":
            # Only a comment when preceded by whitespace or line start.
            if i == 0 or text[i - 1] in " \t":
                runs.append((text[i:], COMMENT))
                break
        # JSON keys must be recognized before the generic string branch.
        if mode == "json" and ch == '"':
            run, consumed = _consume_quote(text, i, '"')
            j = i + consumed
            while j < n and text[j] in " \t":
                j += 1
            if j < n and text[j] == ":":
                runs.append((run, ATTR))
                runs.append((text[i + consumed : j], DEFAULT))
                i = j
                continue
            runs.append((run, STRING))
            i += consumed
            continue
        if ch in "\"'":
            run, consumed = _consume_quote(text, i, ch)
            runs.append((run, STRING))
            i += consumed
            continue
        m = re.match(r"-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b", text[i:])
        if m:
            runs.append((m.group(0), NUMBER))
            i += len(m.group(0))
            continue
        if ch.isalpha() or ch == "_":
            m = _WORD.match(text, i)
            word = m.group(0)
            if word in ("true", "false", "null", "True", "False", "None"):
                runs.append((word, BOOL))
            else:
                runs.append((word, DEFAULT))
            i += len(word)
            continue
        if mode == "yaml" and ch.isalpha():
            m = _WORD.match(text, i)
            j = i + len(m.group(0))
            while j < n and text[j] in " \t":
                j += 1
            if j < n and text[j] == ":":
                runs.append((text[i:j], ATTR))
                i = j
                continue
        runs.append((ch, DEFAULT))
        i += 1
    return runs


def _highlight_properties(text: str) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    stripped = text.lstrip()
    if stripped.startswith(("#", "!")):
        return [(text, COMMENT)]
    for sep in ("=", ":"):
        idx = text.find(sep)
        if idx > 0:
            runs.append((text[:idx], ATTR))
            runs.append((text[idx : idx + 1], DEFAULT))
            runs.append((text[idx + 1 :], STRING if text[idx + 1 :] else ""))
            return [r for r in runs if r[1]]
    return [(text, DEFAULT)]


def _highlight_xml(text: str) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    i = 0
    n = len(text)
    if text.lstrip().startswith("<!--"):
        close = text.find("-->")
        if close != -1:
            return [(text[: close + 3], COMMENT), (text[close + 3 :], DEFAULT)]
        return [(text, COMMENT)]
    while i < n:
        ch = text[i]
        if ch == "<":
            m = _XML_TAG.match(text, i)
            if m:
                tag = m.group(1)
                # Find the end of the tag, honoring quotes.
                j = i + len(m.group(0))
                while j < n:
                    if text[j] in "\"'":
                        _, consumed = _consume_quote(text, j, text[j])
                        j += consumed
                        continue
                    if text[j] == ">":
                        break
                    j += 1
                if j < n and text[j] == ">":
                    tag_text = text[i : j + 1]
                    runs.append((tag_text, TAG))
                    i = j + 1
                    continue
                runs.append((text[i:], TAG))
                break
            runs.append((ch, DEFAULT))
            i += 1
            continue
        if ch in "\"'":
            run, consumed = _consume_quote(text, i, ch)
            # Attribute values inside a tag get colored as strings.
            runs.append((run, STRING))
            i += consumed
            continue
        m = _WORD.match(text, i)
        if m and m.start() == i:
            runs.append((m.group(0), DEFAULT))
            i += len(m.group(0))
            continue
        runs.append((ch, DEFAULT))
        i += 1
    return runs


def _highlight_markdown(text: str) -> list[tuple[str, str]]:
    stripped = text.lstrip()
    if stripped.startswith("#"):
        return [(text, KEYWORD)]
    if text.startswith(("```", "    ")):
        return [(text, COMMENT)]
    # Inline code spans and links get string color; otherwise plain.
    if "`" in text:
        return [(part, STRING if idx % 2 else DEFAULT) for idx, part in enumerate(text.split("`"))]
    if text.strip().startswith(("- ", "* ", "> ")):
        return [(text[:2], ATTR), (text[2:], DEFAULT)]
    return [(text, DEFAULT)]
