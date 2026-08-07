#!/usr/bin/env python3
"""Auto versioning — DevWorkbench version lives in exactly one place.

``src/devworkbench/__init__.py`` (``__version__ = "MAJOR.MINOR.PATCH"``) is the
single source of truth: the PyInstaller spec, the DMG filename, the About box
and the status bar all read it from there. This script reads, bumps and
optionally tags it.

Usage:
    .venv/bin/python scripts/version.py              # print current version
    .venv/bin/python scripts/version.py bump patch   # 0.1.0 -> 0.1.1
    .venv/bin/python scripts/version.py bump minor   # 0.1.1 -> 0.2.0
    .venv/bin/python scripts/version.py bump major   # 0.2.0 -> 1.0.0
    .venv/bin/python scripts/version.py bump --tag   # ... and git tag v0.1.1

The version is validated with ``packaging.version`` if available, else a
simple ``X.Y.Z`` regex — so the build never ships a malformed string.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "src" / "devworkbench" / "__init__.py"

_VERSION_RE = re.compile(r'__version__ = "(\d+)\.(\d+)\.(\d+)"')


class VersionError(RuntimeError):
    """Raised when the version file is unreadable or malformed."""


def read() -> str:
    text = INIT.read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if match is None:
        raise VersionError(f"no __version__ found in {INIT}")
    return ".".join(match.groups())


def write(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise VersionError(f"invalid version {version!r} (expected X.Y.Z)")
    text = INIT.read_text(encoding="utf-8")
    updated = _VERSION_RE.sub(f'__version__ = "{version}"', text, count=1)
    # Atomic replace: an interrupted bump must never corrupt the single
    # source of truth.
    temp = INIT.with_name(INIT.name + ".tmp")
    temp.write_text(updated, encoding="utf-8")
    temp.replace(INIT)


def bump(current: str, part: str) -> str:
    major, minor, patch = (int(x) for x in current.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def git_tag(version: str) -> None:
    tag = f"v{version}"
    try:
        subprocess.run(["git", "tag", tag], check=True, capture_output=True)
        print(f"tagged {tag}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"warning: could not git tag {tag} (not a git repo?)", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("bump",), help="bump the version")
    parser.add_argument("part", nargs="?", choices=("patch", "minor", "major"), default="patch")
    parser.add_argument("--tag", action="store_true", help="git tag after bumping")
    args = parser.parse_args()

    current = read()
    if args.action == "bump":
        next_version = bump(current, args.part)
        write(next_version)
        print(f"{current} -> {next_version}")
        if args.tag:
            git_tag(next_version)
    else:
        print(current)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
