"""GitWorker — real git operations executed off the UI thread.

Each operation runs ``git`` via subprocess in the given directory; output
merges stdout+stderr so console-style progress (fetch/pull) survives. Long
operations get a generous timeout and cancellation is checked between repos
in ``fetch_all``.

Operations:
    check_repo  — is ``path`` inside a git working tree?
    open        — branch + status + recent commit rows in one shot
    status      — ``git status --short --branch``
    fetch       — ``git fetch --all --prune``
    pull        — ``git pull --rebase``
    log         — recent commits as rows (hash/author/date/message)
    find_repos  — every git repo nested under ``path`` (workspaces)
    fetch_all   — run fetch in each nested repo (progress per repo)
    remote_status — branch + ahead/behind vs the upstream (local-only)
    reset       — ``git reset`` (soft; ``hard=True`` → ``--hard``)
    has_branch  — does local ``refs/heads/<name>`` exist? (name in args[0])
    checkout    — ``git checkout <name>``; force and/or from remote tip
                  (``args=(name, "force", "origin")`` → ``checkout -f -B name origin/name``)

Signals contract: construct on the UI thread and retain until finished/error
(see ``workers/base.py``).
"""

from __future__ import annotations

import os
import subprocess

from devworkbench.workers.base import Worker

# Directories never descended into when scanning for nested repositories.
_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "env", "target", "build",
     "dist", "out", "__pycache__", ".idea", ".vscode", ".Trash"}
)
_MAX_DEPTH = 8
_MAX_REPOS = 200
_LOG_FORMAT = "--pretty=format:%h%x1f%an%x1f%ad%x1f%s"
_TIME_OUTPUT = {  # seconds per operation class
    "check_repo": 15,
    "open": 60,
    "status": 30,
    "fetch": 120,
    "pull": 120,
    "log": 30,
    "find_repos": 30,
    "fetch_all": 120,
    "remote_status": 30,
    "reset": 60,
    "has_branch": 15,
    "checkout": 30,
}


