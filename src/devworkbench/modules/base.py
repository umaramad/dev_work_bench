"""Module — the UI contract every built-in screen satisfies.

UI-scaffold version of the future ``ModulePlugin`` (docs/architecture/02):
``build`` creates the screen, the other fields feed the shell (sidebar,
navigator tree, details inspector, status text).

Each view receives a :class:`ModuleContext` so it can resolve services
through the DI container — modules never reach for globals or construct
their own infrastructure.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QWidget


class ModuleContext:
    """Read-only services facade handed to module views at build time."""

    def __init__(self, container) -> None:
        self._container = container

    def resolve(self, key: str):
        """Resolve a service from the DI container (raises if unknown)."""
        return self._container.resolve(key)

    def has(self, key: str) -> bool:
        """True when the container can resolve ``key``."""
        return self._container.has(key)

    @property
    def container(self):
        return self._container


class Module:
    def __init__(
        self,
        id: str,
        title: str,
        icon: str,
        build: Callable[[object, ModuleContext | None], QWidget],
        navigator: tuple[tuple[str, tuple[str, ...]], ...] = (),
        details: tuple[tuple[str, str], ...] = (),
        status: str = "Ready",
    ) -> None:
        self.id = id
        self.title = title
        self.icon = icon
        self.build = build
        self.navigator = navigator
        self.details = details
        self.status = status

    def navigator_items(self) -> list[tuple[str, list[str]]]:
        return [(label, list(children)) for label, children in self.navigator]
