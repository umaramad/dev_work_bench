"""GroupScanWorker — scan + classify every repository of a group off-thread.

Sequential per-repository scan built directly on
``scan_declared_dependencies`` (the existing effective-model service) and the
pure-Python J2EE classifier. A malformed repository (missing path, unreadable
POMs) records its error and never aborts the rest of the group — the result
is deliberately *partial*: each entry carries either a scan + classification
or an error string. Progress is emitted per repository so the UI can render
a live "scanning i/n" state.

Cancellation is cooperative and checked between repositories (a single POM
scan is bounded by the existing ``_MAX_POMS`` cap).
"""

from __future__ import annotations

import logging

from devworkbench.services.dependency_explorer import ArtifactIndex, classify_repository
from devworkbench.workers.base import Worker
from devworkbench.workers.maven_worker import scan_declared_dependencies

logger = logging.getLogger("devworkbench.workers.group_maven")


class GroupScanWorker(Worker):
    """Scan and classify a list of repositories; build the ArtifactIndex.

    ``repos`` is a list of ``(name, path)`` tuples captured at construction
    time (the UI thread owns widget state, never the worker). ``overrides``
    is the persisted ``{repo_path: j2ee|non_j2ee|auto}`` map applied during
    classification.
    """

    def __init__(self, repos: list[tuple[str, str]], overrides: dict[str, str] | None = None) -> None:
        super().__init__()
        self._repos = [(str(name), str(path)) for name, path in (repos or [])]
        self._overrides = dict(overrides or {})

    @property
    def repo_count(self) -> int:
        return len(self._repos)

    def work(self):
        entries: list[dict] = []
        total = len(self._repos)
        for index, (name, path) in enumerate(self._repos):
            if self.cancelled:
                break
            entry: dict = {"name": name, "path": path, "scan": None, "error": "", "classification": {}}
            try:
                scan = scan_declared_dependencies(path)
                entry["scan"] = scan
                entry["classification"] = classify_repository(
                    scan, repo_path=path, overrides=self._overrides
                ).to_dict()
            except Exception as exc:  # noqa: BLE001 — one bad repo must not kill the group scan
                logger.warning("group scan failed for %s: %s", path, exc)
                entry["error"] = str(exc)
                # No scan → classify from the filesystem alone (config-only
                # repos can still qualify via descriptors).
                try:
                    entry["classification"] = classify_repository(
                        {"repo_path": path, "modules": [], "dependencies": []},
                        repo_path=path,
                        overrides=self._overrides,
                    ).to_dict()
                except Exception:  # noqa: BLE001 — classification is best-effort
                    entry["classification"] = {}
            entries.append(entry)
            self.report(
                int((index + 1) * 100 / total) if total else 100,
                {"done": index + 1, "total": total, "name": name},
            )
        return {
            "repos": entries,
            "total": total,
            "completed": len(entries),
            "cancelled": self.cancelled,
            "index": ArtifactIndex(entries),
        }
