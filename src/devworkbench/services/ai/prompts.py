"""Provider-agnostic prompts for the built-in AI tasks.

These builders return plain ``ChatMessage`` lists (system + user), so every
provider produces the same prompt for the same input — the only thing that
varies between providers is transport/parsing, never wording.
"""

from __future__ import annotations

from devworkbench.services.ai.base import ChatMessage

_DIFF_SYSTEM = (
    "You are an expert software engineer. Explain the given code diff clearly "
    "and concisely: what changed, why it might have changed, and any risks or "
    "follow-ups. Use short sections with markdown bullets."
)

_COMMIT_SYSTEM = (
    "You are an expert at writing git commit messages. Given the changed "
    "files and diff, write exactly one commit message — no commentary, no "
    "markdown fences. Match the requested style."
)

_LOG_SYSTEM = (
    "You are a senior site-reliability engineer. Analyze the provided log "
    "lines: summarize what happened, identify the root cause of failures, "
    "and suggest concrete next steps. Be specific and concise."
)


def _user_block(body: str, extra: str | None = None) -> str:
    if extra:
        return f"{body}\n\nContext:\n{extra}"
    return body


def explain_diff_messages(diff_text: str, context: str | None = None) -> list[ChatMessage]:
    """Prompt to explain a unified diff."""
    user = f"Here is the diff:\n\n```diff\n{diff_text}\n```"
    return [
        ChatMessage("system", _DIFF_SYSTEM),
        ChatMessage("user", _user_block(user, context)),
    ]


def commit_messages(
    changes: str,
    style: str = "conventional",
    context: str | None = None,
) -> list[ChatMessage]:
    """Prompt to draft a commit message for ``changes`` (status/diff text)."""
    user = (
        f"Write a commit message in the '{style}' style for these changes:\n\n"
        f"```\n{changes}\n```"
    )
    return [
        ChatMessage("system", _COMMIT_SYSTEM),
        ChatMessage("user", _user_block(user, context)),
    ]


def log_analysis_messages(
    log_text: str,
    level: str = "ERROR",
    focus: str | None = None,
) -> list[ChatMessage]:
    """Prompt to analyze log lines, focusing on ``level`` (and optional focus)."""
    focus_line = f"\nFocus your analysis on: {focus}." if focus else ""
    user = (
        f"Analyze these log lines, paying special attention to {level} "
        f"entries:{focus_line}\n\n```log\n{log_text}\n```"
    )
    return [
        ChatMessage("system", _LOG_SYSTEM),
        ChatMessage("user", user),
    ]
