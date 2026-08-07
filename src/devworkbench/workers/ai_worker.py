"""AiChatWorker — runs an AI completion off the UI thread.

Emits ``finished(AIResult)`` or ``error(AIError)`` through the shared
Worker signals; created on the UI thread so signal delivery is queued.
"""

from __future__ import annotations

from devworkbench.services.ai.base import AIProvider
from devworkbench.workers.base import Worker


class AiChatWorker(Worker):
    """Calls ``provider.chat`` with the current conversation."""

    def __init__(
        self,
        provider: AIProvider,
        messages,
        system: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__()
        self._provider = provider
        self._messages = messages
        self._system = system
        self._model = model

    def work(self):
        """Run the completion; returns the assistant ``AIResult``."""
        return self._provider.chat(
            self._messages,
            system=self._system,
            model=self._model,
        )
