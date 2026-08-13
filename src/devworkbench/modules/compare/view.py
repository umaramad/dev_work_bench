"""Compare screen — file / folder diff wired to the comparison engine.

The view is provider-agnostic in the same spirit as the AI module: it reads
its options from ``ConfigurationService`` (Settings → Compare), then hands
the two paths to a ``CompareWorker`` that runs the engine off-thread. The
``CodeView`` panes render whatever ``DiffResult`` comes back — side-by-side
or inline — with syntax highlighting, folds, search/replace, minimap and
scrollbar markers; folder results render as a collapsible tree whose
sections can be minimized.
"""

from __future__ import annotations

import datetime
import os

from PySide6.QtCore import QThreadPool, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devworkbench.modules.base import Module
from devworkbench.services.compare.models import CompareOptions, detect_kind
from devworkbench.ui.samples import DIFF_LEFT, DIFF_RIGHT
from devworkbench.ui.theme import current_colors
from devworkbench.ui.widgets.code_view import CodeView
from devworkbench.ui.widgets.common import button, icon_button, panel, splitter, styled_label
from devworkbench.workers.compare_worker import CompareWorker
from devworkbench.workers.folder_sync_worker import FolderSyncWorker

def build_view(icons, ctx=None) -> QWidget:
    service = ctx.resolve("services.configuration") if ctx is not None and ctx.has("services.configuration") else None
    events = ctx.resolve("core.events") if ctx is not None and ctx.has("core.events") else None

    def opt(key: str, default):
        if service is None:
            return default
        try:
            return service.get(key)
        except Exception:  # noqa: BLE001 — settings must never block a compare
            return default

    root = QWidget()
    layout = QVBoxLayout(root)
    layout.setContentsMargins(10, 10, 10, 8)
    layout.setSpacing(8)

    # ---- header: pickers + mode + actions -----------------------------------
    header = QWidget()
    header_layout = QVBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.setSpacing(6)

    left_edit = QLineEdit()
    left_edit.setPlaceholderText("Choose the left file or folder…")
    left_edit.setClearButtonEnabled(True)
    right_edit = QLineEdit()
    right_edit.setPlaceholderText("Choose the right file or folder…")
    right_edit.setClearButtonEnabled(True)

    def browse(edit: QLineEdit) -> None:
        if mode_combo.currentData() == "folders":
            chosen = QFileDialog.getExistingDirectory(root, "Choose folder", edit.text().strip() or os.path.expanduser("~"))
        else:
            chosen, _ = QFileDialog.getOpenFileName(root, "Choose file", edit.text().strip() or os.path.expanduser("~"))
        if chosen:
            edit.setText(chosen)

    browse_left = icon_button(icons, "folder", "Browse left…")
    browse_right = icon_button(icons, "folder", "Browse right…")
    browse_left.clicked.connect(lambda: browse(left_edit))
    browse_right.clicked.connect(lambda: browse(right_edit))
    picker_row = QWidget()
    picker_layout = QHBoxLayout(picker_row)
    picker_layout.setContentsMargins(0, 0, 0, 0)
    picker_layout.setSpacing(6)
    picker_layout.addWidget(browse_left, 0, Qt.AlignmentFlag.AlignTop)
    picker_layout.addWidget(left_edit, 1)
    swap_button = icon_button(icons, "swap", "Swap sides")
    picker_layout.addWidget(swap_button, 0, Qt.AlignmentFlag.AlignTop)
    picker_layout.addWidget(browse_right, 0, Qt.AlignmentFlag.AlignTop)
    picker_layout.addWidget(right_edit, 1)
    header_layout.addWidget(picker_row)

    mode_combo = QComboBox()
    mode_combo.addItem("Files", "files")
    mode_combo.addItem("Folders", "folders")
    engine_combo = QComboBox()
    engine_combo.addItem("Engine: auto", "auto")
    engine_combo.addItem("Engine: Myers", "myers")
    engine_combo.addItem("Engine: difflib", "difflib")

    def refresh_engine_combo() -> None:
        configured = opt("compare.engine", "auto")
        index = engine_combo.findData(configured)
        engine_combo.setCurrentIndex(index if index >= 0 else 0)

    refresh_engine_combo()

    view_combo = QComboBox()
    view_combo.addItem("Side by side", "sbs")
    view_combo.addItem("Inline", "inline")

    compare_button = button("Compare", "primary")
    compare_button.setMinimumWidth(110)
    rule_label = styled_label("Rules from Settings → Compare", "tiny")

    action_row = QWidget()
    action_layout = QHBoxLayout(action_row)
    action_layout.setContentsMargins(0, 0, 0, 0)
    action_layout.setSpacing(8)
    action_layout.addWidget(styled_label("Mode", "muted"))
    action_layout.addWidget(mode_combo)
    action_layout.addSpacing(6)
    action_layout.addWidget(engine_combo)
    action_layout.addSpacing(6)
    action_layout.addWidget(view_combo)
    action_layout.addStretch(1)
    action_layout.addWidget(rule_label)
    action_layout.addWidget(compare_button)
    header_layout.addWidget(action_row)
    layout.addWidget(header)

    # ---- search / replace bar --------------------------------------------------
    find_bar = QWidget()
    find_bar.setVisible(False)
    find_layout = QHBoxLayout(find_bar)
    find_layout.setContentsMargins(0, 0, 0, 0)
    find_layout.setSpacing(6)

    find_query = QLineEdit()
    find_query.setPlaceholderText("Find in comparison…")
    find_query.setClearButtonEnabled(True)
    find_query.setMinimumWidth(220)
    find_query.setProperty("class", "search")

    case_button = button("Aa", "ghost")
    case_button.setCheckable(True)
    case_button.setChecked(True)
    case_button.setToolTip("Match case (toggle)")

    find_prev = icon_button(icons, "chevron_left", "Previous match (Shift+Enter)")
    find_next = icon_button(icons, "chevron_right", "Next match (Enter)")
    match_label = styled_label("0 matches", "hint")
    match_label.setMinimumWidth(72)

    replace_edit = QLineEdit()
    replace_edit.setPlaceholderText("Replace with…")
    replace_edit.setMinimumWidth(160)
    replace_button = button("Replace", "ghost")
    replace_all_button = button("Replace all", "ghost")
    close_find = icon_button(icons, "close", "Close (Esc)")

    find_layout.addWidget(find_query, 1)
    find_layout.addWidget(case_button)
    find_layout.addWidget(find_prev)
    find_layout.addWidget(find_next)
    find_layout.addWidget(match_label)
    find_layout.addWidget(styled_label("·", "muted"))
    find_layout.addWidget(replace_edit, 1)
    find_layout.addWidget(replace_button)
    find_layout.addWidget(replace_all_button)
    find_layout.addWidget(close_find)
    layout.addWidget(find_bar)

    # ---- dirty buffer banner ----------------------------------------------------
    dirty_bar = QWidget()
    dirty_bar.setVisible(False)
    dirty_layout = QHBoxLayout(dirty_bar)
    dirty_layout.setContentsMargins(8, 4, 8, 4)
    dirty_layout.setSpacing(8)
    dirty_label = styled_label("Left file modified in memory — save to disk or revert.", "hint")
    save_left = button("Save left file", "ghost")
    revert_left = button("Revert", "ghost")
    dirty_layout.addWidget(QLabel("●"))
    dirty_layout.addWidget(dirty_label)
    dirty_layout.addStretch(1)
    dirty_layout.addWidget(save_left)
    dirty_layout.addWidget(revert_left)
    layout.addWidget(dirty_bar)

    # ---- content: diff panes or folder table -----------------------------------
    stack = QStackedWidget()
    stack.setObjectName("compareStack")

    diff_split = splitter(Qt.Orientation.Horizontal)
    left_view = CodeView(kind="text")
    right_view = CodeView(kind="text")
    diff_split.addWidget(left_view)
    diff_split.addWidget(right_view)
    diff_split.setSizes([560, 560])
    stack.addWidget(diff_split)

    # Folder compare page: sync toolbar + results table.
    folder_page = QWidget()
    folder_page_layout = QVBoxLayout(folder_page)
    folder_page_layout.setContentsMargins(0, 0, 0, 0)
    folder_page_layout.setSpacing(6)

    sync_bar = QWidget()
    sync_layout = QHBoxLayout(sync_bar)
    sync_layout.setContentsMargins(0, 0, 0, 0)
    sync_layout.setSpacing(6)
    copy_left_to_right = button("Copy → right", "ghost")
    copy_right_to_left = button("Copy ← left", "ghost")
    delete_selected = button("Delete", "danger")
    diff_selected = button("Diff", "ghost")
    diff_selected.setToolTip("Open the selected file as a side-by-side diff")
    refresh_folder = icon_button(icons, "refresh", "Re-compare folders")
    ignore_hint = styled_label("", "tiny")
    row_hint = styled_label("Double-click a file row to see its diff", "tiny")
    sync_layout.addWidget(copy_left_to_right)
    sync_layout.addWidget(copy_right_to_left)
    sync_layout.addWidget(delete_selected)
    sync_layout.addWidget(diff_selected)
    sync_layout.addWidget(refresh_folder)
    sync_layout.addSpacing(10)
    collapse_all = icon_button(icons, "chevron_up", "Collapse all")
    collapse_all.setObjectName("collapseAllButton")
    expand_all = icon_button(icons, "chevron_down", "Expand all")
    expand_all.setObjectName("expandAllButton")
    hide_identical = button("Hide identical", "ghost")
    hide_identical.setObjectName("hideIdenticalButton")
    hide_identical.setCheckable(True)
    hide_identical.setToolTip("Show only differing files and folders — identical-only subtrees are hidden")
    sync_layout.addWidget(collapse_all)
    sync_layout.addWidget(expand_all)
    sync_layout.addWidget(hide_identical)
    sync_layout.addSpacing(10)
    sync_layout.addWidget(ignore_hint)
    sync_layout.addStretch(1)
    sync_layout.addWidget(row_hint)
    folder_page_layout.addWidget(sync_bar)

    # Folder results render as a tree: directories are collapsible nodes,
    # files are leaves. Directory rows summarize their subtree (e.g.
    # "2 mod · 1 add") so a section can be minimized to a single line.
    folder_tree = QTreeWidget()
    folder_tree.setObjectName("folderTree")
    folder_tree.setColumnCount(6)
    folder_tree.setHeaderLabels(["State", "Path", "L size", "R size", "L modified", "R modified"])
    folder_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    folder_tree.setUniformRowHeights(True)
    folder_tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    folder_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    folder_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    folder_tree.setRootIsDecorated(True)
    folder_tree.setAnimated(True)  # smooth expand/collapse for the folder tree
    folder_tree.setIndentation(20)
    folder_tree.setSortingEnabled(False)  # children keep the engine's path order
    folder_page_layout.addWidget(folder_tree, 1)
    stack.addWidget(folder_page)

    inline_view = CodeView(kind="text")
    stack.addWidget(inline_view)

    # Tabbed content: tab 0 is the mode-driven compare (files / folders /
    # inline). Every file diff opened from the folder table becomes its own
    # tab, so the folder results stay visible while you inspect files one by
    # one. The tab bar is hidden until at least one file diff is opened.
    tabs = QTabWidget()
    tabs.setObjectName("compareTabs")
    tabs.setDocumentMode(True)
    tabs.setTabsClosable(True)
    tabs.addTab(stack, "Files")
    tabs.tabBar().setVisible(False)  # revealed when the first diff tab opens
    # Tab 0 (the compare stack) never closes; only file-diff tabs do.
    tabs.tabBar().setTabButton(0, QTabBar.ButtonPosition.RightSide, None)

    layout.addWidget(tabs, 1)

    # ---- summary / status bar ----------------------------------------------------
    summary = panel()
    summary_layout = QVBoxLayout(summary)
    summary_layout.setContentsMargins(10, 8, 10, 8)
    summary_layout.setSpacing(4)

    verdict_label = styled_label("Nothing compared yet", "muted")
    stats_row = QWidget()
    stats_layout = QHBoxLayout(stats_row)
    stats_layout.setContentsMargins(0, 0, 0, 0)
    stats_layout.setSpacing(10)
    stats_layout.addWidget(verdict_label)
    stats_layout.addStretch(1)
    prev_hunk_button = icon_button(icons, "chevron_left", "Previous difference")
    next_hunk_button = icon_button(icons, "chevron_right", "Next difference")
    stats_layout.addWidget(prev_hunk_button)
    stats_layout.addWidget(next_hunk_button)
    summary_layout.addWidget(stats_row)

    detail_label = styled_label("", "hint")
    detail_label.setWordWrap(True)
    detail_label.hide()
    summary_layout.addWidget(detail_label)

    status_row = QWidget()
    status_layout = QHBoxLayout(status_row)
    status_layout.setContentsMargins(0, 0, 0, 0)
    status_layout.setSpacing(12)
    line_label = styled_label("", "hint")
    search_label = styled_label("", "hint")
    fold_label = styled_label("", "hint")
    zoom_label = styled_label("100%", "hint")
    status_layout.addWidget(line_label)
    status_layout.addWidget(search_label)
    status_layout.addWidget(fold_label)
    status_layout.addStretch(1)
    status_layout.addWidget(zoom_label)
    summary_layout.addWidget(status_row)
    layout.addWidget(summary)

    # ---- worker execution -----------------------------------------------------------
    # Workers must be retained until their signals deliver (see Worker contract).
    pending_workers: list = []
    hunk_starts: list[int] = []
    inline_hunk_starts: list[int] = []
    mode = "files"
    last_result = {"result": None, "kind": "text", "left_path": ""}
    dirty = {"value": False}
    folder_roots = {"left": "", "right": ""}
    folder_entries: list = []
    # File-diff tabs opened from the folder table: key -> tab state. The
    # folder results stay on tab 0; each opened file gets its own tab.
    diff_tabs: dict[str, dict] = {}
    _tab_key_of: dict[QWidget, str] = {}
    # Last verdict/detail of the main (tab 0) compare, restored when the
    # user switches back from a file-diff tab.
    main_summary = {"verdict": "", "detail": ""}

    def build_options() -> CompareOptions:
        return CompareOptions(
            ignore_whitespace=bool(opt("compare.ignore_whitespace", False)),
            ignore_case=bool(opt("compare.ignore_case", False)),
            ignore_comments=bool(opt("compare.ignore_comments", False)),
            ignore_blank_lines=bool(opt("compare.ignore_blank_lines", False)),
            context_lines=int(opt("compare.context_lines", 3)),
            engine=str(engine_combo.currentData()),
            follow_symlinks=bool(opt("compare.follow_symlinks", True)),
        )

    def set_busy(busy: bool) -> None:
        compare_button.setEnabled(not busy)
        compare_button.setText("Comparing…" if busy else "Compare")

    def show_message(text: str) -> None:
        verdict_label.setText(text)
        detail_label.hide()

    def show_detail(text: str) -> None:
        detail_label.setText(text)
        detail_label.show()

    def set_dirty(value: bool) -> None:
        dirty["value"] = value
        dirty_bar.setVisible(value)

    # --- search -----------------------------------------------------------------

    def active_panes() -> list[CodeView]:
        """The panes of the currently visible tab (diff tab or the main stack)."""
        info = _current_diff_tab()
        if info is not None:
            return info["panes"]
        if view_combo.currentData() == "inline":
            return [inline_view]
        return [left_view, right_view]

    def run_search() -> None:
        query = find_query.text()
        case = case_button.isChecked()
        total = 0
        for pane in active_panes():
            total += pane.find(query, case)
        match_label.setText(f"{total} match{'es' if total != 1 else ''}" if total else "0 matches")
        search_label.setText(f"⧉ {total} found" if query and total else ("⧉ none found" if query else ""))

    def goto_match(forward: bool) -> None:
        found_any = False
        for pane in active_panes():
            if pane.goto_match(forward):
                found_any = True
        if found_any:
            shown = sum(max(0, p.current_match_index() + 1) for p in active_panes())
            total = sum(p.match_count() for p in active_panes())
            match_label.setText(f"{shown}/{total}")

    def show_find() -> None:
        find_bar.setVisible(True)
        find_query.setFocus()
        find_query.selectAll()
        run_search()

    def hide_find() -> None:
        find_bar.setVisible(False)
        for pane in active_panes():
            pane.clear_search()
        match_label.setText("0 matches")
        search_label.setText("")
        _update_status_labels()

    def _replace_enabled() -> bool:
        return bool(last_result["result"] and last_result["left_path"] and find_query.text() and replace_edit.text())

    def replace_left() -> None:
        if not _replace_enabled():
            return
        if left_view.replace_next(replace_edit.text()):
            set_dirty(True)
            _rediff_after_edit()

    def replace_all_left() -> None:
        if not _replace_enabled():
            return
        count = left_view.replace_all(find_query.text(), replace_edit.text(), case_button.isChecked())
        if count:
            set_dirty(True)
            _rediff_after_edit()

    def _rediff_after_edit() -> None:
        """Re-run the comparison with the edited left buffer (keeps both panes live)."""
        if not last_result["result"]:
            return
        if compare_button.text() == "Comparing…":
            return
        options = build_options()
        set_busy(True)
        worker = CompareWorker(
            left="\n".join(left_view.content_lines()),
            right="\n".join(right_view.content_lines()),
            mode="texts",
            options=options,
            kind=last_result["kind"],
        )
        pending_workers.append(worker)

        def done(result, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            set_busy(False)
            render_diff(result)
            if find_bar.isVisible():
                run_search()

        def failed(exc, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            set_busy(False)
            show_message(f"Re-compare failed: {exc}")

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def save_left_file() -> None:
        path = last_result["left_path"]
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(left_view.content_lines()) + "\n")
            set_dirty(False)
            show_detail("Left file saved.")
        except OSError as exc:
            show_message(f"Save failed: {exc}")

    def revert_left_file() -> None:
        left_edit.setText(last_result["left_path"])
        set_dirty(False)
        compare()

    # --- rendering ---------------------------------------------------------------

    def populate_panes(pane_left: CodeView, pane_right: CodeView, result) -> None:
        """Fill two CodeView panes from a DiffResult (content, states, segments)."""
        kind = result.kind
        pane_left.set_kind(kind)
        pane_right.set_kind(kind)
        left_lines = result.left_lines or []
        right_lines = result.right_lines or []
        left_states = list(result.left_states or [])
        right_states = list(result.right_states or [])
        while len(left_states) < len(left_lines):
            left_states.append("")
        while len(right_states) < len(right_lines):
            right_states.append("")
        pane_left.set_content(left_lines, left_states)
        pane_right.set_content(right_lines, right_states)
        # Intra-line segments for paired "changed" lines (side-by-side panes).
        if result.intraline:
            left_segments: dict[int, list] = {}
            right_segments: dict[int, list] = {}
            for (left_index, right_index), pair in result.intraline.items():
                left_segments[left_index] = pair.left
                right_segments[right_index] = pair.right
            pane_left.set_segments(left_segments)
            pane_right.set_segments(right_segments)

    def hunk_start_lines(result) -> list[int]:
        """Logical line numbers where each hunk begins (for prev/next nav)."""
        starts: list[int] = []
        if result.hunks:
            for hunk in result.hunks:
                if hunk.lines:
                    line = hunk.lines[0]
                    starts.append(line.left_index if line.left_index >= 0 else line.right_index)
        return starts

    def render_diff(result) -> None:
        nonlocal hunk_starts, inline_hunk_starts
        colors = current_colors()  # fresh snapshot — theme may have changed since build
        kind = result.kind
        last_result["result"] = result
        last_result["kind"] = kind
        # In-memory re-diffs (replace) come back with an empty path — keep the
        # original file path so Save / Revert / further replaces still work.
        last_result["left_path"] = result.left_path or last_result["left_path"]

        populate_panes(left_view, right_view, result)
        inline_view.set_kind(kind)
        inline_view.set_content(
            [text for text, _ in (result.inline_lines or [])],
            [state for _, state in (result.inline_lines or [])],
        )

        parts = _stats_html(result, colors)

        if result.identical:
            verdict_label.setText(f"<span style='color:{colors['green']}'>●</span> Files are identical")
        else:
            verdict_label.setText(f"<span style='color:{colors['red']}'>●</span> Files differ")

        if result.message:
            show_detail(result.message)
        elif parts:
            show_detail(parts)
        else:
            detail_label.hide()
        main_summary["verdict"] = verdict_label.text()
        main_summary["detail"] = detail_label.text() if detail_label.isVisible() else ""

        # Hunk navigation targets.
        hunk_starts = hunk_start_lines(result)
        inline_hunk_starts = _inline_change_starts(result.inline_lines or [])
        prev_hunk_button.setEnabled(bool(hunk_starts) or bool(inline_hunk_starts))
        next_hunk_button.setEnabled(bool(hunk_starts) or bool(inline_hunk_starts))

        _update_status_labels()
        _update_zoom_label()

    def _update_status_labels() -> None:
        info = active_panes()[0].status_info()
        total = info["total_lines"]
        line_label.setText(f"Ln {info.get('scroll_top', 0) + 1} / {total:,}")
        folded = info["folded"]
        hidden = info["hidden"]
        fold_label.setText(f"▾ {folded} folded ({hidden:,} hidden)" if folded else "")

    def _update_zoom_label() -> None:
        point = active_panes()[0].status_info()["zoom"]
        zoom_label.setText(f"{int(round(point / 12.0 * 100))}%")

    def render_folder(result) -> None:
        nonlocal hunk_starts, inline_hunk_starts, folder_entries
        colors = current_colors()  # fresh snapshot — theme may have changed since build
        hunk_starts = []
        inline_hunk_starts = []
        prev_hunk_button.setEnabled(False)
        next_hunk_button.setEnabled(False)
        last_result["result"] = result
        last_result["left_path"] = ""
        folder_roots["left"] = result.left or ""
        folder_roots["right"] = result.right or ""
        folder_entries = result.entries or []

        ignore_names = _parse_ignore_dirs(opt("compare.ignore_dirs", ".git,.idea,target,build,node_modules"))
        ignore_hint.setText(f"Ignoring: {', '.join(ignore_names)}" if ignore_names else "")
        if result.skipped_dirs:
            ignore_hint.setText(ignore_hint.text() + f"  ·  {result.skipped_dirs} folders skipped")

        folder_tree.clear()
        nodes: dict[str, QTreeWidgetItem] = {}
        dir_counters: dict[str, dict[str, int]] = {}
        root_item = folder_tree.invisibleRootItem()

        # Root node names both folders so the tree is self-describing, and
        # summarizes the whole comparison in its State column.
        left_name = os.path.basename((result.left or "").rstrip("/")) or (result.left or "left")
        right_name = os.path.basename((result.right or "").rstrip("/")) or (result.right or "right")
        root = QTreeWidgetItem(["", f"{left_name}  ⇄  {right_name}"])
        root.setData(0, Qt.ItemDataRole.UserRole, None)
        root.setData(0, _PURE_ROLE, False)  # the root row is never hidden by the filter
        root.setIcon(1, icons.get("compare", 14))
        root.setForeground(1, QColor(colors["text2"]))
        root_font = root.font(1)
        root_font.setBold(True)
        root.setFont(1, root_font)
        root.setToolTip(1, f"{result.left or ''}\n⇄\n{result.right or ''}")
        root_item.addChild(root)

        def ensure_dir(segments: list[str]) -> QTreeWidgetItem:
            """The (created-on-demand) tree node for a directory path."""
            node = root
            path = ""
            for seg in segments:
                path = f"{path}/{seg}" if path else seg
                existing = nodes.get(path)
                if existing is None:
                    existing = QTreeWidgetItem([""])
                    existing.setData(0, Qt.ItemDataRole.UserRole, None)
                    existing.setForeground(0, QColor(colors["text3"]))
                    existing.setIcon(1, icons.get("folder", 14))
                    existing.setText(1, f"{seg}/")
                    node.addChild(existing)
                    nodes[path] = existing
                node = existing
            return node

        def bump_dir_counts(segments: list[str], state: str) -> None:
            """Count a changed file into every ancestor directory."""
            path = ""
            for seg in segments:
                path = f"{path}/{seg}" if path else seg
                counter = dir_counters.setdefault(path, {})
                counter[state] = counter.get(state, 0) + 1

        counts = {"identical": 0, "modified": 0, "only_left": 0, "only_right": 0, "moved": 0, "renamed": 0}
        identical_bytes = 0
        modified_bytes = 0
        stray_paths: set[str] = set()
        for entry_index, entry in enumerate(folder_entries):
            state = entry.state
            counts[state] = counts.get(state, 0) + 1
            if state == "identical":
                identical_bytes += max(0, entry.left_size)
            elif state == "modified":
                modified_bytes += max(0, entry.left_size)
            parts = entry.relative.split("/")
            if entry.kind == "dir":
                # A stray directory present on only one side: show it as a
                # node with its own state (its files get their own rows).
                # No entry index is stored: dir rows are never diffed and
                # must never be selected for sync (file-only operations).
                stray_paths.add("/".join(parts))
                node = ensure_dir(parts)
                display, token = _STATE_DISPLAY.get(state, (state, "text3"))
                if state in ("moved", "renamed"):
                    display += " ↦"
                node.setText(0, display)
                node.setForeground(0, QColor(colors[token]))
                node.setToolTip(0, _state_tooltip(entry))
                continue
            # File leaf under its directory chain.
            bump_dir_counts(parts[:-1], state)
            parent = ensure_dir(parts[:-1])
            item = QTreeWidgetItem()
            display, token = _STATE_DISPLAY.get(state, (state, "text3"))
            if state in ("moved", "renamed"):
                display += " ↦"
            item.setText(0, display)
            item.setForeground(0, QColor(colors[token]))
            item.setToolTip(0, _state_tooltip(entry))
            item.setData(0, Qt.ItemDataRole.UserRole, entry_index)
            item.setData(0, _PURE_ROLE, state == "identical")
            # The tree already shows the directory chain, so leaves display
            # just their basename; the tooltip keeps the full path.
            path_text = _folder_tree_path(entry)
            item.setText(1, path_text)
            item.setIcon(1, icons.get("file", 14))
            item.setToolTip(1, _folder_path_text(entry))
            item.setText(2, _fmt_size(entry.left_size))
            item.setText(3, _fmt_size(entry.right_size))
            item.setText(4, _fmt_time(entry.mtime_left))
            item.setText(5, _fmt_time(entry.mtime_right))
            parent.addChild(item)

        # Summarize each directory's subtree in its State column so a section
        # can be collapsed down to one informative line.
        for path, counter in dir_counters.items():
            node = nodes.get(path)
            if node is None:
                continue
            label, token = _dir_aggregate(counter)
            if label:
                node.setText(0, label)
                node.setForeground(0, QColor(colors[token]))
                node.setToolTip(0, _dir_aggregate_tooltip(path, counter))
        # Root row summarizes the whole comparison.
        root_label, root_token = _dir_aggregate(counts)
        if root_label:
            root.setText(0, root_label)
            root.setForeground(0, QColor(colors[root_token]))
            root.setToolTip(0, _dir_aggregate_tooltip(f"{left_name} ⇄ {right_name}", counts))
        # Mark which directories contain any difference (for the
        # Hide-identical filter): a dir is pure when none of its subtree's
        # files differ and it is not a stray one-sided directory.
        difference_dirs = {
            path for path, counter in dir_counters.items()
            if any(state != "identical" for state in counter)
        }
        difference_dirs.update(stray_paths)
        for path, node in nodes.items():
            node.setData(0, _PURE_ROLE, path not in difference_dirs)
        folder_tree.expandAll()
        apply_tree_filter()

        parts = [
            f"<span style='color:{colors['green']}'>{counts['identical']} identical</span>",
            f"<span style='color:{colors['amber']}'>{counts['modified']} modified</span>",
            f"<span style='color:{colors['red']}'>{counts['only_left']} deleted</span>",
            f"<span style='color:{colors['green']}'>{counts['only_right']} added</span>",
        ]
        if counts["moved"]:
            parts.append(f"<span style='color:{colors['cyan']}'>{counts['moved']} moved</span>")
        if counts["renamed"]:
            parts.append(f"<span style='color:{colors['cyan']}'>{counts['renamed']} renamed</span>")
        verdict_label.setText(f"● {' · '.join(parts)}")

        detail = [
            f"{_fmt_size(identical_bytes)} identical content",
            f"{_fmt_size(modified_bytes)} modified content",
        ]
        if result.moves:
            detail.append(f"{result.moves} same-content pairs")
        show_detail(" · ".join(detail))
        main_summary["verdict"] = verdict_label.text()
        main_summary["detail"] = detail_label.text() if detail_label.isVisible() else ""
        _update_status_labels()

    def apply_tree_filter() -> None:
        """Show or hide identical-only rows per the filter toggle.

        View-only: rows are hidden, never removed, so toggling back restores
        them instantly. A row is hidden when the filter is on and it is
        marked ``_PURE_ROLE`` (identical file, or a directory whose whole
        subtree matches). Runs after every folder render so the preference
        carries across re-compares.
        """
        filter_on = hide_identical.isChecked()
        hidden_count = 0

        def walk(parent_item) -> None:
            nonlocal hidden_count
            for index in range(parent_item.childCount()):
                item = parent_item.child(index)
                hidden = bool(filter_on and item.data(0, _PURE_ROLE))
                item.setHidden(hidden)
                if hidden:
                    hidden_count += 1
                walk(item)

        walk(folder_tree.invisibleRootItem())
        if filter_on:
            row_hint.setText(
                f"Hide identical: {hidden_count:,} row{'s' if hidden_count != 1 else ''} hidden · "
                "double-click a file row to see its diff"
            )
        else:
            row_hint.setText("Double-click a file row to see its diff")

    # ---- folder synchronization --------------------------------------------------

    def selected_folder_entries() -> list:
        """The file entries behind the currently selected tree items (dirs are
        never included — sync operations are file-only)."""
        selected: list = []
        for item in folder_tree.selectedItems():
            entry_index = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(entry_index, int) and 0 <= entry_index < len(folder_entries):
                entry = folder_entries[entry_index]
                if entry.kind == "file":
                    selected.append(entry)
        return selected

    def entry_from_item(item) -> object | None:
        """The folder entry behind a tree item (None for directory nodes)."""
        if item is None:
            return None
        entry_index = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(entry_index, int) and 0 <= entry_index < len(folder_entries):
            return folder_entries[entry_index]
        return None

    def _current_diff_tab() -> dict | None:
        """State dict of the file-diff tab currently shown (None on tab 0)."""
        key = _tab_key_of.get(tabs.currentWidget())
        return diff_tabs.get(key) if key else None

    def _apply_tab_summary(info: dict) -> None:
        """Point the bottom summary + hunk buttons at a diff tab's result."""
        verdict_label.setText(info["verdict"])
        if info["detail"]:
            show_detail(info["detail"])
        else:
            detail_label.hide()
        prev_hunk_button.setEnabled(bool(info["hunks"]))
        next_hunk_button.setEnabled(bool(info["hunks"]))

    def _clear_tab_busy(info: dict) -> None:
        """Drop the busy marker from a tab once its worker has finished."""
        index = tabs.indexOf(info["widget"])
        if index >= 0:
            tabs.setTabText(index, info["title"])

    def open_entry_diff(entry) -> None:
        """Open the entry's file diff in its own tab.

        The folder results stay visible on tab 0; every opened file gets a
        dedicated tab with its own side-by-side panes, compared off-thread.
        Re-clicking a row whose tab is already open just activates that tab.
        Both-sided entries (modified / moved / renamed / identical) run a
        standard file comparison; single-sided entries diff the existing file
        against an empty document so every row can be inspected.
        """
        if entry.kind != "file":
            show_message("Directories are summarized in the tree — open a file entry to see its diff.")
            return
        left_root = folder_roots["left"]
        right_root = folder_roots["right"]
        if not left_root or not right_root:
            show_message("Compare two folders first.")
            return
        left_rel = entry.relative
        right_rel = entry.pair if entry.state in ("moved", "renamed") and entry.pair else entry.relative
        left_path = os.path.join(left_root, left_rel)
        right_path = os.path.join(right_root, right_rel)
        key = f"{left_path}\x00{right_path}"

        # Activate an already-open tab even while its worker is still busy,
        # so re-clicking a row always brings its diff to the front.
        existing = diff_tabs.get(key)
        if existing is not None:
            tabs.setCurrentWidget(existing["widget"])
            return
        if compare_button.text() == "Comparing…":
            return  # already in flight — serializes tab diffs too

        # Build the tab: title + its own side-by-side panes.
        if entry.state in ("moved", "renamed") and entry.pair:
            title = f"{left_rel} → {entry.pair}"
        else:
            title = entry.relative
        tab_widget = QWidget()
        tab_layout = QVBoxLayout(tab_widget)
        tab_layout.setContentsMargins(6, 6, 6, 6)
        tab_split = splitter(Qt.Orientation.Horizontal)
        pane_left = CodeView(kind="text")
        pane_right = CodeView(kind="text")
        tab_split.addWidget(pane_left)
        tab_split.addWidget(pane_right)
        tab_split.setSizes([560, 560])
        tab_layout.addWidget(tab_split)
        index = tabs.addTab(tab_widget, title)
        tabs.setTabToolTip(index, f"{left_path}\n↔\n{right_path}")
        _sync_tab_bar()
        info = {
            "widget": tab_widget,
            "panes": [pane_left, pane_right],
            "hunks": [],
            "verdict": "Comparing…",
            "detail": "",
            "title": title,
        }
        diff_tabs[key] = info
        _tab_key_of[tab_widget] = key
        tabs.setCurrentWidget(tab_widget)

        # Per-tab busy: the main Compare button stays free so several file
        # diffs can be opened in a row; each tab shows a busy marker instead.
        tabs.setTabText(index, f"⏳ {title}")
        if entry.state in ("only_left", "only_right"):
            # No counterpart on the other side: diff the file against empty.
            # The file is read by the worker (off the UI thread) so a huge
            # one-sided file never blocks the interface.
            path = left_path if entry.state == "only_left" else right_path
            worker = CompareWorker(
                left=path if entry.state == "only_left" else "",
                right=path if entry.state == "only_right" else "",
                mode="texts_with_file",
                file_side="left" if entry.state == "only_left" else "right",
                options=build_options(),
                kind=detect_kind(path),
            )
        else:
            worker = CompareWorker(left=left_path, right=right_path, mode="files", options=build_options())
        pending_workers.append(worker)

        def tab_done(result, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            # The tab may have been closed while the worker ran — cleanup must
            # still run, but the panes are gone and must not be touched.
            if diff_tabs.get(key) is not info:
                return
            _clear_tab_busy(info)
            populate_panes(pane_left, pane_right, result)
            colors = current_colors()
            info["hunks"] = hunk_start_lines(result)
            verdict = (
                f"<span style='color:{colors['green']}'>●</span> Files are identical"
                if result.identical
                else f"<span style='color:{colors['red']}'>●</span> Files differ"
            )
            info["verdict"] = verdict
            parts = [p for p in (_stats_html(result, colors), result.message) if p]
            info["detail"] = " · ".join(parts)
            if _current_diff_tab() is info:
                _apply_tab_summary(info)
            _update_status_labels()
            _update_zoom_label()

        def tab_failed(exc, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            if diff_tabs.get(key) is not info:
                return
            _clear_tab_busy(info)
            colors = current_colors()
            info["verdict"] = f"<span style='color:{colors['red']}'>●</span> Compare failed"
            info["detail"] = str(exc)
            if _current_diff_tab() is info:
                _apply_tab_summary(info)

        worker.signals.finished.connect(tab_done)
        worker.signals.error.connect(tab_failed)
        QThreadPool.globalInstance().start(worker)

    def _on_tab_changed(_index: int) -> None:
        """Keep the summary/hunk/status widgets in sync with the visible tab."""
        info = _current_diff_tab()
        if info is not None:
            _apply_tab_summary(info)
        else:
            # Back on the main compare: restore its own verdict/detail and
            # re-target the hunk buttons at the main stack's result.
            verdict_label.setText(main_summary["verdict"])
            if main_summary["detail"]:
                show_detail(main_summary["detail"])
            else:
                detail_label.hide()
            prev_hunk_button.setEnabled(bool(hunk_starts) or bool(inline_hunk_starts))
            next_hunk_button.setEnabled(bool(hunk_starts) or bool(inline_hunk_starts))
        _update_status_labels()
        _update_zoom_label()

    def close_diff_tab(index: int) -> None:
        """Close a file-diff tab (index 0 — the compare stack — never closes)."""
        if index == 0:
            return
        widget = tabs.widget(index)
        key = _tab_key_of.pop(widget, None)
        if key:
            diff_tabs.pop(key, None)
        tabs.removeTab(index)
        widget.deleteLater()
        _sync_tab_bar()
        _on_tab_changed(tabs.currentIndex())

    def run_selected_diff() -> None:
        selected = selected_folder_entries()
        if not selected:
            show_message("Select a file row in the table to diff it.")
            return
        open_entry_diff(selected[0])

    def on_folder_item_activated(item, _column) -> None:
        entry = entry_from_item(item)
        if entry is not None:
            open_entry_diff(entry)

    def run_sync(operation: str) -> None:
        if not folder_roots["left"] or not folder_roots["right"]:
            show_message("Compare two folders first, then select rows to synchronize.")
            return
        selected = selected_folder_entries()
        if not selected:
            show_message("Select rows in the table to synchronize.")
            return

        overwrite = sum(1 for entry in selected if entry.state == "modified")
        if operation == "delete":
            both_sides = sum(1 for entry in selected if entry.state == "modified")
            message = (
                f"Delete {len(selected)} file(s)?\n\n"
                f"This removes files from disk — {both_sides} modified file(s) are "
                "removed from *both* folders. This cannot be undone."
            )
        else:
            direction = "right side" if operation == "copy_left_to_right" else "left side"
            message = f"Copy {len(selected)} file(s) to the {direction}?"
            if overwrite:
                message += f"\n\n{overwrite} modified file(s) will be overwritten."
        answer = QMessageBox.question(
            root, "Synchronize folders", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        set_busy(True)
        show_message("Synchronizing…")
        worker = FolderSyncWorker(
            entries=list(selected),
            operation=operation,
            left_root=folder_roots["left"],
            right_root=folder_roots["right"],
        )
        pending_workers.append(worker)

        def sync_done(report, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            set_busy(False)
            summary = f"{report['copied']} copied · {report['deleted']} deleted"
            if report["overwritten"]:
                summary += f" · {report['overwritten']} overwritten"
            if report["failed"]:
                summary += f" · {len(report['failed'])} failed"
            show_detail(summary)
            compare()  # re-compare automatically after syncing

        def sync_failed(exc, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            set_busy(False)
            show_message(f"Sync failed: {exc}")

        worker.signals.finished.connect(sync_done)
        worker.signals.error.connect(sync_failed)
        QThreadPool.globalInstance().start(worker)

    def refresh_folders() -> None:
        if folder_roots["left"]:
            left_edit.setText(folder_roots["left"])
            right_edit.setText(folder_roots["right"])
        compare()

    def compare() -> None:
        nonlocal mode
        if compare_button.text() == "Comparing…":
            return  # already in flight
        # Header Compare always renders into the main tab, so bring it forward.
        tabs.setCurrentIndex(0)
        left = left_edit.text().strip()
        right = right_edit.text().strip()
        if not left or not right:
            show_message("Choose both a left and a right path to compare.")
            return
        mode = str(mode_combo.currentData())
        options = build_options()
        set_busy(True)
        show_message("Comparing…")
        worker = CompareWorker(left=left, right=right, mode=mode, options=options)
        pending_workers.append(worker)

        def done(result, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            set_busy(False)
            set_dirty(False)
            if mode == "folders":
                render_folder(result)
            else:
                render_diff(result)
            if find_bar.isVisible():
                run_search()

        def failed(exc, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            set_busy(False)
            show_message(f"Compare failed: {exc}")

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def swap() -> None:
        current_left = left_edit.text()
        left_edit.setText(right_edit.text())
        right_edit.setText(current_left)

    def on_mode_changed() -> None:
        is_folder = mode_combo.currentData() == "folders"
        tabs.setTabText(0, "Folders" if is_folder else "Files")
        stack.setCurrentIndex(1 if is_folder else (2 if view_combo.currentData() == "inline" else 0))
        left_edit.setPlaceholderText("Choose the left folder…" if is_folder else "Choose the left file…")
        right_edit.setPlaceholderText("Choose the right folder…" if is_folder else "Choose the right file…")
        # Header interactions always act on the main tab, never on a diff tab.
        tabs.setCurrentIndex(0)
        if find_bar.isVisible():
            run_search()
        _update_status_labels()

    def on_view_changed() -> None:
        on_mode_changed()

    def next_hunk(forward: bool) -> None:
        """Jump to the previous/next difference in the visible tab's panes."""
        info = _current_diff_tab()
        if info is not None:
            starts = info["hunks"]
            panes = info["panes"]
            if not starts:
                return
            center = _visible_center(panes[0])
            if forward:
                target = next((line for line in starts if line > center), starts[-1])
            else:
                target = next((line for line in reversed(starts) if line < center), starts[0])
            panes[0].goto_line(target)
            panes[1].goto_line(target)
            return
        if view_combo.currentData() == "inline":
            starts = inline_hunk_starts
            pane = inline_view
        else:
            starts = hunk_starts
            pane = left_view
        if not starts:
            return
        center = _visible_center(pane)
        if forward:
            target = next((line for line in starts if line > center), starts[-1])
        else:
            target = next((line for line in reversed(starts) if line < center), starts[0])
        pane.goto_line(target)
        if view_combo.currentData() != "inline":
            right_view.goto_line(target)

    compare_button.clicked.connect(compare)
    swap_button.clicked.connect(swap)
    mode_combo.currentIndexChanged.connect(on_mode_changed)
    view_combo.currentIndexChanged.connect(on_view_changed)
    prev_hunk_button.clicked.connect(lambda: next_hunk(False))
    next_hunk_button.clicked.connect(lambda: next_hunk(True))
    copy_left_to_right.clicked.connect(lambda: run_sync("copy_left_to_right"))
    copy_right_to_left.clicked.connect(lambda: run_sync("copy_right_to_left"))
    delete_selected.clicked.connect(lambda: run_sync("delete"))
    diff_selected.clicked.connect(run_selected_diff)
    refresh_folder.clicked.connect(refresh_folders)
    # Enter / double-click on a folder file row opens its diff in a tab.
    folder_tree.itemActivated.connect(on_folder_item_activated)
    # Tree controls: expand/collapse everything, and the Hide-identical
    # filter (view-only, re-applied after every render).
    collapse_all.clicked.connect(folder_tree.collapseAll)
    expand_all.clicked.connect(folder_tree.expandAll)
    hide_identical.toggled.connect(lambda _checked: apply_tree_filter())

    # File-diff tab lifecycle: switching tabs re-targets the summary/hunk
    # widgets; the tab bar only appears once a diff tab exists.
    tabs.currentChanged.connect(_on_tab_changed)
    tabs.tabCloseRequested.connect(close_diff_tab)

    def _sync_tab_bar() -> None:
        tabs.tabBar().setVisible(tabs.count() > 1)

    _sync_tab_bar()

    # ---- search wiring ----------------------------------------------------------
    # Live search as you type (debounced so huge files only re-scan when the
    # user pauses).
    search_timer = QTimer(root)
    search_timer.setSingleShot(True)
    search_timer.setInterval(150)
    search_timer.timeout.connect(run_search)
    find_query.textChanged.connect(lambda _text: search_timer.start())
    find_query.returnPressed.connect(lambda: goto_match(True))
    case_button.clicked.connect(run_search)
    find_next.clicked.connect(lambda: goto_match(True))
    find_prev.clicked.connect(lambda: goto_match(False))
    close_find.clicked.connect(hide_find)
    replace_button.clicked.connect(replace_left)
    replace_all_button.clicked.connect(replace_all_left)
    save_left.clicked.connect(save_left_file)
    revert_left.clicked.connect(revert_left_file)

    # ---- status wiring -------------------------------------------------------------
    for pane in (left_view, right_view, inline_view):
        pane.foldStateChanged.connect(lambda _folded, _hidden: _update_status_labels())
        pane.zoomChanged.connect(lambda _point: _update_zoom_label())
        pane.scrollChanged.connect(lambda _top: _update_status_labels())

    def zoom_panes(action) -> None:
        for pane in active_panes():
            action(pane)
        _update_zoom_label()

    # ---- keyboard shortcuts ----------------------------------------------------------
    def toggle_fold_at_top() -> None:
        pane = active_panes()[0]
        top = pane.status_info().get("scroll_top", 0)
        pane.toggle_fold_at(top)

    QShortcut(QKeySequence(QKeySequence.StandardKey.Find), root).activated.connect(show_find)
    QShortcut(QKeySequence(QKeySequence.StandardKey.FindNext), root).activated.connect(lambda: goto_match(True))
    QShortcut(QKeySequence(QKeySequence.StandardKey.FindPrevious), root).activated.connect(lambda: goto_match(False))
    QShortcut(QKeySequence("Esc"), root).activated.connect(hide_find)
    QShortcut(QKeySequence("Ctrl+]"), root).activated.connect(lambda: next_hunk(True))
    QShortcut(QKeySequence("Ctrl+["), root).activated.connect(lambda: next_hunk(False))
    QShortcut(QKeySequence("Ctrl+="), root).activated.connect(lambda: zoom_panes(lambda p: p.zoom_in()))
    QShortcut(QKeySequence("Ctrl+-"), root).activated.connect(lambda: zoom_panes(lambda p: p.zoom_out()))
    QShortcut(QKeySequence("Ctrl+0"), root).activated.connect(lambda: zoom_panes(lambda p: p.zoom_reset()))
    QShortcut(QKeySequence("Ctrl+Shift+F"), root).activated.connect(toggle_fold_at_top)
    QShortcut(QKeySequence("Ctrl+Shift+U"), root).activated.connect(lambda: zoom_panes(lambda p: p.unfold_all()))

    # ---- theme refresh --------------------------------------------------------------
    if events is not None:

        def on_settings(**payload) -> None:
            key = payload.get("key")
            if key is None or key == "appearance.theme":
                for pane in (left_view, right_view, inline_view):
                    pane.refresh()
                for info in diff_tabs.values():
                    for pane in info["panes"]:
                        pane.refresh()

        events.subscribe("settings.changed", on_settings)
        events.subscribe("settings.applied", on_settings)

    # ---- demo: compare the built-in sample pair so the panes never start empty ---
    demo_options = build_options()
    demo_worker = CompareWorker(
        left="\n".join(DIFF_LEFT),
        right="\n".join(DIFF_RIGHT),
        mode="texts",
        options=demo_options,
        kind="text",
    )
    pending_workers.append(demo_worker)
    set_busy(True)

    def demo_done(result, current=demo_worker) -> None:
        if current in pending_workers:
            pending_workers.remove(current)
        set_busy(False)
        left_edit.setText("Sample file A (demo)")
        right_edit.setText("Sample file B (demo)")
        render_diff(result)

    def demo_failed(exc, current=demo_worker) -> None:
        if current in pending_workers:
            pending_workers.remove(current)
        set_busy(False)

    demo_worker.signals.finished.connect(demo_done)
    demo_worker.signals.error.connect(demo_failed)
    QThreadPool.globalInstance().start(demo_worker)

    on_mode_changed()
    return root


# ---------------------------------------------------------------------------
# Module descriptor
# ---------------------------------------------------------------------------


_STATE_DISPLAY = {
    "identical": ("identical", "green"),
    "modified": ("modified", "amber"),
    "only_left": ("deleted", "red"),
    "only_right": ("added", "green"),
    "moved": ("moved", "cyan"),
    "renamed": ("renamed", "cyan"),
}

# Extra item-data role: True when a row shows no difference (identical file,
# or a directory whose whole subtree matches) — drives the Hide-identical filter.
_PURE_ROLE = Qt.ItemDataRole.UserRole + 1


def _fmt_size(size: int) -> str:
    if size < 0:
        return "—"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _fmt_time(timestamp: float) -> str:
    if timestamp <= 0:
        return "—"
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _folder_path_text(entry) -> str:
    if entry.state in ("moved", "renamed") and entry.pair:
        return f"{entry.relative}  →  {entry.pair}"
    return entry.relative


def _folder_tree_path(entry) -> str:
    """Tree-mode path text: just the basename (the directory chain is the tree)."""
    left = entry.relative.split("/")[-1]
    if entry.state in ("moved", "renamed") and entry.pair:
        return f"{left}  →  {entry.pair.split('/')[-1]}"
    return left


def _state_tooltip(entry) -> str:
    if entry.state in ("moved", "renamed"):
        return f"Same content: '{entry.relative}' on the left, '{entry.pair}' on the right"
    if entry.state == "only_left":
        return "Present only in the left folder"
    if entry.state == "only_right":
        return "Present only in the right folder"
    if entry.state == "modified" and entry.time_differs:
        return "Content differs"
    if entry.state == "identical" and entry.time_differs:
        return "Same content, different modification time"
    return entry.state


# Short state tokens for directory aggregate rows ("2 mod · 1 add").
_DIR_TOKEN = (
    ("modified", "amber", "mod"),
    ("only_left", "red", "del"),
    ("only_right", "green", "add"),
    ("moved", "cyan", "moved"),
    ("renamed", "cyan", "renamed"),
)


def _dir_aggregate(counter: dict[str, int]) -> tuple[str, str]:
    """(summary label, color token) for a directory's subtree counts."""
    parts = [f"{counter[key]} {token}" for key, _color, token in _DIR_TOKEN if counter.get(key)]
    if not parts:
        return "", "text3"
    if counter.get("modified"):
        color = "amber"
    elif counter.get("only_left"):
        color = "red"
    elif counter.get("moved") or counter.get("renamed"):
        color = "cyan"
    else:
        color = "green"
    return " · ".join(parts), color


def _dir_aggregate_tooltip(path: str, counter: dict[str, int]) -> str:
    """Full breakdown for a directory row's tooltip."""
    parts = [f"{counter[key]} {label}" for key, label in (
        ("modified", "modified"),
        ("only_left", "deleted"),
        ("only_right", "added"),
        ("moved", "moved"),
        ("renamed", "renamed"),
    ) if counter.get(key)]
    return f"{path}/ — " + (" · ".join(parts) if parts else "no differences")


def _parse_ignore_dirs(value) -> tuple[str, ...]:
    """Parse a comma-separated ignore list (empty entries dropped)."""
    if not value:
        return ()
    return tuple(part.strip() for part in str(value).split(",") if part.strip())


def _visible_center(view: CodeView) -> int:
    """The logical line currently centered in ``view`` (for hunk jumping)."""
    bar = view.verticalScrollBar()
    visual = (bar.value() + view.viewport().height() // 2) // max(1, view._line_height())
    return view._fold.logical_of(visual)


def _stats_html(result, colors: dict) -> str:
    """HTML summary of a DiffResult's stats (shared by main + per-tab renders)."""
    stats = result.stats
    parts = [
        f"<span style='color:{colors['green']}'>+{stats.added}</span>",
        f"<span style='color:{colors['red']}'>−{stats.removed}</span>",
    ]
    if stats.changed:
        parts.append(f"<span style='color:{colors['amber']}'>~{stats.changed} changed</span>")
    if stats.hunks:
        parts.append(f"{stats.hunks} hunk{'s' if stats.hunks != 1 else ''}")
    if result.encoding and result.kind != "text":
        parts.append(result.encoding)
    if result.truncated:
        parts.append("<span style='color:#e06c6c'>truncated</span>")
    return " · ".join(parts)


def _inline_change_starts(inline_lines: list[tuple[str, str]]) -> list[int]:
    """Start indices of each changed group in an inline (unified) rendering."""
    starts: list[int] = []
    previous_changed = False
    for index, (_text, state) in enumerate(inline_lines):
        changed = state in ("added", "removed")
        if changed and not previous_changed:
            starts.append(index)
        previous_changed = changed
    return starts


compare_module = Module(
    id="compare",
    title="Compare",
    icon="compare",
    build=build_view,
    navigator=(
        ("Recent comparisons", ("theme.py ↔ theme.py.bak", "settings.py ↔ settings.py.orig", "README.md ↔ README.md.bak")),
        ("Saved sessions", ("sync-configs", "audit-deps")),
    ),
    details=(
        ("Engine", "Auto · Myers O(ND)"),
        ("Modes", "Side-by-side · inline · folder"),
        ("Rules", "From Settings → Compare"),
        ("Viewer", "Syntax · folds · search · minimap"),
    ),
    status="Compare · select two files or folders to diff",
)
