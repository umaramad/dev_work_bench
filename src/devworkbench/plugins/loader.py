"""PluginLoader — discovery and import of plugin packages.

Discovery reads manifests without importing anything; ``load`` imports the
entry module *from the plugin's own directory* (via
``importlib.util.spec_from_file_location``) into an isolated namespace.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path

from devworkbench.plugins.api import API_VERSION
from devworkbench.plugins.manifest import MANIFEST_FILENAME, ManifestError, PluginManifest

logger = logging.getLogger("devworkbench.plugins.loader")


class LoadError(Exception):
    """Raised when a plugin cannot be discovered or imported."""


@dataclass
class PluginRecord:
    """A discovered, importable plugin."""

    manifest: PluginManifest
    directory: Path
    module: object | None = None
    entry_point: object | None = None


class PluginLoader:
    """Scans plugin directories, validates manifests, imports entries."""

    def __init__(self, plugins_dir: str | Path, api_version: str = API_VERSION) -> None:
        self._plugins_dir = Path(plugins_dir)
        self._api_version = api_version

    @property
    def plugins_dir(self) -> Path:
        return self._plugins_dir

    # -- discovery ---------------------------------------------------------------------

    def discover(self) -> list[tuple[PluginManifest, Path]]:
        """Return ``(manifest, directory)`` for every plugin folder."""
        found: list[tuple[PluginManifest, Path]] = []
        if not self._plugins_dir.exists():
            return found
        for directory in sorted(self._plugins_dir.iterdir()):
            if not directory.is_dir():
                continue
            manifest_path = directory / MANIFEST_FILENAME
            if not manifest_path.exists():
                continue
            try:
                manifest = PluginManifest.load(manifest_path)
                self._check_api(manifest)
                found.append((manifest, directory))
            except (ManifestError, LoadError) as exc:
                logger.warning("skipping %s: %s", directory.name, exc)
        return found

    # -- loading ------------------------------------------------------------------------

    def load(self, manifest: PluginManifest, directory: Path | None = None) -> PluginRecord:
        """Import the entry module from the plugin's directory."""
        self._check_api(manifest)
        directory = directory or self._resolve_directory(manifest)
        module_name, _, attribute = manifest.entry.partition(":")
        if not module_name or not attribute:
            raise LoadError(f"{manifest.id}: entry must be 'module:Class'")

        module_file = directory / f"{module_name}.py"
        if not module_file.exists():
            raise LoadError(f"{manifest.id}: entry module not found: {module_file}")
        try:
            # Isolated namespace per plugin id — never pollute sys.modules globally.
            import_name = f"devworkbench.plugins.dynamic.{manifest.id}"
            spec = importlib.util.spec_from_file_location(import_name, module_file)
            if spec is None or spec.loader is None:
                raise LoadError(f"{manifest.id}: cannot build import spec for {module_file}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            entry_point = getattr(module, attribute)
        except LoadError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface as a readable error
            raise LoadError(f"{manifest.id}: failed to import {manifest.entry}: {exc}") from exc
        logger.debug("loaded plugin %s (%s)", manifest.id, manifest.version)
        return PluginRecord(manifest=manifest, directory=directory, module=module, entry_point=entry_point)

    # -- internals ----------------------------------------------------------------------------

    def _resolve_directory(self, manifest: PluginManifest) -> Path:
        """Find the folder whose manifest matches ``manifest.id``."""
        if self._plugins_dir.exists():
            for directory in sorted(self._plugins_dir.iterdir()):
                manifest_path = directory / MANIFEST_FILENAME
                if manifest_path.exists():
                    try:
                        if PluginManifest.load(manifest_path).id == manifest.id:
                            return directory
                    except ManifestError:
                        continue
        return self._plugins_dir / manifest.id

    def _check_api(self, manifest: PluginManifest) -> None:
        if manifest.api_version != self._api_version:
            raise LoadError(
                f"{manifest.id}: plugin API v{manifest.api_version} does not match "
                f"host API v{self._api_version}"
            )
