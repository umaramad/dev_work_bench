"""AppShell — the base layout controller for the DevWorkbench Flet UI.

Owns the ``ft.Page`` and composes the responsive shell: a left
NavigationRail (sidebar) plus a content area that fills the rest of the
window. Screens are registered in ``screens`` and swapped into the
content area on navigation; the rail collapses to icon-only on narrow
windows so the layout stays usable at any size.

Design system (see ``theme``): deep-slate background, surface cards with
rounded corners, subtle borders and soft shadows, consistent 20px
padding.

Usage (entry point)::

    def main(page: ft.Page) -> None:
        AppShell(page).mount()

    ft.run(main)
"""

from __future__ import annotations

import logging
from pathlib import Path

import flet as ft

from devworkbench.flet_ui import theme
from devworkbench.flet_ui.screens import MODULE_SCREENS, build_screen

logger = logging.getLogger("devworkbench.flet_ui.shell")

# ---------------------------------------------------------------------------
# Window spec (defaults; the OS lets the user resize beyond these)
# ---------------------------------------------------------------------------

WINDOW_WIDTH = 1024
WINDOW_HEIGHT = 720
WINDOW_MIN_WIDTH = 640
WINDOW_MIN_HEIGHT = 480

# Below this width the rail switches to icon-only to save space.
RAIL_COLLAPSE_WIDTH = 900

# Rail metrics.
RAIL_WIDTH = 168
RAIL_COLLAPSED_WIDTH = 72


