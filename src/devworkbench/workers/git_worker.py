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
    remote_status — branch + ahead/behind vs the upstream (local-only);
                    also returns dirty_count from the same porcelain parse
    working_tree_status — porcelain file list (local changes vs HEAD)
    diff_vs_upstream — name-status vs @{upstream} (or origin/<branch>)
    reset       — ``git reset`` (soft; ``hard=True`` → ``--hard``)
    has_branch  — does local ``refs/heads/<name>`` exist? (name in args[0])
    checkout    — ``git checkout <name>``; force and/or from remote tip
                  (``args=(name, "force", "origin")`` → ``checkout -f -B name origin/name``)
    run_cmd     — run a parsed ``git …`` command string (args[0]); git-only for safety

Signals contract: construct on the UI thread and retain until finished/error
(see ``workers/base.py``).
"""

from __future__ import annotations

import os
import shlex
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
    "working_tree_status": 30,
    "diff_vs_upstream": 45,
    "reset": 60,
    "has_branch": 15,
    "checkout": 30,
    "run_cmd": 120,
}


def _porcelain_label(code: str) -> str:
    """Map a 2-char porcelain XY code to a short display label."""
    code = (code + "  ")[:2]
    if code == "??":
        return "untracked"
    if "U" in code or code in ("DD", "AA", "AU", "UA", "DU", "UD"):
        return "conflict"
    index, work = code[0], code[1]
    if index == "A" or work == "A":
        return "added"
    if index == "D" or work == "D":
        return "deleted"
    if index == "R" or work == "R":
        return "renamed"
    if index == "C" or work == "C":
        return "copied"
    if index == "M" or work == "M":
        return "modified"
    return code.strip() or "changed"


def parse_porcelain_status(output: str) -> list[dict]:
    """Parse ``git status --porcelain=v1`` / ``--short`` body lines into file rows."""
    files: list[dict] = []
    seen: set[str] = set()
    for raw in (output or "").splitlines():
        line = raw.rstrip("\n")
        if not line or line.startswith("##"):
            continue
        if len(line) < 2:
            continue
        code = line[:2]
        rest = line[3:] if len(line) > 2 and line[2] == " " else line[2:].lstrip()
        path = rest
        if " -> " in rest:
            path = rest.split(" -> ", 1)[-1].strip()
        path = path.strip().strip('"')
        if not path or path in seen:
            continue
        seen.add(path)
        files.append({"code": code, "status": _porcelain_label(code), "path": path})
    return files


def parse_name_status(output: str) -> list[dict]:
    """Parse ``git diff --name-status`` lines into file rows."""
    files: list[dict] = []
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0].strip()
        letter = code[:1] if code else "?"
        path = parts[-1].strip()
        label = {
            "M": "modified",
            "A": "added",
            "D": "deleted",
            "R": "renamed",
            "C": "copied",
            "T": "typechange",
            "U": "unmerged",
        }.get(letter, code or "changed")
        if not path:
            continue
        files.append({"code": letter, "status": label, "path": path})
    return files


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
        if op == "working_tree_status":
            return self._working_tree_status()
        if op == "diff_vs_upstream":
            return self._diff_vs_upstream()
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
        if op == "run_cmd":
            raw = (self._args[0] if self._args else "").strip()
            if not raw:
                return {"ok": False, "output": "empty command"}
            try:
                argv = shlex.split(raw)
            except ValueError as exc:
                return {"ok": False, "output": f"parse error: {exc}"}
            if not argv:
                return {"ok": False, "output": "empty command"}
            if argv[0] != "git":
                return {"ok": False, "output": "only git commands are allowed (must start with git)"}
            # Drop the literal "git" token; _git prefixes the configured executable.
            return self._git(tuple(argv[1:]))
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

        A single ``git status`` doubles as the repo check — it fails cleanly
        outside a working tree (and in bare repos), so no separate
        ``rev-parse`` subprocess is needed. Halves the spawn cost of the
        per-card status refresh on the landing page.
        """
        status = self._git(("status", "--short", "--branch"))
        if not status["ok"]:
            return {
                "is_repo": False,
                "branch": "",
                "ahead": 0,
                "behind": 0,
                "upstream": None,
                "dirty_count": 0,
            }
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
        files = parse_porcelain_status(status["output"])
        return {
            "is_repo": True,
            "branch": branch,
            "ahead": ahead,
            "behind": behind,
            "upstream": upstream,
            "dirty_count": len(files),
        }

    def _working_tree_status(self) -> dict:
        """Working-tree file list (``git status --porcelain=v1``)."""
        check = self._git(("rev-parse", "--is-inside-work-tree"))
        if not check["ok"]:
            return {"ok": False, "files": [], "count": 0, "error": "Not a git repository"}
        result = self._git(("status", "--porcelain=v1"))
        if not result["ok"]:
            return {
                "ok": False,
                "files": [],
                "count": 0,
                "error": result["output"] or "git status failed",
            }
        files = parse_porcelain_status(result["output"])
        return {"ok": True, "files": files, "count": len(files), "error": None}

    def _diff_vs_upstream(self) -> dict:
        """Committed differences vs upstream (``git diff --name-status A...B``).

        Does not auto-fetch. Uncommitted changes are reported only as
        ``dirty_hint_count`` so Local changes stays the place for the working tree.
        """
        check = self._git(("rev-parse", "--is-inside-work-tree"))
        if not check["ok"]:
            return {
                "ok": False,
                "upstream": None,
                "files": [],
                "dirty_hint_count": 0,
                "error": "Not a git repository",
            }
        upstream, err = self._resolve_upstream()
        if not upstream:
            return {
                "ok": False,
                "upstream": None,
                "files": [],
                "dirty_hint_count": 0,
                "error": err or "No upstream configured — fetch or set upstream first.",
            }
        diff = self._git(("diff", "--name-status", f"{upstream}...HEAD"))
        if not diff["ok"]:
            return {
                "ok": False,
                "upstream": upstream,
                "files": [],
                "dirty_hint_count": 0,
                "error": diff["output"] or f"Could not diff against {upstream}",
            }
        files = parse_name_status(diff["output"])
        dirty = parse_porcelain_status(self._git(("status", "--porcelain=v1"))["output"])
        return {
            "ok": True,
            "upstream": upstream,
            "files": files,
            "dirty_hint_count": len(dirty),
            "error": None,
        }

    def _resolve_upstream(self) -> tuple[str | None, str | None]:
        """Return (upstream_ref, error). Prefers @{upstream}, then origin/<branch>."""
        up = self._git(("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"))
        if up["ok"] and up["output"].strip():
            return up["output"].strip(), None
        branch = self._git(("rev-parse", "--abbrev-ref", "HEAD"))["output"].strip()
        if not branch or branch == "HEAD":
            return None, "Detached HEAD — no upstream to compare."
        remote_ref = f"origin/{branch}"
        exists = self._git(("show-ref", "--verify", "--quiet", f"refs/remotes/{remote_ref}"))
        if exists["ok"]:
            return remote_ref, None
        return None, "No upstream configured — fetch or set upstream first."

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
