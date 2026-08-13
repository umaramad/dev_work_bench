"""Screen registry for the Flet shell.

Each built-in module becomes one screen (mirroring the PySide6 module
list). ``MODULE_SCREENS`` drives the NavigationRail destinations and the
content area; ``build_screen`` returns the screen's root control. Today
every screen is a styled placeholder that demonstrates the design system
— the real ported screens land here one by one as the migration
progresses (compare.py, git.py, …).
"""

from __future__ import annotations

from dataclasses import dataclass

import flet as ft

from devworkbench.flet_ui import theme

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenSpec:
    id: str
    title: str
    icon: str
    description: str


MODULE_SCREENS: list[ScreenSpec] = [
    ScreenSpec(
        "compare",
        "Compare",
        ft.Icons.COMPARE,
        "Diff files and folders side by side — lines, folders and binary files.",
    ),
    ScreenSpec(
        "git",
        "Git",
        ft.Icons.ACCOUNT_TREE,
        "Grouped repository tiles, per-repo operations and branch status.",
    ),
    ScreenSpec(
        "ai",
        "AI Assistant",
        ft.Icons.SMART_TOY,
        "Chat with your configured AI providers (OpenAI, Anthropic, Gemini, Ollama).",
    ),
    ScreenSpec(
        "ssh",
        "SSH",
        ft.Icons.TERMINAL,
        "Saved SSH servers, connections and quick commands.",
    ),
    ScreenSpec(
        "loganalyzer",
        "Log Analyzer",
        ft.Icons.DESCRIPTION,
        "Parse and analyze log files with structured filters.",
    ),
    ScreenSpec(
        "settings",
        "Settings",
        ft.Icons.SETTINGS,
        "Appearance, git, AI providers, SSH keys and menu visibility.",
    ),
    ScreenSpec(
        "plugins",
        "Plugins",
        ft.Icons.EXTENSION,
        "Manage installed plugins and their permissions.",
    ),
]

SCREEN_BY_ID: dict[str, ScreenSpec] = {screen.id: screen for screen in MODULE_SCREENS}


# ---------------------------------------------------------------------------
# Placeholder screens
# ---------------------------------------------------------------------------


def _placeholder(screen: ScreenSpec, shell) -> ft.Container:
    """A styled placeholder panel proving the design system works end to end."""
    icon = ft.Container(
        content=ft.Icon(screen.icon, color=theme.ACCENT, size=44),
        bgcolor=theme.ACCENT_SOFT,
        border_radius=theme.radius_all(theme.RADIUS_LARGE),
        padding=theme.padding_all(16),
    )
    title = ft.Text(screen.title, size=18, weight=ft.FontWeight.W_600, color=theme.TEXT)
    description = ft.Text(
        screen.description,
        size=13,
        color=theme.TEXT_MUTED,
        selectable=True,
    )
    pill = ft.Container(
        content=ft.Text("Migrating to Flet", size=11, color=theme.OK),
        bgcolor=theme.SURFACE_HOVER,
        border=theme.border_all(),
        border_radius=theme.radius_all(99),
        padding=theme.padding_xy(10, 4),
    )
    return theme.card(
        ft.Column(
            [
                ft.Row([icon, ft.Column([title, description], spacing=2)], spacing=14),
                ft.Divider(height=1, color=theme.BORDER),
                ft.Row([pill, ft.Container(expand=True)]),
            ],
            spacing=16,
        ),
        expand=True,
    )


def build_screen(screen: ScreenSpec, shell) -> ft.Container:
    """Return the root control for ``screen``.

    Screens that have been migrated to Flet dispatch to their real module;
    the rest render a styled placeholder until they are ported.
    """
    if screen.id == "git":
        from devworkbench.flet_ui.screens.git import build_git_screen

        return build_git_screen(shell)
    if screen.id == "settings":
        from devworkbench.flet_ui.screens.settings import build_settings_screen

        return build_settings_screen(shell)
    if screen.id == "compare":
        from devworkbench.flet_ui.screens.compare import build_compare_screen

        return build_compare_screen(shell)
    return _placeholder(screen, shell)
