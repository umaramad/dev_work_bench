"""Event bus — typed pub/sub used to decouple modules.

``publish`` dispatches synchronously on the current thread (UI thread by
convention). ``post`` enqueues from any thread; drain the queue with
``publish_pending`` from the event loop (a timer in the UI layer).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from threading import Lock

Topic = str
Callback = Callable[..., None]

_logger = logging.getLogger("devworkbench.core.events")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[Topic, list[Callback]] = defaultdict(list)
        self._pending: list[tuple[Topic, dict]] = []
        self._lock = Lock()

    # -- subscription -------------------------------------------------------------

    def subscribe(self, topic: Topic, callback: Callback) -> Callback:
        """Subscribe to ``topic``; returns the callback (usable for cleanup)."""
        with self._lock:
            self._subscribers[topic].append(callback)
        return callback

    def unsubscribe(self, topic: Topic, callback: Callback) -> None:
        """Remove a previously registered callback."""
        with self._lock:
            try:
                self._subscribers[topic].remove(callback)
            except ValueError:
                _logger.debug("unsubscribe: %r not subscribed to %r", callback, topic)

    # -- dispatch -----------------------------------------------------------------

    def publish(self, topic: Topic, **payload) -> None:
        """Dispatch ``topic`` synchronously to all subscribers."""
        with self._lock:
            callbacks = list(self._subscribers.get(topic, ()))
        for callback in callbacks:
            try:
                callback(**payload)
            except Exception:  # noqa: BLE001 — one bad subscriber must not break the bus
                _logger.exception("subscriber failed for topic %r", topic)

    def post(self, topic: Topic, **payload) -> None:
        """Enqueue an event from any thread; delivered by ``publish_pending``."""
        with self._lock:
            self._pending.append((topic, payload))

    def publish_pending(self) -> int:
        """Drain queued ``post`` events (call on the UI thread). Returns count."""
        with self._lock:
            pending, self._pending = self._pending, []
        for topic, payload in pending:
            self.publish(topic, **payload)
        return len(pending)
