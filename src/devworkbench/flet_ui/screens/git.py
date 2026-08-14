"""Git screen (Flet) — parity with ``devworkbench.modules.git``.

Split landing: left group list, right repos for the selected group,
bulk Fetch/Status/Reset, and a collapsed console at the bottom.
Uses Column scrolling (no nested ListView/GridView expand) for Flet 0.86.
Debug logs go to ``<log_dir>/flet_git.log``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import flet as ft

from devworkbench.flet_ui import theme
from devworkbench.models.persistence import Favorite
from devworkbench.services.git import GitService

_DEMO_REPOS = ("dev_work_bench", "freebuff-desktop", "scripts", "dotfiles")
_GROUP_WIDTH = 240
_CONSOLE_HEIGHT = 200
logger = logging.getLogger("devworkbench.flet_ui.git")


def _setup_file_logging(backend: dict) -> None:
    """Attach a file handler once so blank-screen issues leave a trail."""
    if any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        return
    logger.setLevel(logging.DEBUG)
    log_dir = Path("data/logs")
    paths = backend.get("paths")
    if paths is not None:
        try:
            log_dir = Path(paths.log_dir)
        except Exception:  # noqa: BLE001
            pass
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / "flet_git.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.debug("git file logging → %s", log_dir / "flet_git.log")
    except Exception:  # noqa: BLE001
        logger.exception("could not open git log file")


class _GitView:
    def __init__(self, shell) -> None:
        self.page = shell.page
        self._backend = getattr(shell, "backend", None) or {}
        _setup_file_logging(self._backend)
        self._favorites = self._backend.get("favorites")
        self._history = self._backend.get("history")
        self._config = self._backend.get("config")
        self._git = self._backend.get("git") or GitService()
        self._sync_git_executable()

        self._group: str | None = None
        self._search = ""
        self._status_cache: dict[str, dict] = {}
        self._open_path: str | None = None
        self._bulk_busy = False
        self._console_open = False
        self._console_lines: list[str] = []

        self._card_buttons: dict[str, list[ft.Control]] = {}
        self._card_rings: dict[str, ft.ProgressRing] = {}
        self._card_status: dict[str, ft.Text] = {}
        self._card_badges: dict[str, ft.Text] = {}

        self._groups_col = ft.Column(spacing=4, tight=True)
        self._repos_col = ft.Column(spacing=10, tight=True)
        self._search_field = ft.TextField(
            label="Filter",
            hint_text="Filter by name or path…",
            prefix_icon=ft.Icons.SEARCH,
            border_radius=theme.RADIUS,
            focused_border_color=theme.ACCENT,
            on_change=self._on_search,
            expand=True,
            dense=True,
        )
        self._group_title = ft.Text("", size=16, weight=ft.FontWeight.W_600, color=theme.TEXT)
        self._back_button = ft.IconButton(
            icon=ft.Icons.ARROW_BACK,
            icon_color=theme.TEXT_MUTED,
            tooltip="Close opened folder",
            on_click=lambda _e: self._back_to_groups(),
            visible=False,
        )
        self._bulk_fetch = ft.OutlinedButton(
            content="Fetch all",
            icon=ft.Icons.CLOUD_DOWNLOAD,
            on_click=lambda _e: self._run_bulk("fetch"),
        )
        self._bulk_status = ft.OutlinedButton(
            content="Status all",
            icon=ft.Icons.SEARCH,
            on_click=lambda _e: self._run_bulk("status"),
        )
        self._bulk_reset = ft.OutlinedButton(
            content="Reset all",
            icon=ft.Icons.REFRESH,
            on_click=lambda _e: self._run_bulk("reset"),
        )
        self._bulk_ring = ft.ProgressRing(
            width=16, height=16, stroke_width=2, color=theme.ACCENT, visible=False
        )
        self._console_chevron = ft.Text("▸", size=14, color=theme.TEXT_MUTED)
        self._console_status = ft.Text("idle", size=12, color=theme.TEXT_MUTED, expand=True)
        self._console_body = ft.Column(spacing=2, tight=True)
        self._console_scroll = ft.Container(
            content=ft.Column(
                [self._console_body],
                expand=True,
                scroll=ft.ScrollMode.AUTO,
                auto_scroll=True,
            ),
            height=_CONSOLE_HEIGHT,
            bgcolor=theme.BG,
            border=theme.border_all(),
            border_radius=theme.radius_all(theme.RADIUS),
            padding=theme.padding_all(10),
            visible=False,
        )
        self._console_clear = ft.TextButton(
            content="Clear",
            on_click=lambda _e: self._clear_console(),
            visible=False,
        )
        self._restore_view_state()
        logger.info(
            "GitView init favorites=%s group=%r search=%r",
            "yes" if self._favorites else "no",
            self._group,
            self._search,
        )

    def build(self) -> ft.Control:
        logger.info("GitView.build start")
        actions = ft.Row(
            [
                ft.OutlinedButton(content="Open folder", icon=ft.Icons.FOLDER_OPEN, on_click=lambda _e: self._open_folder()),
                ft.OutlinedButton(content="Scan…", icon=ft.Icons.TRAVEL_EXPLORE, on_click=lambda _e: self._show_scan_dialog()),
                ft.OutlinedButton(content="Manage groups", icon=ft.Icons.FOLDER_COPY, on_click=lambda _e: self._show_manage_groups()),
                ft.FilledButton(content="Add repository", icon=ft.Icons.ADD, on_click=lambda _e: self._show_add_dialog()),
            ],
            wrap=True,
            spacing=8,
        )
        bulk_bar = ft.Row(
            [self._bulk_fetch, self._bulk_status, self._bulk_reset, self._bulk_ring],
            spacing=8,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        right_header = ft.Column(
            [
                ft.Row(
                    [self._back_button, self._group_title, ft.Container(expand=True), self._search_field],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                ),
                bulk_bar,
            ],
            spacing=8,
            tight=True,
        )
        left_pane = ft.Container(
            width=_GROUP_WIDTH,
            content=ft.Column(
                [
                    ft.Text("Groups", size=12, weight=ft.FontWeight.W_600, color=theme.TEXT_MUTED),
                    ft.Container(
                        expand=True,
                        content=ft.Column(
                            [self._groups_col],
                            expand=True,
                            scroll=ft.ScrollMode.AUTO,
                        ),
                    ),
                ],
                expand=True,
                spacing=8,
            ),
        )
        right_pane = ft.Container(
            expand=True,
            content=ft.Column(
                [
                    right_header,
                    ft.Divider(height=1, color=theme.BORDER),
                    ft.Container(
                        expand=True,
                        content=ft.Column(
                            [self._repos_col],
                            expand=True,
                            scroll=ft.ScrollMode.AUTO,
                        ),
                    ),
                ],
                expand=True,
                spacing=10,
            ),
        )
        split = ft.Row(
            [
                left_pane,
                ft.VerticalDivider(width=1, color=theme.BORDER),
                right_pane,
            ],
            expand=True,
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        console_header = ft.Container(
            content=ft.Row(
                [
                    self._console_chevron,
                    ft.Text("Console", size=13, weight=ft.FontWeight.W_600, color=theme.TEXT),
                    self._console_status,
                    self._console_clear,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ink=True,
            on_click=lambda _e: self._toggle_console(),
            padding=theme.padding_xy(8, 6),
            border_radius=theme.radius_all(theme.RADIUS),
        )
        console = ft.Column(
            [console_header, self._console_scroll],
            spacing=4,
            tight=True,
        )
        root = ft.Container(
            expand=True,
            bgcolor=theme.SURFACE,
            border=theme.border_all(),
            border_radius=theme.radius_all(theme.RADIUS_LARGE),
            padding=theme.padding_all(16),
            content=ft.Column(
                [
                    ft.Text("Git repositories", size=18, weight=ft.FontWeight.W_600, color=theme.TEXT),
                    actions,
                    ft.Container(expand=True, content=split),
                    ft.Divider(height=1, color=theme.BORDER),
                    console,
                ],
                expand=True,
                spacing=10,
            ),
        )
        self._ensure_group_selection()
        self._refresh(update=False)
        logger.info(
            "GitView.build done groups=%d repos=%d",
            len(self._groups_col.controls),
            len(self._repos_col.controls),
        )
        return root

    # -- config / persistence -----------------------------------------------------

    def _sync_git_executable(self) -> None:
        if self._config is None:
            return
        try:
            executable = str(self._config.get("git.executable") or "git")
        except Exception:  # noqa: BLE001
            executable = "git"
        if hasattr(self._git, "set_executable"):
            self._git.set_executable(executable)

    def _persist_view_state(self) -> None:
        if self._config is None:
            return
        try:
            self._config.apply(
                {
                    "git.home.search": self._search,
                    "git.home.group": "" if self._group is None else self._group,
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("persist view state failed")

    def _restore_view_state(self) -> None:
        if self._config is None:
            return
        try:
            self._search = str(self._config.get("git.home.search") or "")
            stored = self._config.get("git.home.group")
            self._group = None if stored is None or stored == "" else str(stored)
            self._search_field.value = self._search
        except Exception:  # noqa: BLE001
            logger.exception("restore view state failed")

    def _note_recent(self, path: str) -> None:
        if self._history is not None:
            try:
                self._history.add_recent_folder(path)
            except Exception:  # noqa: BLE001
                logger.exception("add_recent_folder failed")

    # -- data ---------------------------------------------------------------------

    def _section_counts(self) -> dict[str, int]:
        if self._favorites is None:
            return {"__demo__": len(_DEMO_REPOS)}
        counts: dict[str, int] = {}
        for favorite in self._favorites.by_kind("folder", limit=None):
            key = (favorite.group_name or "").strip()
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _favorites_for_group(self) -> list:
        if self._favorites is None:
            return []
        return [
            favorite
            for favorite in self._favorites.by_kind("folder")
            if (favorite.group_name or "").strip() == (self._group or "")
        ]

    def _known_groups(self) -> tuple[str, ...]:
        if self._favorites is None:
            return ()
        groups = {
            (favorite.group_name or "").strip()
            for favorite in self._favorites.by_kind("folder", limit=None)
        }
        groups.discard("")
        return tuple(sorted(groups, key=str.casefold))

    def _ensure_group_selection(self) -> None:
        counts = self._section_counts()
        if not counts:
            self._group = None
            return
        if self._group is not None and self._group in counts:
            return
        # Prefer named groups over Ungrouped when restoring a missing key.
        ordered = sorted(counts, key=lambda g: (g == "", g.casefold()))
        self._group = ordered[0]
        self._persist_view_state()

    # -- rendering -----------------------------------------------------------------

    def _refresh(self, update: bool = True) -> None:
        try:
            self._ensure_group_selection()
            self._groups_col.controls = self._group_rows()
            if self._open_path:
                self._repos_col.controls = self._open_folder_items()
                self._set_bulk_enabled(False)
            else:
                self._repos_col.controls = self._cards_items()
                can_bulk = (
                    not self._bulk_busy
                    and self._group is not None
                    and self._group != "__demo__"
                    and bool(self._favorites_for_group())
                )
                self._set_bulk_enabled(can_bulk)
            logger.info(
                "refresh ok groups=%d repos=%d group=%r",
                len(self._groups_col.controls),
                len(self._repos_col.controls),
                self._group,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("refresh failed")
            self._repos_col.controls = [
                ft.Text(f"Could not load Git view: {exc}", color=theme.ERR, selectable=True)
            ]
        if update:
            self.page.update()
        if (
            self._group is not None
            and self._group != "__demo__"
            and not self._open_path
            and not self._bulk_busy
        ):
            self._refresh_all_statuses()

    def _group_rows(self) -> list[ft.Control]:
        counts = self._section_counts()
        if not counts:
            return [ft.Text("No groups yet.", size=12, color=theme.TEXT_MUTED)]

        rows: list[ft.Control] = []
        for key in sorted(counts, key=lambda g: (g == "", g == "__demo__", g.casefold())):
            count = counts[key]
            if key == "__demo__":
                name = "Demo folders"
            else:
                name = key or "Ungrouped"
            noun = "repo" if count == 1 else "repos"
            selected = self._group == key and not self._open_path
            rows.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.FOLDER,
                                color=theme.ACCENT if selected else theme.TEXT_MUTED,
                                size=18,
                            ),
                            ft.Column(
                                [
                                    ft.Text(
                                        name,
                                        size=13,
                                        weight=ft.FontWeight.W_600 if selected else ft.FontWeight.W_400,
                                        color=theme.TEXT,
                                    ),
                                    ft.Text(f"{count} {noun}", size=11, color=theme.TEXT_MUTED),
                                ],
                                spacing=1,
                                expand=True,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=theme.ACCENT_SOFT if selected else theme.SURFACE_HOVER,
                    border=theme.border_all(theme.ACCENT if selected else theme.BORDER),
                    border_radius=theme.radius_all(theme.RADIUS),
                    padding=theme.padding_xy(10, 8),
                    ink=True,
                    on_click=lambda _e, k=key: self._open_group(k),
                )
            )
        return rows

    def _cards_items(self) -> list[ft.Control]:
        self._back_button.visible = False
        self._card_buttons = {}
        self._card_rings = {}
        self._card_status = {}
        self._card_badges = {}

        if self._group is None:
            self._group_title.value = "Select a group"
            return [ft.Text("Choose a group on the left.", color=theme.TEXT_MUTED)]

        if self._group == "__demo__":
            self._group_title.value = "Demo folders"
            return [self._card(name, name, demo=True) for name in _DEMO_REPOS]

        self._group_title.value = self._group or "Ungrouped"
        members = self._favorites_for_group()
        query = self._search.strip().lower()
        if query:
            members = [
                favorite
                for favorite in members
                if query in " ".join((favorite.label, favorite.ref, favorite.group_name or "")).lower()
            ]
        members.sort(key=lambda favorite: (favorite.label or favorite.ref).casefold())
        if not members:
            msg = "No repositories match your search." if query else "This group is empty."
            return [ft.Text(msg, color=theme.TEXT_MUTED)]
        return [
            self._card(favorite.ref, favorite.label or os.path.basename(favorite.ref.rstrip("/")), demo=False)
            for favorite in members
        ]

    def _open_folder_items(self) -> list[ft.Control]:
        path = self._open_path or ""
        label = os.path.basename(path.rstrip("/")) or path
        self._back_button.visible = True
        self._group_title.value = f"Opened — {label}"
        self._card_buttons = {}
        self._card_rings = {}
        self._card_status = {}
        self._card_badges = {}
        return [self._card(path, label, demo=False, transient=True)]

    def _card(self, path: str, label: str, demo: bool, transient: bool = False) -> ft.Container:
        title = ft.Text(label, size=15, weight=ft.FontWeight.W_600, color=theme.TEXT)
        path_text = ft.Text(path, size=12, color=theme.TEXT_MUTED, selectable=True)
        badge = ft.Text(self._badge_text(path), size=12, color=theme.ACCENT)
        ring = ft.ProgressRing(width=16, height=16, stroke_width=2, color=theme.ACCENT, visible=False)
        status = ft.Text("", size=12, color=theme.TEXT_MUTED)

        if demo:
            actions: list[ft.Control] = [
                ft.Text("Demo data — add real favorites to run git operations", size=12, color=theme.TEXT_FAINT)
            ]
        else:
            actions = [
                self._op_button(ft.Icons.SEARCH, "Status", "status", path),
                self._op_button(ft.Icons.CLOUD_DOWNLOAD, "Fetch", "fetch", path),
                self._op_button(ft.Icons.DOWNLOAD, "Pull", "pull", path),
                self._op_button(ft.Icons.HISTORY, "Log", "log", path),
                self._op_button(ft.Icons.ACCOUNT_TREE, "Branches", "branches", path),
                self._op_button(ft.Icons.SYNC, "Fetch all", "fetch_all", path),
                self._op_button(ft.Icons.REFRESH, "Reset", "reset", path),
            ]
            if not transient:
                actions.append(
                    ft.IconButton(
                        icon=ft.Icons.EDIT,
                        icon_color=theme.TEXT_MUTED,
                        tooltip="Edit favorite",
                        on_click=lambda _e, p=path: self._show_edit_dialog(p),
                    )
                )
                actions.append(
                    ft.IconButton(
                        icon=ft.Icons.DELETE,
                        icon_color=theme.ERR,
                        tooltip="Remove from favorites",
                        on_click=lambda _e, p=path: self._remove(p),
                    )
                )

        self._card_buttons[path] = [c for c in actions if isinstance(c, ft.FilledButton)]
        self._card_rings[path] = ring
        self._card_status[path] = status
        self._card_badges[path] = badge

        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.FOLDER, color=theme.ACCENT, size=22),
                            ft.Column([title, path_text, badge], spacing=1, expand=True),
                        ],
                        spacing=10,
                    ),
                    ft.Row(actions, spacing=6, wrap=True),
                    ft.Row([ring, status], spacing=8),
                ],
                spacing=10,
            ),
            bgcolor=theme.SURFACE_HOVER,
            border=theme.border_all(),
            border_radius=theme.radius_all(theme.RADIUS),
            padding=theme.padding_all(14),
        )

    def _badge_text(self, path: str) -> str:
        info = self._status_cache.get(path)
        if not info or not info.get("is_repo"):
            return ""
        branch = info.get("branch") or "?"
        ahead = int(info.get("ahead") or 0)
        behind = int(info.get("behind") or 0)
        parts = [branch]
        if ahead:
            parts.append(f"↑{ahead}")
        if behind:
            parts.append(f"↓{behind}")
        upstream = info.get("upstream")
        if upstream:
            parts.append(f"→ {upstream}")
        return " · ".join(parts)

    def _op_button(self, icon: str, text: str, op: str, path: str) -> ft.FilledButton:
        return ft.FilledButton(content=text, icon=icon, on_click=lambda _e: self._run_op(op, path))

    def _set_bulk_enabled(self, enabled: bool) -> None:
        self._bulk_fetch.disabled = not enabled
        self._bulk_status.disabled = not enabled
        self._bulk_reset.disabled = not enabled
        self._bulk_ring.visible = self._bulk_busy

    # -- navigation ------------------------------------------------------------------

    def _open_group(self, key: str) -> None:
        self._open_path = None
        self._group = key
        self._persist_view_state()
        self._refresh()

    def _back_to_groups(self) -> None:
        self._open_path = None
        self._refresh()

    def _on_search(self, event) -> None:
        self._search = (event.control.value or "").strip()
        self._persist_view_state()
        self._refresh()

    # -- console --------------------------------------------------------------------

    def _toggle_console(self) -> None:
        self._console_open = not self._console_open
        self._console_chevron.value = "▾" if self._console_open else "▸"
        self._console_scroll.visible = self._console_open
        self._console_clear.visible = self._console_open
        self.page.update()

    def _clear_console(self) -> None:
        self._console_lines.clear()
        self._console_body.controls.clear()
        if not self._bulk_busy:
            self._console_status.value = "idle"
        self.page.update()

    def _set_console_status(self, text: str) -> None:
        self._console_status.value = text

    def _console_log(self, line: str, *, ok: bool | None = None) -> None:
        color = theme.TEXT_MUTED
        if ok is True:
            color = theme.OK
        elif ok is False:
            color = theme.ERR
        self._console_lines.append(line)
        self._console_body.controls.append(
            ft.Text(line, size=11, color=color, selectable=True, font_family="Menlo")
        )
        # Cap retained lines so the panel stays light.
        while len(self._console_body.controls) > 400:
            self._console_body.controls.pop(0)
            if self._console_lines:
                self._console_lines.pop(0)

    # -- async operations ---------------------------------------------------------------

    def _run_op(self, op: str, path: str) -> None:
        self._sync_git_executable()
        buttons = self._card_buttons.get(path) or []
        ring = self._card_rings.get(path)
        status = self._card_status.get(path)

        for button in buttons:
            button.disabled = True
        if ring is not None:
            ring.visible = True
        if status is not None:
            status.value = f"Running git {op}…"
            status.color = theme.TEXT_MUTED
        self._console_log(f"$ git {op} — {path}")
        self._set_console_status(f"git {op}…")
        self.page.update()

        async def run():
            try:
                if op == "status":
                    return await self._git.status(path)
                if op == "fetch":
                    return await self._git.fetch(path)
                if op == "pull":
                    return await self._git.pull(path)
                if op == "log":
                    return await self._git.log(path)
                if op == "branches":
                    return await self._git.branches(path)
                if op == "fetch_all":
                    return await self._git.fetch_all(path)
                if op == "reset":
                    return await self._git.reset(path)
                return {"ok": False, "output": f"unknown operation {op!r}"}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "output": str(exc)}

        future = self.page.run_task(run)
        future.add_done_callback(lambda f: self._finish_op(op, path, f.result()))

    def _finish_op(self, op: str, path: str, result: dict) -> None:
        buttons = self._card_buttons.get(path) or []
        ring = self._card_rings.get(path)
        status = self._card_status.get(path)
        ok = bool(result.get("ok"))

        for button in buttons:
            button.disabled = False
        if ring is not None:
            ring.visible = False

        detail = self._format_result(op, result)
        if status is not None:
            first = detail.splitlines()[0] if detail else ("ok" if ok else "failed")
            status.value = f"{'✓' if ok else '✕'} git {op}: {first[:120]}"
            status.color = theme.OK if ok else theme.ERR

        summary = detail.splitlines()[0] if detail else ("ok" if ok else "failed")
        self._console_log(f"  {'✓' if ok else '✕'} {summary[:200]}", ok=ok)
        if not self._bulk_busy:
            self._set_console_status("idle")

        self._snack(f"git {op} {'succeeded' if ok else 'failed'} — {path}", ok=ok)
        if op in ("fetch", "fetch_all", "pull", "status"):
            self._refresh_status(path)
        self.page.update()

        if op == "log" and ok and result.get("rows"):
            self._show_log_dialog(path, result["rows"])

    def _run_bulk(self, op: str) -> None:
        if self._bulk_busy or self._open_path or self._group in (None, "__demo__"):
            return
        members = self._favorites_for_group()
        if not members:
            return
        paths = [favorite.ref for favorite in members]
        self._bulk_busy = True
        self._set_bulk_enabled(False)
        self._bulk_ring.visible = True
        label = {"fetch": "fetch", "status": "status", "reset": "reset"}.get(op, op)
        self._console_log(f"$ bulk {label} — {len(paths)} repos in {self._group or 'Ungrouped'}")
        self._set_console_status(f"{label} 0/{len(paths)}…")
        self.page.update()

        async def run():
            results: list[tuple[str, dict]] = []
            for index, path in enumerate(paths, start=1):
                self._set_console_status(f"{label} {index}/{len(paths)}…")
                self._console_log(f"$ git {op} — {path}")
                self.page.update()
                try:
                    if op == "fetch":
                        result = await self._git.fetch(path)
                    elif op == "status":
                        result = await self._git.status(path)
                    elif op == "reset":
                        result = await self._git.reset(path)
                    else:
                        result = {"ok": False, "output": f"unknown bulk op {op!r}"}
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "output": str(exc)}
                results.append((path, result))
                ok = bool(result.get("ok"))
                detail = self._format_result(op, result)
                summary = detail.splitlines()[0] if detail else ("ok" if ok else "failed")
                self._console_log(f"  {'✓' if ok else '✕'} {summary[:200]}", ok=ok)
                ring = self._card_rings.get(path)
                status = self._card_status.get(path)
                if ring is not None:
                    ring.visible = False
                if status is not None:
                    status.value = f"{'✓' if ok else '✕'} git {op}: {summary[:120]}"
                    status.color = theme.OK if ok else theme.ERR
                self.page.update()
            return results

        future = self.page.run_task(run)
        future.add_done_callback(lambda f: self._finish_bulk(op, f.result()))

    def _finish_bulk(self, op: str, results: list[tuple[str, dict]]) -> None:
        self._bulk_busy = False
        self._bulk_ring.visible = False
        ok_count = sum(1 for _, result in results if result.get("ok"))
        total = len(results)
        self._console_log(
            f"  bulk {op} done — {ok_count}/{total} ok",
            ok=ok_count == total,
        )
        self._set_console_status("idle")
        self._snack(
            f"Bulk {op}: {ok_count}/{total} succeeded",
            ok=ok_count == total,
        )
        if op in ("fetch", "status"):
            for path, _ in results:
                self._refresh_status(path)
        self._set_bulk_enabled(
            self._group is not None
            and self._group != "__demo__"
            and bool(self._favorites_for_group())
        )
        self.page.update()

    def _format_result(self, op: str, result: dict) -> str:
        if op == "log" and result.get("rows"):
            return f"{len(result['rows'])} commits"
        return (result.get("output") or "").strip()

    def _refresh_status(self, path: str) -> None:
        async def run():
            try:
                return await self._git.remote_status(path)
            except Exception:  # noqa: BLE001
                return {}

        future = self.page.run_task(run)

        def done(fut) -> None:
            info = fut.result() or {}
            if info:
                self._status_cache[path] = info
            badge = self._card_badges.get(path)
            if badge is not None:
                badge.value = self._badge_text(path)
                self.page.update()

        future.add_done_callback(done)

    def _refresh_all_statuses(self) -> None:
        if self._favorites is None or self._group is None:
            return
        for favorite in self._favorites_for_group():
            self._refresh_status(favorite.ref)

    # -- open / scan / groups / edit -----------------------------------------------

    def _open_folder(self) -> None:
        async def browse(_event=None) -> None:
            chosen = await ft.FilePicker().get_directory_path(
                dialog_title="Open repository folder",
                initial_directory=os.path.expanduser("~"),
            )
            if not chosen:
                return
            path = os.path.abspath(chosen)
            self._sync_git_executable()

            async def check():
                try:
                    return await self._git.check_repo(path)
                except Exception as exc:  # noqa: BLE001
                    return exc

            result = await check()
            if isinstance(result, Exception):
                self._snack(str(result), ok=False)
                return
            if not result:
                self._snack(f"Not a git repository: {path}", ok=False)
                return
            self._open_path = path
            self._note_recent(path)
            self._snack(f"Opened {path}", ok=True)
            self._refresh()

        self.page.run_task(browse)

    def _show_scan_dialog(self) -> None:
        root_field = ft.TextField(
            label="Scan root *",
            hint_text="/Users/me/Projects",
            border_radius=theme.RADIUS,
            focused_border_color=theme.ACCENT,
            expand=True,
        )
        group_options = [ft.dropdown.Option(key="", text="— Ungrouped —")]
        group_options += [ft.dropdown.Option(key=g, text=g) for g in self._known_groups()]
        group_field = ft.Dropdown(
            label="Add to group",
            options=group_options,
            value="",
            editable=True,
            border_radius=theme.RADIUS,
        )
        results_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=220)
        selected: dict[str, bool] = {}
        scan_ring = ft.ProgressRing(width=18, height=18, stroke_width=2, visible=False)
        add_button = ft.FilledButton(content="Add selected", icon=ft.Icons.CHECK, disabled=True)

        async def browse(_e) -> None:
            chosen = await ft.FilePicker().get_directory_path(
                dialog_title="Choose folder to scan",
                initial_directory=os.path.expanduser("~"),
            )
            if chosen:
                root_field.value = chosen
                self.page.update()

        async def scan(_e) -> None:
            root = os.path.abspath(os.path.expanduser((root_field.value or "").strip()))
            if not os.path.isdir(root):
                self._snack(f"Folder not found: {root}", ok=False)
                return
            self._sync_git_executable()
            scan_ring.visible = True
            results_col.controls.clear()
            add_button.disabled = True
            self.page.update()
            try:
                found = await self._git.find_repos(root)
            except Exception as exc:  # noqa: BLE001
                scan_ring.visible = False
                self._snack(str(exc), ok=False)
                self.page.update()
                return
            scan_ring.visible = False
            selected.clear()
            if not found:
                results_col.controls.append(ft.Text("No repositories found.", color=theme.TEXT_MUTED))
                self.page.update()
                return
            for path in found:
                already = self._favorites is not None and self._favorites.is_favorite("folder", path)
                selected[path] = not already
                results_col.controls.append(
                    ft.Checkbox(
                        label=path + (" (already added)" if already else ""),
                        value=not already,
                        disabled=already,
                        on_change=lambda e, p=path: selected.__setitem__(p, bool(e.control.value)),
                    )
                )
            add_button.disabled = False
            self.page.update()

        def add_selected(_e) -> None:
            if self._favorites is None:
                self._snack("Database unavailable — cannot add favorites", ok=False)
                return
            group = (group_field.value or "").strip()
            count = 0
            for path, yes in selected.items():
                if not yes or self._favorites.is_favorite("folder", path):
                    continue
                self._favorites.insert(
                    Favorite(
                        kind="folder",
                        ref=path,
                        label=os.path.basename(path.rstrip("/")) or path,
                        group_name=group,
                    )
                )
                self._note_recent(path)
                count += 1
            self._close_dialog(dialog)
            self._snack(f"Added {count} repositor{'y' if count == 1 else 'ies'}", ok=True)
            self._refresh()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Scan for repositories", weight=ft.FontWeight.W_600),
            content=ft.Column(
                [
                    ft.Row([root_field, ft.IconButton(icon=ft.Icons.FOLDER_OPEN, on_click=browse)]),
                    group_field,
                    ft.Row([ft.FilledButton(content="Scan", icon=ft.Icons.TRAVEL_EXPLORE, on_click=scan), scan_ring]),
                    results_col,
                ],
                spacing=theme.GAP,
                width=520,
                tight=True,
            ),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _e: self._close_dialog(dialog)),
                add_button,
            ],
        )
        add_button.on_click = add_selected
        self.page.show_dialog(dialog)

    def _show_manage_groups(self) -> None:
        if self._favorites is None:
            self._snack("Database unavailable", ok=False)
            return
        groups = list(self._known_groups())
        list_view = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, height=280)
        rename_field = ft.TextField(label="New name", border_radius=theme.RADIUS)
        selected: dict[str, str | None] = {"group": None}

        def rebuild() -> None:
            list_view.controls.clear()
            for name in groups:
                list_view.controls.append(
                    ft.ListTile(
                        title=ft.Text(name),
                        selected=selected["group"] == name,
                        on_click=lambda _e, n=name: select(n),
                    )
                )
            self.page.update()

        def select(name: str) -> None:
            selected["group"] = name
            rename_field.value = name
            rebuild()

        def favorites_in(group: str) -> list:
            return [
                favorite
                for favorite in self._favorites.by_kind("folder", limit=None)
                if (favorite.group_name or "").strip() == group
            ]

        def rename(_e) -> None:
            old = selected["group"]
            new = (rename_field.value or "").strip()
            if not old or not new or new == old:
                return
            for favorite in favorites_in(old):
                favorite.group_name = new
                self._favorites.update(favorite)
            groups[:] = list(self._known_groups())
            selected["group"] = new
            if self._group == old:
                self._group = new
                self._persist_view_state()
            self._snack(f"Renamed group to {new}", ok=True)
            rebuild()
            self._refresh()

        def merge(_e) -> None:
            old = selected["group"]
            target = (rename_field.value or "").strip()
            if not old or not target or target == old:
                self._snack("Pick a group and enter a different target name", ok=False)
                return
            for favorite in favorites_in(old):
                favorite.group_name = target
                self._favorites.update(favorite)
            groups[:] = list(self._known_groups())
            selected["group"] = target if target in groups else None
            if self._group == old:
                self._group = target
                self._persist_view_state()
            self._snack(f"Merged {old} → {target}", ok=True)
            rebuild()
            self._refresh()

        def ungroup(_e) -> None:
            old = selected["group"]
            if not old:
                return
            for favorite in favorites_in(old):
                favorite.group_name = ""
                self._favorites.update(favorite)
            groups[:] = list(self._known_groups())
            selected["group"] = None
            rename_field.value = ""
            if self._group == old:
                self._group = ""
                self._persist_view_state()
            self._snack(f"Ungrouped {old}", ok=True)
            rebuild()
            self._refresh()

        rebuild()
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Manage groups", weight=ft.FontWeight.W_600),
            content=ft.Column(
                [
                    list_view,
                    rename_field,
                    ft.Row(
                        [
                            ft.OutlinedButton(content="Rename", on_click=rename),
                            ft.OutlinedButton(content="Merge into name", on_click=merge),
                            ft.OutlinedButton(content="Ungroup", on_click=ungroup),
                        ],
                        wrap=True,
                    ),
                ],
                width=420,
                tight=True,
            ),
            actions=[ft.TextButton(content="Close", on_click=lambda _e: self._close_dialog(dialog))],
        )
        self.page.show_dialog(dialog)

    def _show_edit_dialog(self, path: str) -> None:
        if self._favorites is None:
            return
        favorite = self._favorites.find("folder", path)
        if favorite is None:
            self._snack("Favorite not found", ok=False)
            return
        name_field = ft.TextField(label="Name *", value=favorite.label or "", border_radius=theme.RADIUS)
        path_field = ft.TextField(label="Path *", value=favorite.ref, border_radius=theme.RADIUS)
        group_options = [ft.dropdown.Option(key="", text="— Ungrouped —")]
        group_options += [ft.dropdown.Option(key=g, text=g) for g in self._known_groups()]
        group_field = ft.Dropdown(
            label="Group",
            options=group_options,
            value=favorite.group_name or "",
            editable=True,
            border_radius=theme.RADIUS,
        )

        def save(_e) -> None:
            new_ref = os.path.abspath(os.path.expanduser((path_field.value or "").strip()))
            label = (name_field.value or "").strip()
            if not label:
                self._snack("Name is required", ok=False)
                return
            if not os.path.isdir(new_ref):
                self._snack(f"Folder not found: {new_ref}", ok=False)
                return
            if new_ref != favorite.ref and self._favorites.is_favorite("folder", new_ref):
                self._snack("Another favorite already uses that path", ok=False)
                return
            favorite.label = label
            favorite.ref = new_ref
            favorite.group_name = (group_field.value or "").strip()
            self._favorites.update(favorite)
            self._close_dialog(dialog)
            self._snack(f"Updated {label}", ok=True)
            self._refresh()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit repository", weight=ft.FontWeight.W_600),
            content=ft.Column([name_field, path_field, group_field], spacing=theme.GAP, width=420),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _e: self._close_dialog(dialog)),
                ft.FilledButton(content="Save", icon=ft.Icons.CHECK, on_click=save),
            ],
        )
        self.page.show_dialog(dialog)

    def _show_log_dialog(self, path: str, rows: list) -> None:
        lines = []
        for row in rows[:40]:
            if len(row) >= 4:
                lines.append(f"{row[0]}  {row[2]}  {row[1]}  {row[3]}")
            else:
                lines.append("  ".join(row))
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Recent commits — {os.path.basename(path)}", weight=ft.FontWeight.W_600),
            content=ft.Container(
                content=ft.Text("\n".join(lines) or "(no commits)", selectable=True, size=12, font_family="Menlo"),
                width=560,
                height=320,
            ),
            actions=[ft.TextButton(content="Close", on_click=lambda _e: self._close_dialog(dialog))],
        )
        self.page.show_dialog(dialog)

    # -- add / remove ---------------------------------------------------------------------

    def _show_add_dialog(self) -> None:
        name_field = ft.TextField(label="Name *", hint_text="e.g. dev_work_bench", border_radius=theme.RADIUS)
        path_field = ft.TextField(label="Path *", hint_text="/Users/me/Projects/my-repo", border_radius=theme.RADIUS)

        async def _browse(_event) -> None:
            chosen = await ft.FilePicker().get_directory_path(
                dialog_title="Choose repository folder",
                initial_directory=os.path.expanduser("~"),
            )
            if chosen:
                path_field.value = chosen
                self.page.update()

        path_field.suffix = ft.IconButton(icon=ft.Icons.FOLDER_OPEN, tooltip="Browse…", on_click=_browse)
        group_options = [ft.dropdown.Option(key="", text="— Ungrouped —")]
        group_options += [ft.dropdown.Option(key=group, text=group) for group in self._known_groups()]
        group_field = ft.Dropdown(
            label="Group",
            options=group_options,
            value="",
            editable=True,
            border_radius=theme.RADIUS,
        )

        def save(_e) -> None:
            name = (name_field.value or "").strip()
            ref = os.path.abspath(os.path.expanduser((path_field.value or "").strip()))
            if not name:
                self._snack("Name is required", ok=False)
                return
            if not os.path.isdir(ref):
                self._snack(f"Folder not found: {ref}", ok=False)
                return
            if self._favorites is not None and not self._favorites.is_favorite("folder", ref):
                self._favorites.insert(
                    Favorite(
                        kind="folder",
                        ref=ref,
                        label=name,
                        group_name=(group_field.value or "").strip(),
                    )
                )
                self._note_recent(ref)
            self._close_dialog(dialog)
            self._snack(f"Added {name} — {ref}", ok=True)
            self._refresh()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Add repository", weight=ft.FontWeight.W_600, color=theme.TEXT),
            content=ft.Column([name_field, path_field, group_field], spacing=theme.GAP, width=420),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _e: self._close_dialog(dialog)),
                ft.FilledButton(content="Add", icon=ft.Icons.CHECK_CIRCLE, on_click=save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dialog)

    def _remove(self, path: str) -> None:
        if self._favorites is not None:
            self._favorites.remove_ref("folder", path)
        self._snack(f"Removed {path} from favorites", ok=True)
        self._refresh()

    # -- feedback ---------------------------------------------------------------------

    def _snack(self, message: str, ok: bool = True) -> None:
        try:
            self.page.show_dialog(
                ft.SnackBar(
                    content=ft.Text(message, color=theme.TEXT),
                    bgcolor=theme.OK if ok else theme.ERR,
                    open=True,
                    duration=3500,
                )
            )
        except Exception:  # noqa: BLE001 — snack must never break the screen
            logger.exception("snack failed: %s", message)
            self.page.update()

    def _close_dialog(self, dialog) -> None:
        dialog.open = False
        self.page.update()


def build_git_screen(shell) -> ft.Control:
    """Build the Git screen for the Flet shell (see screens/build_screen)."""
    try:
        return _GitView(shell).build()
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_git_screen failed")
        return ft.Container(
            expand=True,
            bgcolor=theme.SURFACE,
            padding=theme.padding_all(20),
            content=ft.Text(f"Git screen failed to build:\n{exc}", color=theme.ERR, selectable=True),
        )