class GitWorker(Worker):
    """Runs one git operation (or a batch) off the UI thread."""

    def __init__(
        self,
        operation: str,
        path: str,
        args: tuple[str, ...] = (),
        executable: str = "git",
    ) -> None:
        super().__init__()
        self._operation = operation
        self._path = path
        self._args = args
        self._executable = executable
        self._timeout = _TIME_OUTPUT.get(operation, 60)

    # -- dispatch ---------------------------------------------------------------

    def work(self):
        op = self._operation
        if op == "check_repo":
            result = self._git(("rev-parse", "--is-inside-work-tree"))
            return {"is_repo": result["ok"], "output": result["output"]}
        if op == "open":
            return self._open_repo()
        if op == "status":
            return self._git(("status", "--short", "--branch"))
        if op == "fetch":
            return self._git(("fetch", "--all", "--prune"))
        if op == "pull":
            return self._git(("pull", "--rebase"))
        if op == "log":
            result = self._git(("log", _LOG_FORMAT, "--date=short", "-20", *self._args))
            rows = [line.split("\x1f") for line in result["output"].splitlines() if line]
            return {"ok": result["ok"], "rows": rows}
        if op == "find_repos":
            return {"repos": self._find_repos()}
        if op == "fetch_all":
            return self._fetch_all()
        if op == "remote_status":
            return self._remote_status()
        if op == "reset":
            # args: () soft reset; ("hard",) hard HEAD; ("hard", "origin/main") hard to ref
            if self._args and self._args[0] == "hard":
                target = self._args[1] if len(self._args) > 1 else None
                if target:
                    return self._git(("reset", "--hard", str(target)))
                return self._git(("reset", "--hard"))
            return self._git(("reset",))
        if op == "has_branch":
            name = (self._args[0] if self._args else "").strip()
            if not name:
                return {"ok": False, "exists": False, "output": "branch name required"}
            # Optional remote: args=("feature", "origin") → refs/remotes/origin/feature
            remote = (self._args[1] if len(self._args) > 1 else "").strip()
            ref = f"refs/remotes/{remote}/{name}" if remote else f"refs/heads/{name}"
            result = self._git(("show-ref", "--verify", "--quiet", ref))
            return {"ok": True, "exists": bool(result["ok"]), "output": result["output"], "ref": ref}
        if op == "checkout":
            name = (self._args[0] if self._args else "").strip()
            if not name:
                return {"ok": False, "output": "branch name required"}
            # Optional force: args=(name, "force")
            # From remote tip: args=(name, "force", "origin") → checkout -f -B name origin/name
            # Creates/resets the local branch to match the remote and discards local dirt.
            force = len(self._args) > 1 and str(self._args[1]).lower() in {"force", "-f", "true", "1"}
            remote = (self._args[2] if len(self._args) > 2 else "").strip()
            if remote:
                start = f"{remote}/{name}"
                if force:
                    return self._git(("checkout", "-f", "-B", name, start))
                return self._git(("checkout", "-B", name, start))
            if force:
                return self._git(("checkout", "-f", name))
            return self._git(("checkout", name))
        raise ValueError(f"unknown git operation {op!r}")

    # -- operations ---------------------------------------------------------------

    def _open_repo(self) -> dict:
        """Branch + status + recent commits in one worker (fast open).

        ``is_repo`` is False when ``path`` is not inside a working tree, so
        the UI can decide whether quick operations make sense."""
        check = self._git(("rev-parse", "--is-inside-work-tree"))
        if not check["ok"]:
            return {"is_repo": False, "output": check["output"]}
        branch = self._git(("rev-parse", "--abbrev-ref", "HEAD"))["output"]
        status = self._git(("status", "--short", "--branch"))
        log = self._git(("log", _LOG_FORMAT, "--date=short", "-20"))
        rows = [line.split("\x1f") for line in log["output"].splitlines() if line]
        # ``status --short --branch`` prints the branch line first, then one
        # line per change — anything beyond the first line means dirty.
        status_lines = status["output"].splitlines()
        return {
            "is_repo": True,
            "branch": branch or "unknown",
            "status": status["output"],
            "rows": rows,
            "dirty": len(status_lines) > 1,
        }

    def _find_repos(self) -> list[str]:
        """Every directory under ``path`` that contains a ``.git`` marker."""
        found: list[str] = []
        root = os.path.abspath(self._path)
        for dirpath, dirnames, _filenames in os.walk(root):
            if len(found) >= _MAX_REPOS:
                break
            depth = os.path.relpath(dirpath, root)
            if depth != "." and depth.count(os.sep) >= _MAX_DEPTH:
                dirnames[:] = []
                continue
            # Prune junk directories before descending.
            dirnames[:] = [d for d in sorted(dirnames) if d not in _SKIP_DIRS]
            marker = os.path.join(dirpath, ".git")
            if os.path.isdir(marker):
                found.append(dirpath)
                # A regular repository owns its whole working tree — stop
                # descending so a workspace of many repos is only walked once.
                # (Submodules still appear as `.git` *files*, handled below.)
                dirnames[:] = []
            elif os.path.isfile(marker):  # worktrees / submodules
                found.append(dirpath)
        return found

    def _fetch_all(self) -> dict:
        """Run fetch in the root repo (if any) and every nested repo."""
        repos = []
        if self._is_repo_root():
            repos.append(os.path.abspath(self._path))
        repos.extend(repo for repo in self._find_repos() if os.path.abspath(repo) not in repos)
        results: list[dict] = []
        total = max(1, len(repos))
        for index, repo in enumerate(repos, start=1):
            if self._cancelled:
                break
            self.report(int(index / total * 100), repo)
            run = self._git(("fetch", "--all", "--prune"), cwd=repo)
            results.append({"path": repo, "ok": run["ok"], "output": run["output"]})
        return {"repos": repos, "results": results}

    def _is_repo_root(self) -> bool:
        return self._git(("rev-parse", "--is-inside-work-tree"))["ok"]

    def _remote_status(self) -> dict:
        """Branch + ahead/behind vs the upstream — purely local (no network).

        Parses the branch line of ``git status --short --branch``:

            ## main...origin/main                  → up to date
            ## main...origin/main [ahead 1]        → ahead 1
            ## main...origin/main [behind 2]       → behind 2
            ## main...origin/main [ahead 1, behind 2] → diverged
            ## main                                → no upstream
            ## HEAD (no branch)                    → detached

        ``is_repo`` is False when ``path`` is not inside a working tree, so
        the UI can leave the card's status empty for plain folders.
        """
        check = self._git(("rev-parse", "--is-inside-work-tree"))
        if not check["ok"]:
            return {"is_repo": False, "branch": "", "ahead": 0, "behind": 0, "upstream": None}
        status = self._git(("status", "--short", "--branch"))
        branch = ""
        ahead = 0
        behind = 0
        upstream: str | None = None
        for line in status["output"].splitlines():
            if not line.startswith("##"):
                continue
            body = line[2:].strip()
            # A branch with no commits yet prints a status line instead of a
            # plain branch name (git 2.32+: ``No commits yet on main``, older
            # git: ``Initial commit on main``) — extract the branch and stop.
            if body.startswith("No commits yet on "):
                branch = body[len("No commits yet on "):]
                break
            if body.startswith("Initial commit on "):
                branch = body[len("Initial commit on "):]
                break
            if body in ("No commits yet", "Initial commit"):  # ancient git
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

    # -- plumbing -------------------------------------------------------------------

    def _git(self, args: tuple[str, ...], cwd: str | None = None) -> dict:
        """Run git; returns {ok, output, code}. Raises on missing binary/timeout."""
        try:
            proc = subprocess.run(
                [self._executable, *args],
                cwd=cwd or self._path,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"git executable not found: {self._executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"git {' '.join(args)} timed out after {self._timeout}s") from exc
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return {"ok": proc.returncode == 0, "output": output, "code": proc.returncode}
