"""Dependency injection container.

A tiny, dependency-free DI container: register factories or singletons by a
name, resolve lazily, and branch child scopes (each plugin host gets one).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ContainerError(Exception):
    """Raised when a dependency cannot be resolved."""


class DependencyContainer:
    """Resolves dependencies registered by dotted key, e.g. ``services.git.GitService``.

    Factories are invoked lazily on first ``resolve``; singletons are shared.
    Child scopes fall back to their parent for lookups, so a plugin can only
    *add* registrations, never shadow core services.
    """

    def __init__(self, parent: "DependencyContainer | None" = None) -> None:
        self._parent = parent
        self._factories: dict[str, Callable[[], Any]] = {}
        self._singletons: dict[str, Any] = {}

    # -- registration ---------------------------------------------------------

    def register_factory(self, key: str, factory: Callable[[], Any]) -> None:
        """Register a lazy factory for ``key`` (invoked on first resolve)."""
        self._factories[key] = factory

    def register_singleton(self, key: str, instance: Any) -> None:
        """Register an already-constructed instance shared by all resolvers."""
        self._singletons[key] = instance

    # -- resolution -------------------------------------------------------------

    def resolve(self, key: str) -> Any:
        """Resolve ``key`` from this scope, then parent scopes."""
        if key in self._singletons:
            return self._singletons[key]
        if key in self._factories:
            instance = self._factories[key]()
            # Factories are treated as singletons for stability: resolve once.
            self._singletons[key] = instance
            return instance
        if self._parent is not None:
            return self._parent.resolve(key)
        raise ContainerError(f"no dependency registered for {key!r}")

    def has(self, key: str) -> bool:
        if key in self._singletons or key in self._factories:
            return True
        return bool(self._parent is not None and self._parent.has(key))

    # -- scoping -----------------------------------------------------------------

    def child_scope(self) -> "DependencyContainer":
        """Create a child scope: reads fall through to this container."""
        return DependencyContainer(parent=self)

    def clear(self) -> None:
        """Drop all local registrations and cached instances."""
        self._factories.clear()
        self._singletons.clear()
