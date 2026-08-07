"""Model base — dict (de)serialization for dataclass models."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass
class Model:
    """Base for domain models; provides ``to_dict`` / ``from_dict``."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Model":
        """Reconstruct from a ``to_dict`` payload (ignores unknown keys)."""
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in fields})
