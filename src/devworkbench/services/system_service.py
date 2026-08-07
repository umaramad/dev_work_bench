"""SystemService — macOS shell integrations (reveal, URLs, platform info).

Implementation is stubbed; the public surface is defined so modules can
depend on it today.
"""

from __future__ import annotations

import platform
from pathlib import Path


class SystemService:
    """OS-level helpers used by modules (git, ssh, settings…)."""

    @staticmethod
    def os_name() -> str:
        return platform.system()

    @staticmethod
    def is_macos() -> bool:
        return platform.system() == "Darwin"

    def reveal_in_finder(self, path: str | Path) -> None:
        """Select ``path`` in Finder (macOS only)."""
        # e.g. subprocess.Popen(["open", "-R", str(path)])
        raise NotImplementedError("reveal_in_finder will be implemented with the services milestone")

    def open_url(self, url: str) -> None:
        """Open ``url`` in the default browser."""
        # e.g. subprocess.Popen(["open", url])
        raise NotImplementedError("open_url will be implemented with the services milestone")

    def open_terminal(self, cwd: str | Path) -> None:
        """Open a Terminal.app window at ``cwd``."""
        raise NotImplementedError("open_terminal will be implemented with the services milestone")
