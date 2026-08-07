"""Domain models — plain dataclasses shared between services and views."""

from devworkbench.models.ai import AiMessage, AiProviderConfig, AiSession
from devworkbench.models.base import Model
from devworkbench.models.comparison import CompareSession, DiffFile, DiffHunk, DiffLine, DiffStats
from devworkbench.models.git import BlameLine, BranchInfo, CommitInfo, RepoStatus, StagedFile
from devworkbench.models.log import LogEntry, LogFile, LogFilter, LogPattern
from devworkbench.models.persistence import (
    Favorite,
    HistoryEntry,
    PluginState,
    Project,
    RecentFile,
    RecentFolder,
    RepositoryRecord,
    SettingsEntry,
    SshServerRecord,
)
from devworkbench.models.plugin import PluginRecord
from devworkbench.models.ssh import RemoteFile, SshProfile, TransferTask

__all__ = [
    "AiMessage",
    "AiProviderConfig",
    "AiSession",
    "BlameLine",
    "BranchInfo",
    "CommitInfo",
    "CompareSession",
    "DiffFile",
    "DiffHunk",
    "DiffLine",
    "DiffStats",
    "Favorite",
    "HistoryEntry",
    "LogEntry",
    "LogFile",
    "LogFilter",
    "LogPattern",
    "Model",
    "PluginRecord",
    "PluginState",
    "Project",
    "RecentFile",
    "RecentFolder",
    "RemoteFile",
    "RepoStatus",
    "RepositoryRecord",
    "SettingsEntry",
    "SshProfile",
    "SshServerRecord",
    "StagedFile",
    "TransferTask",
]
