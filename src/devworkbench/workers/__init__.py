"""Concrete long-running workers.

Scheduling happens on the ``core.workers.TaskExecutor``; these classes are
the *work itself* (parsers, indexers, sessions) — one subclass per module.
"""

from devworkbench.workers.ai_worker import AiChatWorker
from devworkbench.workers.base import Worker
from devworkbench.workers.compare_worker import CompareWorker
from devworkbench.workers.folder_sync_worker import FolderSyncWorker
from devworkbench.workers.git_worker import GitWorker
from devworkbench.workers.log_worker import LogParserWorker
from devworkbench.workers.maven_worker import MavenPomWorker
from devworkbench.workers.ssh_worker import SshWorker

__all__ = [
    "AiChatWorker",
    "CompareWorker",
    "FolderSyncWorker",
    "GitWorker",
    "LogParserWorker",
    "MavenPomWorker",
    "SshWorker",
    "Worker",
]
