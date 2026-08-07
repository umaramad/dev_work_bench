"""Core framework — no business logic.

The framework every module builds on: dependency injection, event bus,
command stack, background tasks, settings, paths, configuration, logging.

``core.workers`` holds the executor; concrete long-running workers live in
``devworkbench.workers``.
"""
