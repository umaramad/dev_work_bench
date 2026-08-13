"""GitService — Qt-free async git operations for the Flet UI layer.

The Flet shell (``devworkbench.flet_ui``) cannot reuse the PySide6
``GitWorker`` (it is built on ``QRunnable`` + Qt signals), so this
service mirrors the same command set as asyncio coroutines: every
operation runs ``git`` in a subprocess off the event loop, returns a
plain dict, and raises ``RuntimeError`` on failure. Callers use
``await`` and ``page.run_task`` for non-blocking execution.

Operations match ``devworkbench.workers.git_worker`` where applicable so
the two UIs behave identically: status, pull (rebase), fetch, log,
branches, reset, check_repo, remote_status, find_repos.
"""

from __future__ import annotations

import asyncio
import os

# Seconds per operation class (generous; matches the Qt worker).
_TIMEOUTS = {
    "check_repo": 15,
    "status": 30,
    "fetch": 120,
    "pull": 120,
    "log": 30,
    "branches": 30,
    "reset": 60,
    "remote_status": 30,
    "find_repos": 30,
}

_LOG_FORMAT = "--pretty=format:%h%x1f%an%x1f%ad%x1f%s"

# Directories never descended into when scanning for nested repositories.
_SKIP_DIRS = frozenset(
    {
        ".git", "node_modules", ".venv", "venv", "env", "target", "build",
        "dist", "out", "__pycache__", ".idea", ".vscode", ".Trash",
    }
)
_MAX_DEPTH = 8
_MAX_REPOS = 200


class GitService:
    """Runs git operations as asyncio subprocesses (no Qt involved)."""

    def __init__(self, executable: str = "git") -> None:
        self._executable = executable

    # -- public operations -----------------------------------------------------

    async def check_repo(self, path: str) -> bool:
        """True when ``path`` is inside a git working tree."""
        result = await self._git(("rev-parse", "--is-inside-work-tree"), path)
        return result["ok"]

    async def status(self, path: str) -> dict:
        return await self._git(("status", "--short", "--branch"), path)

    async def fetch(self, path: str) -> dict:
        return await self._git(("fetch", "--all", "--prune"), path)

    async def fetch_all(self, path: str) -> dict:
        """Fetch every nested git repo under ``path`` (including ``path``)."""
        repos = await self.find_repos(path)
        if not repos and await self.check_repo(path):
            repos = [os.path.abspath(path)]
        results = []
        for repo in repos:
            try:
                result = await self.fetch(repo)
            except Exception as exc:  # noqa: BLE001 — collect per-repo failures
                result = {"ok": False, "output": str(exc), "code": -1}
            results.append({"path": repo, "ok": result.get("ok"), "output": result.get("output", "")})
        ok = all(item.get("ok") for item in results) if results else False
        summary = f"{sum(1 for r in results if r.get('ok'))}/{len(results)} repos fetched"
        return {"ok": ok, "output": summary, "repos": repos, "results": results}

    async def pull(self, path: str) -> dict:
        return await self._git(("pull", "--rebase"), path)

    def set_executable(self, executable: str) -> None:
        """Update the git binary (e.g. after Settings Apply)."""
        self._executable = (executable or "git").strip() or "git"

    async def branches(self, path: str) -> dict:
        """Local + remote branches with tracking info (``-vv``)."""
        return await self._git(("branch", "-a", "-vv"), path)

    async def reset(self, path: str, hard: bool = False) -> dict:
        args = ("reset", "--hard") if hard else ("reset",)
        return await self._git(args, path)

    async def log(self, path: str, count: int = 20) -> dict:
        result = await self._git(
            ("log", _LOG_FORMAT, "--date=short", f"-{count}"), path
        )
        rows = [line.split("\x1f") for line in result["output"].splitlines() if line]
        return {"ok": result["ok"], "rows": rows}

    async def remote_status(self, path: str) -> dict:
        """Branch + ahead/behind vs upstream — local-only (no network)."""
        check = await self._git(("rev-parse", "--is-inside-work-tree"), path)
        if not check["ok"]:
            return {"is_repo": False, "branch": "", "ahead": 0, "behind": 0, "upstream": None}
        status = await self._git(("status", "--short", "--branch"), path)
        branch = ""
        ahead = 0
        behind = 0
        upstream: str | None = None
        for line in status["output"].splitlines():
            if not line.startswith("##"):
                continue
            body = line[2:].strip()
            if body.startswith("No commits yet on "):
                branch = body[len("No commits yet on "):]
                break
            if body.startswith("Initial commit on "):
                branch = body[len("Initial commit on "):]
                break
            if body in ("No commits yet", "Initial commit"):
                break
            head, _, bracket = body.partition(" [")
            if "..." in head:
                branch, upstream = head.split("...", 1)
            else:
                branch = head
            if bracket.endswith("]"):
                bracket = bracket[:-1]
            for piece in bracket.split(","):
                piece = piece.strip()
                if piece.startswith("ahead "):
                    ahead = int(piece.split(" ", 1)[1])
                elif piece.startswith("behind "):
                    behind = int(piece.split(" ", 1)[1])
            break
        return {
            "is_repo": True,
            "branch": branch,
            "ahead": ahead,
            "behind": behind,
            "upstream": upstream,
        }

    async def find_repos(self, path: str) -> list[str]:
        """Every directory under ``path`` that contains a ``.git`` marker."""
        found: list[str] = []
        root = os.path.abspath(path)
        for dirpath, dirnames, _filenames in os.walk(root):
            if len(found) >= _MAX_REPOS:
                break
            depth = os.path.relpath(dirpath, root)
            if depth != "." and depth.count(os.sep) >= _MAX_DEPTH:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in sorted(dirnames) if d not in _SKIP_DIRS]
            marker = os.path.join(dirpath, ".git")
            if os.path.isdir(marker) or os.path.isfile(marker):
                found.append(dirpath)
                if os.path.isdir(marker):
                    dirnames[:] = []  # regular repo owns its working tree
        return found

    # -- plumbing ---------------------------------------------------------------

    async def _git(self, args: tuple[str, ...], cwd: str, timeout: int | None = None) -> dict:
        """Run git; returns {ok, output, code}. Raises on missing binary/timeout."""
        if not cwd or not os.path.isdir(cwd):
            raise RuntimeError(f"folder not found: {cwd}")
        seconds = timeout or _TIMEOUTS.get(args[0] if args else "", 60)
        try:
            proc = await asyncio.create_subprocess_exec(
                self._executable,
                *args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"git executable not found: {self._executable}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), seconds)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise RuntimeError(f"git {' '.join(args)} timed out after {seconds}s") from exc
        output = ((stdout or b"") + (stderr or b"")).decode("utf-8", errors="replace").strip()
        return {"ok": proc.returncode == 0, "output": output, "code": proc.returncode}
