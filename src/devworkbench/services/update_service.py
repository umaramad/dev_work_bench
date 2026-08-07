"""UpdateService — release-feed checks for new DevWorkbench versions.

Runs as a background task and fails silently (a stale client is not a crash).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    url: str
    notes: str = ""


class UpdateService:
    """Checks the project's release feed for newer versions."""

    def __init__(self, current_version: str, feed_url: str = "") -> None:
        self._current = current_version
        self._feed_url = feed_url

    # -- public surface -----------------------------------------------------------

    async def check(self) -> ReleaseInfo | None:
        """Return a newer release if one exists, else None."""
        # httpx GET on the feed, semver compare — later milestone.
        return None

    @property
    def current_version(self) -> str:
        return self._current

    def changelog(self, release: ReleaseInfo) -> str:
        """Render release notes for the changelog dialog."""
        return release.notes
