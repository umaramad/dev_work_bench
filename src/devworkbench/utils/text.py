"""Text helpers — truncation, slugs, identifier conversion."""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[^a-z0-9]+")


def truncate(text: str, limit: int = 80, ellipsis: str = "\u2026") -> str:
    """Cut ``text`` to ``limit`` characters, appending ``ellipsis``."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(ellipsis))].rstrip() + ellipsis


def slugify(text: str, fallback: str = "item") -> str:
    """Lowercase, hyphenated identifier from arbitrary text."""
    slug = _WORD_RE.sub("-", text.lower()).strip("-")
    return slug or fallback


def camel_to_snake(name: str) -> str:
    """``CompareService`` -> ``compare_service``."""
    step1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", step1).lower()
