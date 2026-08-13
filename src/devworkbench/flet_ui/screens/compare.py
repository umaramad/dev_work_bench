"""Compare screen (Flet) — folder tree + retained file-diff tabs.

Uses a lightweight chip tab strip + content pane (not ``ft.Tabs`` /
``TabBarView``, which were blanking the panel on Flet 0.86 after folder
picks / updates). Folder results render as an indented tree; file diffs
open in extra chips so prior results stay available.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace

import flet as ft

from devworkbench.flet_ui import theme
from devworkbench.services.compare.models import FolderDiffEntry, FolderDiffResult
from devworkbench.services.compare_service import CompareService

logger = logging.getLogger("devworkbench.flet_ui.compare")

_STATE_COLORS = {
    "": theme.TEXT,
    "added": "#86efac",
    "removed": "#fca5a5",
    "changed": "#fde68a",
    "header": theme.TEXT_MUTED,
    "identical": theme.TEXT_MUTED,
    "modified": "#fde68a",
    "only_left": "#fca5a5",
    "only_right": "#86efac",
    "moved": theme.ACCENT,
    "renamed": theme.ACCENT,
}


class _CompareView:
    def __init__(self, shell) -> None:
        self.page = shell.page
        self._backend = getattr(shell, "backend", None) or {}
        self._config = self._backend.get("config")
        self._compare: CompareService = self._backend.get("compare") or CompareService()

        self._mode = "files"
        self._busy = False
        self._folder_result: FolderDiffResult | None = None
        self._folder_filter = ""
        self._entry_checks: dict[str, ft.Checkbox] = {}
        self._entry_by_rel: dict[str, FolderDiffEntry] = {}

        # Lightweight tab model (chip strip + single content pane).
        self._tab_labels: list[str] = ["Results"]
        self._tab_keys: list[str] = ["__results__"]
        self._tab_contents: list[ft.Control] = [self._empty_hint()]
        self._tab_index = 0

        default_engine = "auto"
        if self._config is not None:
            try:
                default_engine = str(self._config.get("compare.engine") or "auto")
            except Exception:  # noqa: BLE001
                pass

        self._left = ft.TextField(
            label="Left",
            border_radius=theme.RADIUS,
            focused_border_color=theme.ACCENT,
            expand=True,
        )
        self._right = ft.TextField(
            label="Right",
            border_radius=theme.RADIUS,
            focused_border_color=theme.ACCENT,
            expand=True,
        )
        self._mode_dd = ft.Dropdown(
            label="Mode",
            options=[
                ft.dropdown.Option(key="files", text="Files"),
                ft.dropdown.Option(key="folders", text="Folders"),
            ],
            value="files",
            width=140,
            border_radius=theme.RADIUS,
            on_select=self._on_mode,
        )
        self._engine_dd = ft.Dropdown(
            label="Engine",
            options=[
                ft.dropdown.Option(key="auto", text="Auto"),
                ft.dropdown.Option(key="myers", text="Myers"),
                ft.dropdown.Option(key="difflib", text="Difflib"),
            ],
            value=default_engine if default_engine in ("auto", "myers", "difflib") else "auto",
            width=130,
            border_radius=theme.RADIUS,
        )
        self._layout_dd = ft.Dropdown(
            label="Layout",
            options=[
                ft.dropdown.Option(key="side", text="Side by side"),
                ft.dropdown.Option(key="inline", text="Inline"),
            ],
            value="side",
            width=150,
            border_radius=theme.RADIUS,
        )
        self._compare_btn = ft.FilledButton(
            content="Compare", icon=ft.Icons.COMPARE, on_click=lambda _e: self._run_compare()
        )
        self._ring = ft.ProgressRing(width=18, height=18, stroke_width=2, color=theme.ACCENT, visible=False)
        self._status = ft.Text("", size=12, color=theme.TEXT_MUTED)
        self._filter_field = ft.TextField(
            label="Filter folder tree",
            border_radius=theme.RADIUS,
            prefix_icon=ft.Icons.SEARCH,
            on_change=self._on_filter,
            visible=False,
            width=220,
        )
        self._close_tab_btn = ft.OutlinedButton(
            content="Close tab",
            icon=ft.Icons.CLOSE,
            on_click=lambda _e: self._close_current_tab(),
            disabled=True,
        )

        self._tab_row = ft.Row(spacing=6, wrap=True)
        self._pane = ft.Container(
            expand=True,
            bgcolor=theme.BG,
            border=theme.border_all(),
            border_radius=theme.radius_all(8),
            padding=theme.padding_all(10),
            content=self._tab_contents[0],
        )
        self._rebuild_tab_chips()

    def build(self) -> ft.Container:
        pickers = ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN,
                    tooltip="Browse left",
                    on_click=lambda _e: self._browse("left"),
                ),
                self._left,
                ft.IconButton(icon=ft.Icons.SWAP_HORIZ, tooltip="Swap", on_click=lambda _e: self._swap()),
                ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN,
                    tooltip="Browse right",
                    on_click=lambda _e: self._browse("right"),
                ),
                self._right,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        actions = ft.Row(
            [
                self._mode_dd,
                self._engine_dd,
                self._layout_dd,
                self._compare_btn,
                self._ring,
                self._status,
            ],
            wrap=True,
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        sync_bar = ft.Row(
            [
                self._filter_field,
                ft.OutlinedButton(
                    content="Copy L→R",
                    icon=ft.Icons.ARROW_FORWARD,
                    on_click=lambda _e: self._run_sync("copy_left_to_right"),
                ),
                ft.OutlinedButton(
                    content="Copy R→L",
                    icon=ft.Icons.ARROW_BACK,
                    on_click=lambda _e: self._run_sync("copy_right_to_left"),
                ),
                ft.OutlinedButton(
                    content="Delete",
                    icon=ft.Icons.DELETE,
                    on_click=lambda _e: self._run_sync("delete"),
                ),
                self._close_tab_btn,
            ],
            wrap=True,
            spacing=8,
        )
        return ft.Container(
            expand=True,
            bgcolor=theme.SURFACE,
            border=theme.border_all(),
            border_radius=theme.radius_all(theme.RADIUS_LARGE),
            padding=theme.padding_all(16),
            content=ft.Column(
                [
                    ft.Text("Compare", size=18, weight=ft.FontWeight.W_600, color=theme.TEXT),
                    pickers,
                    actions,
                    sync_bar,
                    ft.Divider(height=1, color=theme.BORDER),
                    self._tab_row,
                    self._pane,
                ],
                spacing=theme.GAP,
                expand=True,
            ),
        )

    def _empty_hint(self) -> ft.Control:
        return ft.Container(
            content=ft.Text(
                "Choose two paths and click Compare.\n"
                "Tip: set Mode to Folders before browsing directories.",
                color=theme.TEXT_MUTED,
                size=13,
                text_align=ft.TextAlign.CENTER,
            ),
            alignment=ft.Alignment.CENTER,
            expand=True,
            padding=theme.padding_all(20),
        )

    def _on_mode(self, _e) -> None:
        self._mode = self._mode_dd.value or "files"
        self._filter_field.visible = self._mode == "folders"
        logger.info("compare mode → %s", self._mode)
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            logger.exception("mode update failed")

    def _on_filter(self, event) -> None:
        self._folder_filter = (event.control.value or "").strip().lower()
        if self._folder_result is not None:
            try:
                self._set_results_content(self._render_folder_tree(self._folder_result))
                self.page.update()
            except Exception:  # noqa: BLE001
                logger.exception("filter refresh failed")

    # -- browse ------------------------------------------------------------------

    def _browse(self, side: str) -> None:
        async def run(_e=None) -> None:
            mode = self._mode_dd.value or "files"
            logger.info("browse %s mode=%s", side, mode)
            try:
                if mode == "folders":
                    chosen = await ft.FilePicker().get_directory_path(
                        dialog_title=f"Choose {side} folder"
                    )
                else:
                    files = await ft.FilePicker().pick_files(
                        allow_multiple=False, dialog_title=f"Choose {side} file"
                    )
                    chosen = files[0].path if files else None
            except Exception as exc:  # noqa: BLE001
                logger.exception("FilePicker failed")
                self._status.value = f"Browse failed: {exc}"
                self._status.color = theme.ERR
                self.page.update()
                return
            if not chosen:
                logger.info("browse cancelled")
                return
            if side == "left":
                self._left.value = chosen
            else:
                self._right.value = chosen
            self._status.value = f"{side}: {chosen}"
            self._status.color = theme.TEXT_MUTED
            logger.info("browse chose %s", chosen)
            self.page.update()

        self.page.run_task(run)

    def _swap(self) -> None:
        self._left.value, self._right.value = self._right.value, self._left.value
        self.page.update()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._compare_btn.disabled = busy
        self._ring.visible = busy
        self.page.update()

    def _options(self):
        options = self._compare.options_from_config(self._config)
        engine = self._engine_dd.value or options.engine
        return replace(options, engine=engine).normalize()

    # -- chips / pane ------------------------------------------------------------

    def _rebuild_tab_chips(self) -> None:
        chips: list[ft.Control] = []
        for index, label in enumerate(self._tab_labels):
            selected = index == self._tab_index
            chips.append(
                ft.Container(
                    content=ft.Text(
                        label,
                        size=12,
                        color=theme.TEXT if selected else theme.TEXT_MUTED,
                        weight=ft.FontWeight.W_600 if selected else ft.FontWeight.W_400,
                    ),
                    bgcolor=theme.ACCENT_SOFT if selected else theme.SURFACE_HOVER,
                    border=theme.border_all(),
                    border_radius=theme.radius_all(8),
                    padding=theme.padding_xy(10, 6),
                    ink=True,
                    on_click=lambda _e, i=index: self._select_tab(i),
                )
            )
        self._tab_row.controls = chips
        self._close_tab_btn.disabled = self._tab_index == 0

    def _select_tab(self, index: int) -> None:
        if not 0 <= index < len(self._tab_contents):
            return
        self._tab_index = index
        self._pane.content = self._tab_contents[index]
        self._rebuild_tab_chips()
        self.page.update()

    def _set_results_content(self, content: ft.Control) -> None:
        self._tab_contents[0] = content
        if self._tab_index == 0:
            self._pane.content = content

    def _open_or_focus_tab(self, key: str, label: str, content: ft.Control) -> None:
        if key in self._tab_keys:
            index = self._tab_keys.index(key)
            self._tab_contents[index] = content
            self._tab_labels[index] = label
            self._tab_index = index
        else:
            self._tab_keys.append(key)
            self._tab_labels.append(label)
            self._tab_contents.append(content)
            self._tab_index = len(self._tab_keys) - 1
        self._pane.content = self._tab_contents[self._tab_index]
        self._rebuild_tab_chips()
        self.page.update()

    def _close_current_tab(self) -> None:
        if self._tab_index <= 0:
            return
        index = self._tab_index
        self._tab_keys.pop(index)
        self._tab_labels.pop(index)
        self._tab_contents.pop(index)
        self._tab_index = max(0, index - 1)
        self._pane.content = self._tab_contents[self._tab_index]
        self._rebuild_tab_chips()
        self.page.update()

    # -- compare -----------------------------------------------------------------

    def _run_compare(self) -> None:
        left = (self._left.value or "").strip()
        right = (self._right.value or "").strip()
        if not left or not right:
            self._snack("Both left and right paths are required", ok=False)
            return
        mode = self._mode_dd.value or "files"
        self._mode = mode
        self._set_busy(True)
        self._status.value = "Comparing…"
        self._status.color = theme.TEXT_MUTED
        self.page.update()
        logger.info("compare start mode=%s left=%s right=%s", mode, left, right)

        async def run():
            try:
                return await self._compare.compare(
                    left=left, right=right, mode=mode, options=self._options()
                )
            except Exception as exc:  # noqa: BLE001
                return exc

        future = self.page.run_task(run)
        future.add_done_callback(lambda f: self._finish_compare(f.result(), mode, left, right))

    def _finish_compare(self, result, mode: str, left: str, right: str) -> None:
        self._set_busy(False)
        if isinstance(result, Exception):
            logger.exception("compare failed: %s", result)
            self._status.value = str(result)
            self._status.color = theme.ERR
            self._snack(str(result), ok=False)
            self.page.update()
            return

        try:
            if mode == "folders":
                self._folder_result = result
                tree = self._render_folder_tree(result)
                self._set_results_content(tree)
                self._tab_index = 0
                self._pane.content = self._tab_contents[0]
                self._rebuild_tab_chips()
                counts = (
                    f"modified={result.count('modified')} only_left={result.count('only_left')} "
                    f"only_right={result.count('only_right')} identical={result.count('identical')}"
                )
                self._status.value = counts
                self._status.color = theme.OK if result.identical else theme.WARN
                self._snack("Folder compare finished", ok=True)
                self.page.update()
                logger.info("folder compare done entries=%d", len(result.entries or []))
                return

            self._folder_result = None
            summary = self._diff_summary(result)
            self._set_results_content(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text("Latest file compare", size=14, weight=ft.FontWeight.W_600, color=theme.TEXT),
                            ft.Text(f"{left}\n⇄\n{right}", size=12, color=theme.TEXT_MUTED, selectable=True),
                            ft.Text(summary, size=12, color=theme.TEXT),
                            ft.Text(
                                "Open tabs keep prior file diffs until you close them.",
                                size=12,
                                color=theme.TEXT_FAINT,
                            ),
                        ],
                        spacing=8,
                    ),
                    padding=theme.padding_all(12),
                )
            )
            label = f"{os.path.basename(left)} ⇄ {os.path.basename(right)}"
            key = f"file:{left}|{right}"
            self._open_or_focus_tab(key, label[:40], self._render_diff(result))
            self._status.value = summary
            self._status.color = theme.OK if getattr(result, "identical", False) else theme.WARN
            self._snack("File compare opened in a tab", ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("finish_compare UI failed")
            self._status.value = f"UI error: {exc}"
            self._status.color = theme.ERR
            self._set_results_content(
                ft.Text(f"Failed to render compare result:\n{exc}", color=theme.ERR, selectable=True)
            )
            self._pane.content = self._tab_contents[0]
            self.page.update()

    def _diff_summary(self, result) -> str:
        if hasattr(result, "left_hash"):
            return "Binary compare"
        stats = getattr(result, "stats", None)
        note = " (truncated)" if getattr(result, "truncated", False) else ""
        if getattr(result, "identical", False):
            return f"Identical{note}"
        if stats is None:
            return f"Diff ready{note}"
        return f"+{stats.added} −{stats.removed} ~{stats.changed} hunks={stats.hunks}{note}"

    # -- folder tree -------------------------------------------------------------

    def _render_folder_tree(self, result: FolderDiffResult) -> ft.Control:
        self._entry_checks = {}
        self._entry_by_rel = {}

        left_name = os.path.basename((result.left or "").rstrip("/")) or "left"
        right_name = os.path.basename((result.right or "").rstrip("/")) or "right"

        root: dict = {"dirs": {}, "files": []}
        for entry in result.entries:
            if self._folder_filter and self._folder_filter not in entry.relative.lower():
                continue
            parts = [p for p in entry.relative.split("/") if p]
            if not parts:
                continue
            node = root
            if entry.kind == "dir":
                for seg in parts:
                    node = node["dirs"].setdefault(seg, {"dirs": {}, "files": [], "dir_entry": None})
                node["dir_entry"] = entry
                continue
            for seg in parts[:-1]:
                node = node["dirs"].setdefault(seg, {"dirs": {}, "files": [], "dir_entry": None})
            node["files"].append((parts[-1], entry))
            self._entry_by_rel[entry.relative] = entry

        rows = self._flatten_tree_rows(root, depth=0)
        header = ft.Row(
            [
                ft.Icon(ft.Icons.ACCOUNT_TREE, color=theme.ACCENT),
                ft.Text(
                    f"{left_name}  ⇄  {right_name}",
                    size=14,
                    weight=ft.FontWeight.W_600,
                    color=theme.TEXT,
                    expand=True,
                ),
            ],
            spacing=8,
        )
        if not rows:
            return ft.Column(
                [header, ft.Text("No folder entries to show.", color=theme.TEXT_MUTED)],
                spacing=10,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            )
        return ft.Column(
            [header, *rows],
            spacing=4,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

    def _flatten_tree_rows(self, node: dict, depth: int) -> list[ft.Control]:
        """Indent-based tree (more reliable than ExpansionTile on Flet 0.86)."""
        controls: list[ft.Control] = []
        pad = depth * 16
        for name in sorted(node.get("dirs", {}), key=str.casefold):
            child = node["dirs"][name]
            dir_entry = child.get("dir_entry")
            state = getattr(dir_entry, "state", "") if dir_entry else ""
            controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.FOLDER, color=theme.ACCENT, size=18),
                            ft.Text(f"{name}/", size=13, weight=ft.FontWeight.W_500, color=theme.TEXT),
                            ft.Text(state or "folder", size=11, color=_STATE_COLORS.get(state, theme.TEXT_MUTED)),
                        ],
                        spacing=8,
                    ),
                    padding=ft.Padding(left=pad, top=4, right=4, bottom=4),
                )
            )
            controls.extend(self._flatten_tree_rows(child, depth + 1))
        for name, entry in sorted(node.get("files", []), key=lambda item: item[0].casefold()):
            controls.append(self._file_row(name, entry, pad=pad))
        return controls

    def _file_row(self, name: str, entry: FolderDiffEntry, pad: int = 0) -> ft.Control:
        check = ft.Checkbox(value=entry.state != "identical")
        self._entry_checks[entry.relative] = check
        color = _STATE_COLORS.get(entry.state, theme.TEXT)
        return ft.Container(
            content=ft.Row(
                [
                    check,
                    ft.Icon(ft.Icons.INSERT_DRIVE_FILE, color=color, size=18),
                    ft.Text(entry.state, size=11, color=color, width=90),
                    ft.Text(name, size=12, color=theme.TEXT, selectable=True, expand=True),
                    ft.TextButton(
                        content="Open",
                        on_click=lambda _e, ent=entry: self._open_entry(ent),
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            padding=ft.Padding(left=pad, top=4, right=8, bottom=4),
            border=theme.border_all(),
            border_radius=theme.radius_all(6),
            bgcolor=theme.SURFACE_HOVER,
        )

    def _selected_folder_entries(self) -> list[FolderDiffEntry]:
        if self._folder_result is None:
            return []
        selected = []
        for relative, check in self._entry_checks.items():
            if check.value and relative in self._entry_by_rel:
                selected.append(self._entry_by_rel[relative])
        return selected

    def _open_entry(self, entry: FolderDiffEntry) -> None:
        if self._folder_result is None:
            return
        left_root = self._folder_result.left
        right_root = self._folder_result.right
        left_path = os.path.join(left_root, entry.relative)
        right_path = os.path.join(right_root, entry.pair or entry.relative)
        key = f"entry:{entry.relative}"
        label = os.path.basename(entry.relative) or entry.relative
        self._set_busy(True)
        self._status.value = f"Opening {entry.relative}…"
        self.page.update()

        async def run():
            try:
                if entry.state == "only_left":
                    return await self._compare.compare(
                        left=left_path,
                        right="",
                        mode="texts_with_file",
                        options=self._options(),
                        file_side="left",
                    )
                if entry.state == "only_right":
                    return await self._compare.compare(
                        left="",
                        right=right_path,
                        mode="texts_with_file",
                        options=self._options(),
                        file_side="right",
                    )
                return await self._compare.compare(
                    left=left_path, right=right_path, mode="files", options=self._options()
                )
            except Exception as exc:  # noqa: BLE001
                return exc

        future = self.page.run_task(run)

        def done(fut) -> None:
            self._set_busy(False)
            result = fut.result()
            if isinstance(result, Exception):
                self._snack(str(result), ok=False)
                return
            pane = ft.Column(
                [
                    ft.Text(entry.relative, size=12, color=theme.TEXT_MUTED, selectable=True),
                    ft.Container(content=self._render_diff(result), expand=True),
                ],
                expand=True,
                spacing=8,
                scroll=ft.ScrollMode.AUTO,
            )
            self._open_or_focus_tab(key, label[:32], pane)
            self._status.value = self._diff_summary(result)
            self._status.color = theme.OK if getattr(result, "identical", False) else theme.WARN

        future.add_done_callback(done)

    # -- diff render -------------------------------------------------------------

    def _render_diff(self, result) -> ft.Control:
        layout = self._layout_dd.value or "side"
        if getattr(result, "kind", "") == "binary" or hasattr(result, "left_hash"):
            text = (
                f"Binary compare\nidentical={getattr(result, 'identical', False)}\n"
                f"left={getattr(result, 'left_size', '?')} bytes  right={getattr(result, 'right_size', '?')} bytes\n"
                f"left_hash={getattr(result, 'left_hash', '')}\nright_hash={getattr(result, 'right_hash', '')}\n"
                f"{getattr(result, 'sample', '')}"
            )
            return ft.Column(
                [ft.Text(text, selectable=True, size=12, font_family="Menlo", color=theme.TEXT)],
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            )

        if layout == "inline" and getattr(result, "inline_lines", None):
            rows = [
                ft.Text(
                    text,
                    size=12,
                    font_family="Menlo",
                    color=_STATE_COLORS.get(state, theme.TEXT),
                    selectable=True,
                )
                for text, state in result.inline_lines
            ]
            return ft.Column(rows, expand=True, scroll=ft.ScrollMode.AUTO, spacing=0)

        left_lines = getattr(result, "left_lines", []) or []
        right_lines = getattr(result, "right_lines", []) or []
        left_states = getattr(result, "left_states", []) or []
        right_states = getattr(result, "right_states", []) or []
        height = max(len(left_lines), len(right_lines))
        left_col: list[ft.Control] = []
        right_col: list[ft.Control] = []
        for index in range(height):
            ltext = left_lines[index] if index < len(left_lines) else ""
            rtext = right_lines[index] if index < len(right_lines) else ""
            lstate = left_states[index] if index < len(left_states) else ""
            rstate = right_states[index] if index < len(right_states) else ""
            left_col.append(
                ft.Container(
                    content=ft.Text(
                        ltext,
                        size=12,
                        font_family="Menlo",
                        color=_STATE_COLORS.get(lstate, theme.TEXT),
                        selectable=True,
                    ),
                    bgcolor=(
                        "#14532d33"
                        if lstate == "added"
                        else ("#7f1d1d33" if lstate == "removed" else ("#713f1233" if lstate == "changed" else None))
                    ),
                    padding=ft.Padding(4, 1, 4, 1),
                )
            )
            right_col.append(
                ft.Container(
                    content=ft.Text(
                        rtext,
                        size=12,
                        font_family="Menlo",
                        color=_STATE_COLORS.get(rstate, theme.TEXT),
                        selectable=True,
                    ),
                    bgcolor=(
                        "#14532d33"
                        if rstate == "added"
                        else ("#7f1d1d33" if rstate == "removed" else ("#713f1233" if rstate == "changed" else None))
                    ),
                    padding=ft.Padding(4, 1, 4, 1),
                )
            )
        return ft.Row(
            [
                ft.Container(
                    content=ft.Column(left_col, expand=True, scroll=ft.ScrollMode.AUTO, spacing=0),
                    expand=True,
                    border=theme.border_all(),
                    border_radius=theme.radius_all(8),
                ),
                ft.Container(
                    content=ft.Column(right_col, expand=True, scroll=ft.ScrollMode.AUTO, spacing=0),
                    expand=True,
                    border=theme.border_all(),
                    border_radius=theme.radius_all(8),
                ),
            ],
            expand=True,
            spacing=8,
        )

    def _run_sync(self, operation: str) -> None:
        if self._folder_result is None:
            self._snack("Run a folder compare first", ok=False)
            return
        entries = self._selected_folder_entries()
        if not entries:
            self._snack("Select at least one entry", ok=False)
            return
        self._set_busy(True)
        self._status.value = f"Syncing ({operation})…"
        self.page.update()

        async def run():
            try:
                return await self._compare.sync(
                    entries,
                    operation,
                    self._folder_result.left,
                    self._folder_result.right,
                )
            except Exception as exc:  # noqa: BLE001
                return exc

        future = self.page.run_task(run)

        def done(fut) -> None:
            self._set_busy(False)
            report = fut.result()
            if isinstance(report, Exception):
                self._snack(str(report), ok=False)
                return
            failed = report.get("failed") or []
            self._status.value = (
                f"copied={report.get('copied', 0)} overwritten={report.get('overwritten', 0)} "
                f"deleted={report.get('deleted', 0)} failed={len(failed)}"
            )
            self._status.color = theme.ERR if failed else theme.OK
            self._snack(self._status.value, ok=not failed)
            self._run_compare()

        future.add_done_callback(done)

    def _snack(self, message: str, ok: bool = True) -> None:
        self._status.value = message
        self._status.color = theme.OK if ok else theme.ERR
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            logger.exception("status update failed")


def build_compare_screen(shell) -> ft.Control:
    try:
        return _CompareView(shell).build()
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_compare_screen failed")
        return ft.Container(
            expand=True,
            bgcolor=theme.SURFACE,
            padding=theme.padding_all(20),
            content=ft.Text(f"Compare screen failed to build:\n{exc}", color=theme.ERR, selectable=True),
        )
