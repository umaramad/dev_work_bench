"""LogParserWorker — streaming parse of large log files in chunks."""

from __future__ import annotations

from pathlib import Path

from devworkbench.workers.base import Worker


class LogParserWorker(Worker):
    """Parses a log file chunk-by-chunk, reporting progress.

    The parse *rules* come from the log-analyzer service; this worker only
    drives the file I/O and progress reporting.
    """

    def __init__(self, path: str | Path, chunk_lines: int = 4096) -> None:
        super().__init__()
        self._path = Path(path)
        self._chunk_lines = chunk_lines

    def work(self):
        """Yield parsed entries and index updates to the caller."""
        # Wired to LogService + LogIndexer (FTS5) in the services milestone.
        raise NotImplementedError("LogParserWorker will be wired to LogService/LogIndexer")
