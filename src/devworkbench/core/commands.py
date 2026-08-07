"""Command pattern — undoable units of work (diff edits, compare actions…)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class Command(ABC):
    """One undoable action."""

    @abstractmethod
    def execute(self) -> None:
        """Apply the action."""

    def undo(self) -> None:
        """Reverse the action; commands without undo raise by default."""
        raise NotImplementedError(f"{type(self).__name__} does not support undo")

    def redo(self) -> None:
        """Re-apply after undo (defaults to execute)."""
        self.execute()

    @property
    def description(self) -> str:
        """Human-readable label for history UI."""
        return type(self).__name__


class CommandStack:
    """Bounded undo/redo history with a dirty flag."""

    def __init__(self, max_depth: int = 50) -> None:
        self._max_depth = max_depth
        self._undo_stack: list[Command] = []
        self._redo_stack: list[Command] = []
        self._dirty = False
        self._baseline = 0

    def push(self, command: Command) -> None:
        """Execute ``command`` and record it for undo."""
        command.execute()
        self._undo_stack.append(command)
        if len(self._undo_stack) > self._max_depth:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._dirty = True

    def undo(self) -> bool:
        """Undo the most recent command; returns whether anything was undone."""
        if not self._undo_stack:
            return False
        command = self._undo_stack.pop()
        command.undo()
        self._redo_stack.append(command)
        self._dirty = True
        return True

    def redo(self) -> bool:
        """Re-apply the most recently undone command."""
        if not self._redo_stack:
            return False
        command = self._redo_stack.pop()
        command.redo()
        self._undo_stack.append(command)
        self._dirty = True
        return True

    def clear(self) -> None:
        """Drop all history and reset the dirty flag."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._dirty = False

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def dirty(self) -> bool:
        return self._dirty
