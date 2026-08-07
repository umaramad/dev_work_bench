"""Structured-format canonicalization for JSON, XML, YAML and .properties.

Comparing "semantic" content: two JSON files with different key order or
whitespace are still *the same document*, so canonicalizers parse the format
and re-emit a normalized text. The canonical text is then diffed with the
normal line engine — no bespoke diff logic needed.

Behavior per format:

- **JSON**: re-emit with sorted keys, compact separators. ``json.loads``
  validates the document; parse errors surface as ``StructuredError`` and the
  caller falls back to a raw text comparison (still better than nothing).
- **XML**: normalize whitespace between tags (``>  <`` → ``><``) and sort
  attributes per element so reordering doesn't count as a change.
- **YAML**: uses PyYAML when installed (canonical ``safe_dump``, sorted keys);
  otherwise falls back to a text-aware pass (strip comments, sort top-level
  ``key:`` lines is NOT done — that would break blocks — so fallback only
  strips comments and collapses blank runs).
- **Properties**: sort the ``key=value`` lines and drop comments/blank lines,
  so reordering properties never shows as a diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

try:  # PyYAML is an optional extra (see pyproject.toml)
    import yaml  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — exercised only without the extra
    yaml = None  # type: ignore[assignment]

_WS_BETWEEN_TAGS = re.compile(r">\s+<")
_ATTR_RE = re.compile(r'(\w[\w.:-]*)=("[^"]*"|\'[^\']*\')')


class StructuredError(Exception):
    """Raised when a structured document cannot be parsed/canonicalized."""


@dataclass(frozen=True)
class StructuredCompare:
    """Canonical text plus a flag whether the original parsed successfully."""

    text: str
    parsed: bool  # False → caller fell back to a raw-text compare
    message: str = ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def canonicalize(kind: str, text: str) -> StructuredCompare:
    """Canonicalize ``text`` for structured ``kind``; never raises."""
    try:
        if kind == "json":
            return _canonicalize_json(text)
        if kind == "xml":
            return _canonicalize_xml(text)
        if kind == "yaml":
            return _canonicalize_yaml(text)
        if kind == "properties":
            return _canonicalize_properties(text)
    except StructuredError as exc:
        return StructuredCompare(text, parsed=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001 — canonicalization must never crash
        return StructuredCompare(text, parsed=False, message=str(exc))
    return StructuredCompare(text, True)


# ---------------------------------------------------------------------------
# Per-format canonicalizers
# ---------------------------------------------------------------------------


def _canonicalize_json(text: str) -> StructuredCompare:
    try:
        value = _json_parse(text)
    except Exception as exc:  # noqa: BLE001
        raise StructuredError(f"invalid JSON: {exc}") from exc
    return StructuredCompare(_json_dump(value), True)


def _json_parse(text: str):
    import json

    return json.loads(text)


def _json_dump(value) -> str:
    import json

    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)


def _canonicalize_xml(text: str) -> StructuredCompare:
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(text)
    except Exception as exc:  # noqa: BLE001
        raise StructuredError(f"invalid XML: {exc}") from exc
    canonical = _xml_serialize(root)
    return StructuredCompare(canonical, True)


def _xml_serialize(element) -> str:
    """Serialize an Element with sorted attributes and normalized whitespace."""
    tag = element.tag
    attrs = "".join(
        f' {name}="{element.get(name)}"' for name in sorted(element.attrib)
    )
    if len(element) == 0 and (element.text or "").strip() == "":
        return f"<{tag}{attrs}/>"
    pieces = [f"<{tag}{attrs}>"]
    if element.text:
        pieces.append(" ".join(element.text.split()))
    for child in element:
        pieces.append(_xml_serialize(child))
        if child.tail:
            pieces.append(" ".join(child.tail.split()))
    pieces.append(f"</{tag}>")
    return "".join(pieces)


def _canonicalize_yaml(text: str) -> StructuredCompare:
    if yaml is not None:
        try:
            value = yaml.safe_load(text)
            if value is None:
                return StructuredCompare("", True)
            canonical = yaml.safe_dump(
                value, sort_keys=True, default_flow_style=False, allow_unicode=True
            )
            return StructuredCompare(canonical, True)
        except Exception as exc:  # noqa: BLE001
            raise StructuredError(f"invalid YAML: {exc}") from exc
    # Fallback without PyYAML: strip comments and blank runs, keep structure.
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(line)
    return StructuredCompare("\n".join(lines) + "\n", False, "PyYAML not installed")


def _canonicalize_properties(text: str) -> StructuredCompare:
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        for sep in ("=", ":"):
            if sep in stripped:
                key, value = stripped.split(sep, 1)
                entries.append((key.strip(), value.strip()))
                break
        else:
            entries.append((stripped, ""))
    entries.sort(key=lambda pair: pair[0])
    return StructuredCompare(
        "\n".join(f"{key}={value}" for key, value in entries) + "\n", True
    )