class AppShell:
    """Composes the page: window, theme, rail, content and navigation."""

    def __init__(self, page: ft.Page, screens: list | None = None, backend: dict | None = None) -> None:
        self.page = page
        # Backend services for the screens (favorites repo, git service, …);
        # built by the entry point (main.py). Screens read ``shell.backend``.
        self.backend = dict(backend or {})
        # Full catalog; ``_screens`` is the Menus-filtered visible subset.
        self._all_screens = list(screens or MODULE_SCREENS)
        self._screens = self._visible_screens()
        self._current_index = 0
        self._current_id: str | None = self._screens[0].id if self._screens else None

        self._rail: ft.NavigationRail | None = None
        self._rail_host: ft.Container | None = None
        self._content: ft.Container | None = None
        self._header_title: ft.Text | None = None
        self._body: ft.Container | None = None

    # -- lifecycle -------------------------------------------------------------

    def mount(self) -> None:
        """Configure the window + theme and add the shell to the page."""
        page = self.page
        page.title = "DevWorkbench"

        # Window spec: 1024x720 default, responsive auto-resize with a sane
        # minimum so the layout never collapses into unusable space.
        page.window.width = WINDOW_WIDTH
        page.window.height = WINDOW_HEIGHT
        page.window.min_width = WINDOW_MIN_WIDTH
        page.window.min_height = WINDOW_MIN_HEIGHT
        self._apply_window_icon(page)

        # Dark by default — the palette from theme.py.
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = ft.Theme(color_scheme_seed=theme.ACCENT, visual_density=ft.VisualDensity.COMPACT)
        page.bgcolor = theme.BG
        page.padding = 0

        page.on_resize = self._on_resize
        page.add(self._build())
        if self._screens:
            self.show_screen(0)
        # Resize may fire before the first layout pass — sync the rail now.
        self._apply_responsive_rail()

    def _apply_window_icon(self, page: ft.Page) -> None:
        """Use the same DevWorkbench icon as the PySide6 app (not Flet default)."""
        candidates: list[Path] = []
        paths = self.backend.get("paths")
        if paths is not None:
            candidates.append(Path(paths.resources_dir) / "icons" / "DevWorkbench.png")
            candidates.append(Path(paths.resources_dir) / "icons" / "DevWorkbench.icns")
        # Fallback when DB/paths failed to wire: resolve from this package → repo root.
        here = Path(__file__).resolve()
        repo_resources = here.parents[3] / "resources" / "icons"
        candidates.append(repo_resources / "DevWorkbench.png")
        candidates.append(repo_resources / "DevWorkbench.icns")
        for icon in candidates:
            if icon.is_file():
                page.window.icon = str(icon)
                return

    def _visible_screens(self) -> list:
        """Screens enabled in Settings → Menus (Settings itself is always shown)."""
        config = self.backend.get("config")
        visible = []
        for screen in self._all_screens:
            if screen.id == "settings":
                visible.append(screen)
                continue
            if config is None:
                # No config (demo mode): show everything so the shell stays useful.
                visible.append(screen)
                continue
            key = f"ui.show_{screen.id}"
            try:
                if bool(config.get(key)):
                    visible.append(screen)
            except KeyError:
                # Unknown module id — keep it visible rather than hiding silently.
                visible.append(screen)
        return visible

    def refresh_navigation(self) -> None:
        """Rebuild the left rail after Settings → Menus Apply/Save.

        Keeps the current screen mounted when it is still visible so an
        in-progress Settings form is not torn down mid-Apply.
        """
        previous_id = self._current_id
        self._screens = self._visible_screens()
        if not self._screens:
            return
        if self._rail is not None:
            self._rail.destinations = [
                ft.NavigationRailDestination(
                    icon=screen.icon,
                    selected_icon=screen.icon,
                    label=screen.title,
                    tooltip=screen.title,
                )
                for screen in self._screens
            ]
        target = 0
        still_visible = False
        if previous_id is not None:
            for index, screen in enumerate(self._screens):
                if screen.id == previous_id:
                    target = index
                    still_visible = True
                    break
        if still_visible:
            self._current_index = target
            if self._rail is not None:
                self._rail.selected_index = target
            self.page.update()
            self._apply_responsive_rail()
            return
        self.show_screen(target)
        self._apply_responsive_rail()

    def _build(self) -> ft.Row:
        """Compose the rail + content row (expands with the window)."""
        rail = self._build_rail()
        self._rail_host = ft.Container(
            content=rail,
            bgcolor=theme.SURFACE,
            border=theme.border_all(),
            border_radius=theme.radius_all(theme.RADIUS_LARGE),
            margin=ft.Margin(left=theme.PADDING, top=theme.PADDING, bottom=theme.PADDING, right=0),
            padding=theme.padding_xy(10, 14),
        )
        rail_host = self._rail_host

        # Content area: header + scrollable body, fills remaining width.
        self._header_title = ft.Text(
            "", size=18, weight=ft.FontWeight.W_600, color=theme.TEXT
        )
        # Explicit bgcolor so a blank screen is distinguishable from "nothing mounted".
        self._body = ft.Container(expand=True, bgcolor=theme.BG)
        content = ft.Column(
            [
                ft.Row(
                    [
                        self._header_title,
                        ft.Container(expand=True),
                        ft.Text("Ready", size=12, color=theme.TEXT_MUTED),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self._body,
            ],
            expand=True,
            spacing=theme.GAP,
        )
        self._content = ft.Container(
            content=content,
            bgcolor=theme.BG,
            padding=theme.padding_all(theme.PADDING),
            expand=True,
        )

        return ft.Row(
            [rail_host, self._content],
            expand=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    # -- rail -------------------------------------------------------------------

    def _build_rail(self) -> ft.NavigationRail:
        self._rail = ft.NavigationRail(
            selected_index=self._current_index,
            label_type=ft.NavigationRailLabelType.ALL,
            extended=True,
            min_width=RAIL_COLLAPSED_WIDTH,
            min_extended_width=RAIL_WIDTH,
            bgcolor=ft.Colors.TRANSPARENT,
            indicator_color=theme.ACCENT,
            leading=self._rail_leading(),
            destinations=[
                ft.NavigationRailDestination(
                    icon=screen.icon,
                    selected_icon=screen.icon,
                    label=screen.title,
                    tooltip=screen.title,
                )
                for screen in self._screens
            ],
            on_change=self._on_rail_changed,
        )
        return self._rail

    def _rail_leading(self) -> ft.Control:
        """App mark in the rail — prefer the packaged PNG, else a speed icon."""
        paths = self.backend.get("paths")
        icon_path = None
        if paths is not None:
            candidate = Path(paths.resources_dir) / "icons" / "DevWorkbench.png"
            if candidate.is_file():
                icon_path = candidate
        if icon_path is None:
            fallback = Path(__file__).resolve().parents[3] / "resources" / "icons" / "DevWorkbench.png"
            if fallback.is_file():
                icon_path = fallback
        if icon_path is not None:
            return ft.Container(
                content=ft.Image(src=str(icon_path), width=32, height=32, fit=ft.BoxFit.CONTAIN),
                padding=theme.padding_all(4),
            )
        return ft.Icon(ft.Icons.SPEED, color=theme.ACCENT, size=30)

    def _on_rail_changed(self, event) -> None:
        index = int(event.control.selected_index)
        if index != self._current_index:
            self.show_screen(index)

    def _apply_responsive_rail(self) -> None:
        """Collapse the rail to icons on narrow windows, expand it on wide."""
        if self._rail is None:
            return
        narrow = (self.page.width or WINDOW_WIDTH) < RAIL_COLLAPSE_WIDTH
        self._rail.extended = not narrow
        self._rail.label_type = (
            ft.NavigationRailLabelType.ALL if not narrow else ft.NavigationRailLabelType.NONE
        )
        self.page.update()

    def _on_resize(self, _event=None) -> None:
        self._apply_responsive_rail()

    # -- navigation ---------------------------------------------------------------

    def show_screen(self, index: int) -> None:
        """Render the screen at ``index`` into the content area."""
        if not 0 <= index < len(self._screens):
            return
        self._current_index = index
        screen = self._screens[index]
        self._current_id = screen.id
        if self._rail is not None:
            self._rail.selected_index = index
        if self._header_title is not None:
            self._header_title.value = screen.title
        if self._body is not None:
            try:
                logger.info("show_screen id=%s index=%s", screen.id, index)
                built = build_screen(screen, self)
                self._body.content = built
                logger.info(
                    "show_screen mounted id=%s type=%s",
                    screen.id,
                    type(built).__name__,
                )
            except Exception as exc:  # noqa: BLE001 — show error instead of a blank panel
                logger.exception("show_screen failed for %s", screen.id)
                self._body.content = ft.Container(
                    expand=True,
                    bgcolor=theme.SURFACE,
                    padding=theme.padding_all(20),
                    content=ft.Text(
                        f"Failed to open {screen.title}: {exc}",
                        color=theme.ERR,
                        selectable=True,
                    ),
                )
        self.page.update()

    def navigate(self, module_id: str) -> None:
        """Programmatic navigation by module id (command palette, links)."""
        for index, screen in enumerate(self._screens):
            if screen.id == module_id:
                self.show_screen(index)
                return
        # Module hidden by Menus — ignore rather than crash.
