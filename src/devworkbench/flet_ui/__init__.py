"""DevWorkbench Flet UI layer.

The migration target for the PySide6 shell (``devworkbench.ui``): a
dark-first, responsive Flet application. During the migration both UIs
coexist — this package imports nothing from the Qt layer, so the PySide6
app and its tests are unaffected.

Layout: ``shell.AppShell`` owns the page, the NavigationRail and the
content area; ``screens`` registers the module screens (placeholders
today, real screens as they are ported); ``theme`` holds the design
tokens and the small helpers every screen uses.
"""

from devworkbench.flet_ui.shell import AppShell

__all__ = ["AppShell"]
