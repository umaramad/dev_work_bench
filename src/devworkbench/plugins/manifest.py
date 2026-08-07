"""PluginManifest — parsed and validated ``plugin.toml`` metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+ ships tomllib in the stdlib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.9/3.10 use the tomli backport
    import tomli as tomllib  # type: ignore[no-redef, import-not-found]

MANIFEST_FILENAME = "plugin.toml"


class ManifestError(Exception):
    """Raised when a manifest is missing or malformed."""


@dataclass(frozen=True)
class PluginManifest:
    """Validated plugin metadata."""

    id: str
    name: str
    version: str
    api_version: str = "1"
    entry: str = ""                 # e.g. "plugin:CompareModule"
    category: str = "module"        # module | utility | theme | provider
    builtin: bool = False
    license: str = "MIT"
    description: str = ""
    depends: tuple[str, ...] = field(default_factory=tuple)

    # -- parsing --------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginManifest":
        plugin = data.get("plugin", data)
        try:
            return cls(
                id=str(plugin["id"]),
                name=str(plugin.get("name", plugin["id"])),
                version=str(plugin.get("version", "0.0.0")),
                api_version=str(plugin.get("api_version", "1")),
                entry=str(plugin.get("entry", "")),
                category=str(plugin.get("category", "module")),
                builtin=bool(plugin.get("builtin", False)),
                license=str(plugin.get("license", "MIT")),
                description=str(plugin.get("description", "")),
                depends=tuple(str(dep) for dep in plugin.get("depends", ())),
            )
        except KeyError as exc:
            raise ManifestError(f"manifest missing required field: {exc}") from exc

    @classmethod
    def load(cls, manifest_path: str | Path) -> "PluginManifest":
        path = Path(manifest_path)
        if not path.exists():
            raise ManifestError(f"manifest not found: {path}")
        return cls.load_from_toml(path.read_text(encoding="utf-8"))

    @classmethod
    def load_from_toml(cls, text: str) -> "PluginManifest":
        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ManifestError(f"invalid TOML: {exc}") from exc
        manifest = cls.from_dict(raw)
        manifest.validate()
        return manifest

    # -- validation ----------------------------------------------------------------------

    def validate(self) -> None:
        """Raise ManifestError when required fields are empty."""
        if not self.id:
            raise ManifestError("plugin id must not be empty")
        if not self.entry:
            raise ManifestError(f"plugin {self.id}: entry point must not be empty")
        if not self.api_version.isdigit():
            raise ManifestError(f"plugin {self.id}: api_version must be numeric")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "api_version": self.api_version,
            "entry": self.entry,
            "category": self.category,
            "builtin": self.builtin,
            "license": self.license,
            "description": self.description,
            "depends": list(self.depends),
        }
