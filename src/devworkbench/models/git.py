"""Git-module models."""

from __future__ import annotations

from dataclasses import dataclass, field

from devworkbench.models.base import Model


@dataclass
class StagedFile(Model):
    path: str = ""
    status: str = ""          # A / M / D / R / ??
    staged: bool = False


@dataclass
class RepoStatus(Model):
    repository: str = ""
    branch: str = "main"
    staged: list[StagedFile] = field(default_factory=list)
    unstaged: list[StagedFile] = field(default_factory=list)


@dataclass
class CommitInfo(Model):
    sha: str = ""
    author: str = ""
    when: str = ""
    message: str = ""


@dataclass
class BranchInfo(Model):
    name: str = ""
    active: bool = False
    upstream: str = ""


@dataclass
class BlameLine(Model):
    sha: str = ""
    author: str = ""
    date: str = ""
    text: str = ""
