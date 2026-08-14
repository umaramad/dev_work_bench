"""Git screen — split group/repo landing + per-folder repository tabs.

Tab 0 ("Folders") is the landing page: left group list, right repos for the
selected group (Flet-parity), with bulk Fetch/Status/Reset and a collapsible
console. Opening a folder still creates its **own tab inside the Git screen**
so several repositories can stay open side by side. Each tab offers Fetch,
Pull (rebase), Status, Recent commits, and Fetch-all. All git work happens on
worker threads; the UI never blocks, and closing a tab cancels its in-flight
workers.
"""

from __future__ import annotations

import json
import logging
import os

from PySide6.QtCore import QSize, QThreadPool, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devworkbench.models.persistence import Favorite
from devworkbench.modules.base import Module
from devworkbench.modules.git.dialog import GroupManagerDialog, RepoDialog, ScanReposDialog
from devworkbench.ui.samples import GIT_REPOS
from devworkbench.ui.theme import current_colors
from devworkbench.ui.widgets.common import button, clear_list_widget, icon_button, search_field, styled_label
from devworkbench.workers.git_worker import GitWorker

logger = logging.getLogger("devworkbench.modules.git")


def build_view(icons, ctx=None) -> QWidget:
    service = ctx.resolve("services.configuration") if ctx is not None and ctx.has("services.configuration") else None
    favorites_repo = ctx.resolve("database.repositories.favorites") if ctx is not None and ctx.has("database.repositories.favorites") else None
    history_repo = ctx.resolve("database.repositories.history") if ctx is not None and ctx.has("database.repositories.history") else None
    colors = current_colors()

    def git_exe() -> str:
        if service is None:
            return "git"
        try:
            return str(service.get("git.executable") or "git")
        except Exception:  # noqa: BLE001 — never block the UI on a bad setting
            return "git"

    root = QWidget()
    layout = QVBoxLayout(root)
    layout.setContentsMargins(10, 10, 10, 8)
    layout.setSpacing(8)

    # ================================================================ landing
    landing = QWidget()
    landing_layout = QVBoxLayout(landing)
    landing_layout.setContentsMargins(4, 4, 4, 4)
    landing_layout.setSpacing(10)

    heading = QWidget()
    heading_layout = QHBoxLayout(heading)
    heading_layout.setContentsMargins(0, 0, 0, 0)
    heading_layout.setSpacing(8)
    title = QLabel("Git")
    title.setObjectName("sectionTitle")
    heading_layout.addWidget(title)
    subtitle = styled_label("Groups on the left · repos on the right · Open opens a tab", "hint")
    heading_layout.addWidget(subtitle)
    heading_layout.addStretch(1)
    open_button = button("Open folder…", "ghost")
    open_button.setObjectName("openFolderButton")
    open_button.setToolTip("Pick a folder and open it — it is added to your favorites automatically")
    heading_layout.addWidget(open_button)
    scan_button = button("Scan for repositories…", "ghost")
    scan_button.setObjectName("scanForReposButton")
    scan_button.setToolTip("Scan a folder's subdirectories for git repositories, then add the ones you want")
    heading_layout.addWidget(scan_button)
    add_button = button("Add repository…", "primary")
    add_button.setObjectName("addRepositoryButton")
    add_button.setToolTip("Add a repository with a name and an optional group")
    heading_layout.addWidget(add_button)
    landing_layout.addWidget(heading)

    # Toolbar: search + manage groups + refresh (group selection is the left list).
    toolbar = QWidget()
    toolbar_layout = QHBoxLayout(toolbar)
    toolbar_layout.setContentsMargins(0, 0, 0, 0)
    toolbar_layout.setSpacing(6)
    search_edit = search_field("Filter repositories by name or path…")
    search_edit.setObjectName("repoSearch")
    search_edit.setMinimumWidth(220)
    toolbar_layout.addWidget(search_edit, 1)
    manage_button = button("Manage groups", "ghost")
    manage_button.setObjectName("manageGroupsButton")
    manage_button.setToolTip("Rename, merge or delete repository groups")
    toolbar_layout.addWidget(manage_button)
    refresh_button = icon_button(icons, "refresh", "Refresh repository list")
    refresh_button.setObjectName("refreshReposButton")
    toolbar_layout.addWidget(refresh_button)
    landing_layout.addWidget(toolbar)

    # Split landing: left groups, right repos for the selected group.
    split = QSplitter(Qt.Orientation.Horizontal)
    split.setObjectName("gitLandingSplit")
    split.setChildrenCollapsible(False)

    groups_pane = QWidget()
    groups_pane.setObjectName("groupsPane")
    groups_pane_layout = QVBoxLayout(groups_pane)
    groups_pane_layout.setContentsMargins(0, 0, 0, 0)
    groups_pane_layout.setSpacing(6)
    groups_pane_layout.addWidget(styled_label("Groups", "hint"))
    groups_list = QListWidget()
    groups_list.setObjectName("groupList")
    groups_list.setFrameStyle(0)
    groups_list.setSpacing(4)
    groups_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
    groups_pane_layout.addWidget(groups_list, 1)
    groups_empty = styled_label(
        "No repositories yet — click “Add repository…” or “Scan for repositories…” to get started.",
        "hint",
    )
    groups_empty.setWordWrap(True)
    groups_pane_layout.addWidget(groups_empty)
    split.addWidget(groups_pane)

    repos_pane = QWidget()
    repos_pane.setObjectName("reposPane")
    repos_pane_layout = QVBoxLayout(repos_pane)
    repos_pane_layout.setContentsMargins(0, 0, 0, 0)
    repos_pane_layout.setSpacing(8)
    repos_title = styled_label("", "muted")
    repos_title.setObjectName("groupTitle")
    repos_title_font = repos_title.font()
    repos_title_font.setBold(True)
    repos_title.setFont(repos_title_font)
    repos_pane_layout.addWidget(repos_title)

    bulk_bar = QWidget()
    bulk_bar.setObjectName("bulkBar")
    bulk_layout = QHBoxLayout(bulk_bar)
    bulk_layout.setContentsMargins(0, 0, 0, 0)
    bulk_layout.setSpacing(6)
    bulk_fetch = button("Fetch all", "ghost")
    bulk_fetch.setObjectName("bulkFetchButton")
    bulk_fetch.setToolTip("Fetch every repository in this group")
    bulk_status = button("Status all", "ghost")
    bulk_status.setObjectName("bulkStatusButton")
    bulk_status.setToolTip("Run git status on every repository in this group")
    bulk_reset = button("Reset all", "ghost")
    bulk_reset.setObjectName("bulkResetButton")
    bulk_reset.setToolTip("Run soft git reset on every repository in this group")
    bulk_busy_label = styled_label("", "hint")
    bulk_busy_label.setObjectName("bulkBusyLabel")
    bulk_busy_label.hide()
    bulk_layout.addWidget(bulk_fetch)
    bulk_layout.addWidget(bulk_status)
    bulk_layout.addWidget(bulk_reset)
    bulk_layout.addWidget(bulk_busy_label)
    bulk_layout.addStretch(1)
    repos_pane_layout.addWidget(bulk_bar)

    favorites_list = QListWidget()
    favorites_list.setObjectName("favoritesList")
    favorites_list.setFrameStyle(0)
    favorites_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    repos_pane_layout.addWidget(favorites_list, 1)

    empty_state = styled_label(
        "Select a group on the left to see its repositories.",
        "hint",
    )
    empty_state.setWordWrap(True)
    repos_pane_layout.addWidget(empty_state)
    split.addWidget(repos_pane)
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setSizes([240, 720])
    landing_layout.addWidget(split, 1)

    # Collapsible console at the bottom of the landing (not inside repo tabs).
    console_wrap = QWidget()
    console_wrap.setObjectName("gitConsole")
    console_wrap_layout = QVBoxLayout(console_wrap)
    console_wrap_layout.setContentsMargins(0, 4, 0, 0)
    console_wrap_layout.setSpacing(4)
    console_header = QWidget()
    console_header.setObjectName("consoleHeader")
    console_header_layout = QHBoxLayout(console_header)
    console_header_layout.setContentsMargins(4, 2, 4, 2)
    console_header_layout.setSpacing(8)
    console_toggle = QPushButton("▸  Console")
    console_toggle.setObjectName("consoleToggle")
    console_toggle.setFlat(True)
    console_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
    console_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    console_header_layout.addWidget(console_toggle)
    console_status = styled_label("idle", "hint")
    console_status.setObjectName("consoleStatus")
    console_header_layout.addWidget(console_status, 1)
    console_clear = button("Clear", "ghost")
    console_clear.setObjectName("consoleClear")
    console_clear.hide()
    console_header_layout.addWidget(console_clear)
    console_wrap_layout.addWidget(console_header)
    console_log = QPlainTextEdit()
    console_log.setObjectName("consoleLog")
    console_log.setReadOnly(True)
    console_log.setMaximumBlockCount(500)
    console_log.setFont(QFont("Menlo", 11))
    console_log.setFixedHeight(200)
    console_log.hide()
    console_wrap_layout.addWidget(console_log)
    landing_layout.addWidget(console_wrap)

    # ================================================================= tabs
    # Tab 0 is the landing page (never closes); every opened folder becomes
    # a repository tab. Re-opening a path activates its existing tab.
    tabs = QTabWidget()
    tabs.setObjectName("gitTabs")
    tabs.setDocumentMode(True)
    tabs.setTabsClosable(True)
    tabs.addTab(landing, "Folders")
    # The landing tab has no close button — there must always be a way back.
    tabs.tabBar().setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
    layout.addWidget(tabs, 1)

    # ------------------------------------------------------------ landing state
    # Workers for the one-click card / bulk actions (retained until their
    # signals deliver — see the Worker retention contract).
    home_workers: list = []
    # Selected group key ("" for Ungrouped, "__demo__" in demo mode).
    landing_state = {
        "group": None,
        "console_open": False,
        "bulk_busy": False,
        "refreshing": False,
    }
    # Last known remote status per repo path, rendered onto fresh cards after
    # a rebuild; refreshed on demand, after each fetch and on a timer.
    status_cache: dict[str, dict] = {}
    status_workers: list = []
    bulk_workers: list = []
    bulk_queue: list = []  # remaining paths for the active bulk run
    bulk_meta = {"op": "", "done": 0, "total": 0, "ok": 0}

    def card_widget(path: str, label: str, demo: bool) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 6, 8, 6)
        row_layout.setSpacing(10)
        labels = QVBoxLayout()
        labels.setSpacing(1)
        name = QLabel(label or os.path.basename(path.rstrip("/")) or path)
        labels.addWidget(name)
        path_text = QLabel(path)
        path_text.setObjectName("hint")
        path_text.setWordWrap(True)
        labels.addWidget(path_text)
        # Per-card fetch result line (hidden until a fetch runs).
        status_label = styled_label("", "hint")
        status_label.setProperty("role", "cardStatus")
        status_label.setWordWrap(True)
        status_label.hide()
        labels.addWidget(status_label)
        if not demo:
            # Remote status line: "main · ↑1 ↓2" etc., filled in by an
            # async remote_status worker (refresh button, post-fetch, timer).
            remote_status_label = styled_label("", "hint")
            remote_status_label.setObjectName("cardRemoteStatus")
            remote_status_label.setProperty("role", "cardRemoteStatus")
            remote_status_label.hide()
            labels.addWidget(remote_status_label)
        row_layout.addLayout(labels, 1)
        if demo:
            pill = QLabel("demo")
            pill.setObjectName("statusPill")
            pill.setProperty("state", "warn")
            row_layout.addWidget(pill)
        open_btn = button("Open", "ghost")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda _checked=False, p=path: open_folder(p, demo=demo))
        row_layout.addWidget(open_btn)
        if not demo:
            fetch_btn = icon_button(icons, "download", "Fetch this repository")
            fetch_btn.setObjectName("cardFetchButton")
            fetch_btn.clicked.connect(
                lambda _checked=False, p=path: fetch_repo(p, fetch_btn, status_label)
            )
            row_layout.addWidget(fetch_btn)
            status_refresh = icon_button(icons, "refresh", "Refresh remote status")
            status_refresh.setObjectName("cardStatusRefresh")
            status_refresh.clicked.connect(
                lambda _checked=False, p=path: refresh_card_status(p)
            )
            row_layout.addWidget(status_refresh)
            edit_btn = icon_button(icons, "edit", "Edit repository")
            edit_btn.setObjectName("editRepoButton")
            edit_btn.clicked.connect(lambda _checked=False, p=path: edit_repository(p))
            row_layout.addWidget(edit_btn)
            unpin = icon_button(icons, "close", "Remove from favorites")
            unpin.setObjectName("unpinButton")
            unpin.clicked.connect(lambda _checked=False, p=path: remove_favorite(p))
            row_layout.addWidget(unpin)
        return row

    def add_card(path: str, label: str, demo: bool) -> None:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, path)
        widget = card_widget(path, label, demo)
        item.setSizeHint(widget.sizeHint())
        # The item must be in the list *before* setItemWidget — otherwise the
        # widget is never attached to the view and the card never renders.
        favorites_list.addItem(item)
        favorites_list.setItemWidget(item, widget)

    def add_group_row(key: str, name: str, count: int) -> None:
        """One selectable row in the left group list (name + repo count)."""
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, key)
        noun = "repo" if count == 1 else "repos"
        # Use a button so clicks land on the control (QListWidget item widgets
        # otherwise swallow mouse events and never select / never fire itemClicked).
        row = QPushButton()
        row.setObjectName("groupRow")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.setCheckable(True)
        row.setChecked(key == landing_state["group"])
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 6, 8, 6)
        row_layout.setSpacing(8)
        icon_label = QLabel()
        icon_label.setPixmap(icons.get("folder", 18).pixmap(18, 18))
        icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row_layout.addWidget(icon_label)
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        name_label = QLabel(name)
        name_font = name_label.font()
        name_font.setBold(True)
        name_label.setFont(name_font)
        name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_col.addWidget(name_label)
        count_label = styled_label(f"{count} {noun}", "hint")
        count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_col.addWidget(count_label)
        row_layout.addLayout(text_col, 1)
        item.setSizeHint(QSize(200, 48))
        groups_list.addItem(item)
        groups_list.setItemWidget(item, row)
        row.clicked.connect(lambda _checked=False, k=key: open_group(k))

    def known_groups(favorites: list) -> tuple[str, ...]:
        """Distinct non-empty group names, A–Z."""
        groups = {(favorite.group_name or "").strip() for favorite in favorites}
        groups.discard("")
        return tuple(sorted(groups, key=str.casefold))

    def section_counts() -> dict[str, int]:
        if favorites_repo is None:
            return {"__demo__": len(GIT_REPOS)}
        counts: dict[str, int] = {}
        for favorite in favorites_repo.by_kind("folder"):
            key = (favorite.group_name or "").strip()
            counts[key] = counts.get(key, 0) + 1
        return counts

    def ensure_group_selection() -> None:
        counts = section_counts()
        if not counts:
            landing_state["group"] = None
            return
        if landing_state["group"] is not None and landing_state["group"] in counts:
            return
        ordered = sorted(counts, key=lambda g: (g == "", g.casefold()))
        landing_state["group"] = ordered[0]

    def _set_bulk_enabled(enabled: bool) -> None:
        bulk_fetch.setEnabled(enabled)
        bulk_status.setEnabled(enabled)
        bulk_reset.setEnabled(enabled)
        if landing_state["bulk_busy"]:
            bulk_busy_label.setText("Running…")
            bulk_busy_label.show()
        else:
            bulk_busy_label.hide()

    def refresh_groups() -> None:
        """Rebuild the left group list and keep the current selection."""
        ensure_group_selection()
        selected = landing_state["group"]
        # Block selection signals while tearing down item widgets — otherwise
        # clear() re-enters refresh via currentItemChanged and double-frees.
        groups_list.blockSignals(True)
        try:
            clear_list_widget(groups_list)

            if favorites_repo is None:
                add_group_row("__demo__", "Demo folders", len(GIT_REPOS))
                groups_empty.hide()
                groups_list.show()
                manage_button.setEnabled(False)
                _select_group_item("__demo__")
                return

            favorites = favorites_repo.by_kind("folder")
            manage_button.setEnabled(bool(known_groups(favorites)))
            counts = section_counts()
            if not counts:
                groups_empty.setText(
                    "No repositories yet — click “Add repository…” or “Scan for "
                    "repositories…” to get started."
                )
                groups_empty.show()
                groups_list.hide()
                return
            groups_list.show()
            groups_empty.hide()
            for group in sorted(counts, key=lambda g: (g == "", g.casefold())):
                add_group_row(group, group or "Ungrouped", counts[group])
            _select_group_item(selected if selected in counts else landing_state["group"])
        finally:
            groups_list.blockSignals(False)

    def _select_group_item(key: str | None) -> None:
        if key is None:
            return
        groups_list.blockSignals(True)
        for i in range(groups_list.count()):
            item = groups_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                groups_list.setCurrentItem(item)
                break
        groups_list.blockSignals(False)

    def favorites_for_group() -> list:
        group = landing_state["group"]
        if favorites_repo is None or group is None or group == "__demo__":
            return []
        return [
            favorite
            for favorite in favorites_repo.by_kind("folder")
            if (favorite.group_name or "").strip() == group
        ]

    def refresh_favorites() -> None:
        """Rebuild both panes: group list + cards for the selected group."""
        if landing_state.get("refreshing"):
            return
        landing_state["refreshing"] = True
        try:
            ensure_group_selection()
            refresh_groups()
            group = landing_state["group"]
            clear_list_widget(favorites_list)

            if group is None:
                repos_title.setText("Select a group")
                empty_state.setText("Select a group on the left to see its repositories.")
                empty_state.show()
                _set_bulk_enabled(False)
                return

            repos_title.setText(
                "Demo folders" if group == "__demo__" else (group or "Ungrouped")
            )
            search = search_edit.text().strip().lower()

            if group == "__demo__" or favorites_repo is None:
                for name in GIT_REPOS:
                    add_card(name, name, demo=True)
                empty_state.hide()
                _set_bulk_enabled(False)
                _render_card_statuses()
                return

            members = favorites_for_group()
            if search:
                members = [
                    favorite
                    for favorite in members
                    if search in " ".join((favorite.label, favorite.ref, favorite.group_name or "")).lower()
                ]
            members.sort(key=lambda f: (f.label or f.ref).casefold())
            if not members:
                empty_state.setText(
                    "No repositories match your search." if search else "This group is empty."
                )
                empty_state.show()
                _set_bulk_enabled(False)
                return
            empty_state.hide()
            for favorite in members:
                add_card(favorite.ref, favorite.label, demo=False)
            _set_bulk_enabled(not landing_state["bulk_busy"])
            _render_card_statuses()
        finally:
            landing_state["refreshing"] = False

    def open_group(key: str) -> None:
        """Select a group and refresh the right-hand repo list in place."""
        if key is None or landing_state.get("refreshing"):
            return
        if key == landing_state["group"] and favorites_list.count() > 0:
            # Already showing this group — still sync the checked row state.
            _select_group_item(key)
            return
        landing_state["group"] = key
        refresh_favorites()
        if landing_state["group"] not in (None, "__demo__") and not landing_state["bulk_busy"]:
            refresh_all_statuses()
        persist_timer.start()

    def add_repository() -> None:
        """Open the Add dialog; persist the returned favorite on accept."""
        if favorites_repo is None:
            return
        favorites = favorites_repo.by_kind("folder")
        dialog = RepoDialog(
            root,
            existing_groups=known_groups(favorites),
            existing_paths=tuple(favorite.ref for favorite in favorites),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            persist_new(dialog.record())

    def persist_new(favorite: Favorite) -> None:
        """Insert a new favorite (deduped) and record it in recent folders."""
        if favorites_repo is None:
            return
        if not favorites_repo.is_favorite("folder", favorite.ref):
            favorites_repo.insert(favorite)
        if history_repo is not None:
            history_repo.add_recent_folder(favorite.ref)
        refresh_favorites()

    def edit_repository(path: str) -> None:
        """Open the Edit dialog pre-filled for the favorite at ``path``."""
        if favorites_repo is None:
            return
        favorite = favorites_repo.find("folder", path)
        if favorite is None:
            return
        favorites = favorites_repo.by_kind("folder")
        dialog = RepoDialog(
            root,
            record=favorite,
            existing_groups=known_groups(favorites),
            existing_paths=tuple(f.ref for f in favorites if f.ref != path),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            persist_update(favorite, dialog.record())

    def persist_update(original: Favorite, updated: Favorite) -> None:
        """Apply the edited label/group/path to an existing favorite row."""
        if favorites_repo is None:
            return
        # A path change targets the UNIQUE(kind, ref) index — bail out if the
        # target is already favorited (stale dialog state; the UNIQUE index
        # would otherwise raise an IntegrityError deep in the UI layer).
        if updated.ref != original.ref and favorites_repo.is_favorite("folder", updated.ref):
            refresh_favorites()
            return
        original.label = updated.label
        original.group_name = updated.group_name
        if updated.ref != original.ref:
            original.ref = updated.ref
        favorites_repo.update(original)
        refresh_favorites()

    def manage_groups() -> None:
        """Open the Manage groups dialog; refresh the landing when it closes."""
        if favorites_repo is None:
            return
        dialog = GroupManagerDialog(root, favorites_repo)
        dialog.exec()
        refresh_favorites()

    def show_card_menu(position) -> None:
        """Right-click menu on a repository card: Open / Edit / Remove.

        Built with the non-blocking ``popup`` pattern — every action calls
        the same handler as the card's buttons, so the behavior is identical
        to the buttons while staying testable (no modal ``exec`` loop).
        """
        item = favorites_list.itemAt(position)
        if item is None or not item.flags():  # group headers are plain labels
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        menu = QMenu(favorites_list)
        menu.addAction("Open").triggered.connect(
            lambda _checked=False, p=path: open_folder(p)
        )
        favorite = favorites_repo.find("folder", path) if favorites_repo is not None else None
        if favorite is not None:
            menu.addSeparator()
            menu.addAction("Edit repository…").triggered.connect(
                lambda _checked=False, p=path: edit_repository(p)
            )
            menu.addAction("Remove from favorites").triggered.connect(
                lambda _checked=False, p=path: remove_favorite(p)
            )
        menu.popup(favorites_list.mapToGlobal(position))

    def remove_favorite(path: str) -> None:
        if favorites_repo is not None:
            favorites_repo.remove_ref("folder", path)
        status_cache.pop(path, None)  # don't keep a stale entry for unpinned repos
        refresh_favorites()

    def fetch_repo(path: str, fetch_btn, status_label) -> None:
        """Run ``git fetch`` for one repository straight from its card.

        No tab is opened: the card's button disables while the fetch runs and
        the outcome is reported on the card itself. Repeat clicks during a
        run are ignored (``_fetching`` flag).
        """
        if getattr(fetch_btn, "_fetching", False):
            return
        fetch_btn._fetching = True
        fetch_btn.setEnabled(False)
        fetch_btn.setToolTip("Fetching…")
        status_label.setText("Fetching…")
        status_label.show()
        console_append(f"$ git fetch — {path}")
        set_console_status("git fetch…")

        worker = GitWorker("fetch", path, executable=git_exe())
        home_workers.append(worker)

        def done(result, current=worker) -> None:
            if current in home_workers:
                home_workers.remove(current)
            ok = bool(result.get("ok"))
            output = str(result.get("output", ""))
            summary = (output.strip().splitlines() or ["ok" if ok else "failed"])[0]
            console_append(f"  {'✓' if ok else '✕'} {summary[:200]}", ok=ok)
            if not landing_state["bulk_busy"]:
                set_console_status("idle")
            _finish_fetch(
                fetch_btn, status_label, ok, output, path,
            )

        def failed(exc, current=worker) -> None:
            if current in home_workers:
                home_workers.remove(current)
            console_append(f"  ✕ {exc}", ok=False)
            if not landing_state["bulk_busy"]:
                set_console_status("idle")
            _finish_fetch(fetch_btn, status_label, False, str(exc), path)

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def _finish_fetch(fetch_btn, status_label, ok: bool, output: str, path: str) -> None:
        """Report the fetch result on the card, then auto-clear it.

        The landing list may be rebuilt while a fetch runs (e.g. a repo tab
        opens and auto-pins, refreshing the cards) — a RuntimeError means the
        old card is gone and there is nothing left to update.
        """
        try:
            fetch_btn._fetching = False
            fetch_btn.setEnabled(True)
            fetch_btn.setToolTip("Fetch this repository")
            trimmed = (output or "").strip()
            detail = trimmed.splitlines()[0] if trimmed else "Fetch complete"
            status_label.setText(f"{'✓' if ok else '✕'} {detail[:90]}")
            status_label.show()
            timer = QTimer(status_label)  # dies with the card
            timer.setSingleShot(True)
            timer.setInterval(6000)
            timer.timeout.connect(status_label.hide)
            timer.start()
        except RuntimeError:
            pass  # the card was rebuilt while the fetch ran
        # A fetch may have moved ahead/behind — refresh the card's remote
        # status (cache-only if the card was rebuilt in the meantime).
        refresh_card_status(path)

    # -------------------------------------------------------- console + bulk
    def set_console_status(text: str) -> None:
        console_status.setText(text)

    def console_append(line: str, ok: bool | None = None) -> None:
        colors = current_colors()
        if ok is True:
            color = colors.get("green", "#4cc38a")
        elif ok is False:
            color = colors.get("red", "#e06c6c")
        else:
            color = colors.get("text2", "#a4adbd")
        console_log.appendHtml(
            f'<span style="color:{color}">{_escape_html(line)}</span>'
        )
        console_log.moveCursor(QTextCursor.MoveOperation.End)

    def _escape_html(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def toggle_console() -> None:
        open_ = not landing_state["console_open"]
        landing_state["console_open"] = open_
        console_toggle.setText("▾  Console" if open_ else "▸  Console")
        console_log.setVisible(open_)
        console_clear.setVisible(open_)

    def clear_console() -> None:
        console_log.clear()
        if not landing_state["bulk_busy"]:
            set_console_status("idle")

    def _set_card_op_status(path: str, text: str) -> None:
        """Update the per-card status line if the card is still on screen."""
        try:
            for i in range(favorites_list.count()):
                item = favorites_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) != path:
                    continue
                widget = favorites_list.itemWidget(item)
                if widget is None:
                    return
                for label in widget.findChildren(QLabel):
                    if label.property("role") == "cardStatus":
                        label.setText(text)
                        label.show()
                        return
        except RuntimeError:
            pass

    def run_bulk(op: str) -> None:
        """Sequentially run ``op`` over every repo in the selected group."""
        if landing_state["bulk_busy"]:
            return
        group = landing_state["group"]
        if group in (None, "__demo__"):
            return
        members = favorites_for_group()
        if not members:
            return
        paths = [favorite.ref for favorite in members]
        landing_state["bulk_busy"] = True
        bulk_queue[:] = list(paths)
        bulk_meta.update({"op": op, "done": 0, "total": len(paths), "ok": 0})
        _set_bulk_enabled(False)
        label = {"fetch": "fetch", "status": "status", "reset": "reset"}.get(op, op)
        console_append(f"$ bulk {label} — {len(paths)} repos in {group or 'Ungrouped'}")
        set_console_status(f"{label} 0/{len(paths)}…")
        _run_next_bulk()

    def _run_next_bulk() -> None:
        if not bulk_queue:
            _finish_bulk()
            return
        op = bulk_meta["op"]
        path = bulk_queue.pop(0)
        bulk_meta["done"] += 1
        label = op
        set_console_status(f"{label} {bulk_meta['done']}/{bulk_meta['total']}…")
        console_append(f"$ git {op} — {path}")
        _set_card_op_status(path, f"Running git {op}…")

        worker = GitWorker(op, path, executable=git_exe())
        bulk_workers.append(worker)

        def done(result, current=worker, p=path, operation=op) -> None:
            if current in bulk_workers:
                bulk_workers.remove(current)
            ok = bool(result.get("ok")) if isinstance(result, dict) else False
            if ok:
                bulk_meta["ok"] += 1
            output = ""
            if isinstance(result, dict):
                output = str(result.get("output") or "").strip()
            summary = (output.splitlines() or ["ok" if ok else "failed"])[0]
            console_append(f"  {'✓' if ok else '✕'} {summary[:200]}", ok=ok)
            _set_card_op_status(p, f"{'✓' if ok else '✕'} git {operation}: {summary[:90]}")
            if operation in ("fetch", "status"):
                refresh_card_status(p)
            _run_next_bulk()

        def failed(exc, current=worker, p=path, operation=op) -> None:
            if current in bulk_workers:
                bulk_workers.remove(current)
            console_append(f"  ✕ {exc}", ok=False)
            _set_card_op_status(p, f"✕ git {operation}: {exc}")
            _run_next_bulk()

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def _finish_bulk() -> None:
        op = bulk_meta["op"]
        ok_count = bulk_meta["ok"]
        total = bulk_meta["total"]
        landing_state["bulk_busy"] = False
        console_append(
            f"  bulk {op} done — {ok_count}/{total} ok",
            ok=ok_count == total,
        )
        set_console_status("idle")
        _set_bulk_enabled(
            landing_state["group"] not in (None, "__demo__")
            and bool(favorites_for_group())
        )

    # -------------------------------------------------------- card status
    # Remote status (branch + ahead/behind) for every card, kept in a cache
    # keyed by path so a rebuilt landing can render it instantly, and
    # refreshed asynchronously via the ``remote_status`` git operation.

    def _format_remote_status(result: dict) -> str:
        """Render a status result as a compact card line."""
        branch = result.get("branch") or ""
        upstream = result.get("upstream")
        ahead = int(result.get("ahead") or 0)
        behind = int(result.get("behind") or 0)
        if not branch or branch == "HEAD (no branch)":
            branch = "detached"
        if not upstream:
            return f"{branch} · no upstream"
        if ahead and behind:
            return f"{branch} · ↑{ahead} ↓{behind}"
        if ahead:
            return f"{branch} · ↑{ahead}"
        if behind:
            return f"{branch} · ↓{behind}"
        return f"{branch} · up to date"

    def _apply_remote_status(label, result: dict) -> None:
        """Fill a card's remote label; plain folders stay empty and hidden."""
        try:
            if not result.get("is_repo"):
                label.setText("")
                label.hide()
                return
            text = _format_remote_status(result)
            ahead = int(result.get("ahead") or 0)
            behind = int(result.get("behind") or 0)
            state = (
                "err" if ahead and behind
                else "warn" if (ahead or behind) and result.get("upstream")
                else "ok" if result.get("upstream") and not ahead and not behind
                else ""
            )
            label.setText(text)
            label.setProperty("state", state)
            label.style().unpolish(label)
            label.style().polish(label)
            label.show()
        except RuntimeError:
            pass  # the card was rebuilt while the worker ran

    def _card_status_label(path: str):
        """The remote-status label of the live card for ``path`` (None if gone)."""
        try:
            for i in range(favorites_list.count()):
                item = favorites_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) != path:
                    continue
                widget = favorites_list.itemWidget(item)
                if widget is None:
                    return None
                return widget.findChild(QLabel, "cardRemoteStatus")
        except RuntimeError:
            return None
        return None

    def _render_card_statuses() -> None:
        """Push cached statuses onto the current cards (after a rebuild)."""
        try:
            for i in range(favorites_list.count()):
                item = favorites_list.item(i)
                path = item.data(Qt.ItemDataRole.UserRole)
                widget = favorites_list.itemWidget(item)
                if widget is None or not path:
                    continue
                result = status_cache.get(path)
                if result is None:
                    continue
                label = widget.findChild(QLabel, "cardRemoteStatus")
                if label is not None:
                    _apply_remote_status(label, result)
        except RuntimeError:
            pass

    def refresh_card_status(path: str) -> None:
        """Fetch branch + ahead/behind for one card in the background."""
        if favorites_repo is None:
            return
        worker = GitWorker("remote_status", path, executable=git_exe())
        status_workers.append(worker)

        def done(result, current=worker) -> None:
            if current in status_workers:
                status_workers.remove(current)
            status_cache[path] = result
            label = _card_status_label(path)
            if label is not None:
                _apply_remote_status(label, result)

        def failed(_exc, current=worker) -> None:
            if current in status_workers:
                status_workers.remove(current)
            status_cache[path] = {
                "is_repo": False, "branch": "", "ahead": 0, "behind": 0, "upstream": None,
            }
            label = _card_status_label(path)
            if label is not None:
                try:
                    label.setText("")
                    label.hide()
                except RuntimeError:
                    pass

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def refresh_all_statuses() -> None:
        """Kick off a status refresh for every non-demo card on the landing."""
        try:
            for i in range(favorites_list.count()):
                item = favorites_list.item(i)
                path = item.data(Qt.ItemDataRole.UserRole)
                widget = favorites_list.itemWidget(item)
                if widget is None or not path:
                    continue
                refresh_card_status(path)
        except RuntimeError:
            pass

    def pin_folder(path: str, label: str) -> None:
        if favorites_repo is None:
            return
        if not favorites_repo.is_favorite("folder", path):
            favorites_repo.insert(Favorite(kind="folder", ref=path, label=label))
        if history_repo is not None:
            history_repo.add_recent_folder(path)
        refresh_favorites()

    # ------------------------------------------------------------ repo tabs
    def open_folder(path: str, demo: bool = False) -> None:
        if not path:
            return
        path = os.path.abspath(path)
        # Re-opening a folder already on screen just activates its tab.
        for index in range(tabs.count()):
            if tabs.widget(index).property("repoPath") == path:
                tabs.setCurrentIndex(index)
                return
        create_repo_tab(path, demo=demo)

    def create_repo_tab(path: str, demo: bool = False) -> None:
        """Build a repository tab with its own widgets, state and workers."""
        page = QWidget()
        page.setProperty("repoPath", path)
        page.closed = False
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(8, 8, 8, 8)
        page_layout.setSpacing(8)

        # -- top bar -----------------------------------------------------------
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        back_button = icon_button(icons, "chevron_left", "Back to folders")
        back_button.setObjectName("backButton")
        top_layout.addWidget(back_button)
        path_label = styled_label(path, "muted")
        path_label.setWordWrap(True)
        top_layout.addWidget(path_label, 1)
        branch_pill = QLabel("—")
        branch_pill.setObjectName("statusPill")  # styled pill; doubles as branch indicator
        branch_pill.setProperty("state", "ok")
        top_layout.addWidget(branch_pill)
        page_layout.addWidget(top)

        # -- quick ops -----------------------------------------------------------
        ops_row = QWidget()
        ops_layout = QHBoxLayout(ops_row)
        ops_layout.setContentsMargins(0, 0, 0, 0)
        ops_layout.setSpacing(6)
        fetch_button = button("Fetch", "primary")
        fetch_button.setObjectName("fetchButton")
        pull_button = button("Pull (rebase)", "ghost")
        pull_button.setObjectName("pullButton")
        status_button = button("Status", "ghost")
        status_button.setObjectName("statusButton")
        commits_button = button("Recent commits", "ghost")
        commits_button.setObjectName("commitsButton")
        fetch_all_button = button("Fetch all subfolders", "ghost")
        fetch_all_button.setObjectName("fetchAllButton")
        fetch_all_button.setToolTip("Run git fetch in every git repository under this folder (workspaces / submodules)")
        for widget in (fetch_button, pull_button, status_button, commits_button, fetch_all_button):
            ops_layout.addWidget(widget)
        ops_layout.addStretch(1)
        page_layout.addWidget(ops_row)

        status_label = styled_label("", "hint")
        status_label.setObjectName("gitStatusLabel")
        status_label.setWordWrap(True)
        page_layout.addWidget(status_label)

        result_stack = QStackedWidget()
        result_stack.setObjectName("gitResultStack")

        output_view = QPlainTextEdit()
        output_view.setObjectName("gitOutput")
        output_view.setReadOnly(True)
        output_view.setFrameStyle(0)
        font = QFont()
        font.setFamilies(["SF Mono", "Menlo", "monospace"])
        font.setPointSizeF(11.5)
        output_view.setFont(font)
        result_stack.addWidget(output_view)

        commits_table = QTableWidget(0, 4)
        commits_table.setObjectName("commitsTable")
        commits_table.setHorizontalHeaderLabels(["Commit", "Author", "Date", "Message"])
        commits_table.verticalHeader().setVisible(False)
        commits_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        commits_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        commits_table.setColumnWidth(0, 90)
        commits_table.setColumnWidth(1, 140)
        commits_table.setColumnWidth(2, 110)
        commits_table.horizontalHeader().setStretchLastSection(True)
        result_stack.addWidget(commits_table)
        page_layout.addWidget(result_stack, 1)

        # -- per-tab state ---------------------------------------------------------
        state = {"path": path, "busy": False, "is_repo": False}
        pending: list = []
        # Retained on the page so close_tab can cancel in-flight workers.
        page.pending_workers = pending

        def set_busy(busy: bool) -> None:
            state["busy"] = busy
            for widget in (fetch_button, pull_button, status_button,
                           commits_button, fetch_all_button):
                widget.setEnabled(not busy)

        def set_repo_ops(enabled: bool) -> None:
            for widget in (fetch_button, pull_button, status_button, commits_button):
                widget.setEnabled(enabled and not state["busy"])

        def show_commits(rows: list) -> None:
            commits_table.setRowCount(0)
            commits_table.setRowCount(len(rows))
            for row, parts in enumerate(rows):
                sha = parts[0] if len(parts) > 0 else ""
                author = parts[1] if len(parts) > 1 else ""
                date = parts[2] if len(parts) > 2 else ""
                message = parts[3] if len(parts) > 3 else ""
                sha_item = QTableWidgetItem(sha)
                sha_item.setForeground(QColor(colors["cyan"]))
                commits_table.setItem(row, 0, sha_item)
                commits_table.setItem(row, 1, QTableWidgetItem(author))
                commits_table.setItem(row, 2, QTableWidgetItem(date))
                commits_table.setItem(row, 3, QTableWidgetItem(message))
            result_stack.setCurrentWidget(commits_table)

        def show_output(header: str, body: str) -> None:
            text = f"$ git {header}\n" if header else ""
            if body:
                text += body
            output_view.setPlainText(text)
            output_view.moveCursor(QTextCursor.MoveOperation.End)
            result_stack.setCurrentWidget(output_view)

        def run_worker(operation: str, on_done) -> None:
            worker = GitWorker(operation, state["path"], executable=git_exe())
            pending.append(worker)

            def done(result, current=worker) -> None:
                if page.closed:
                    return  # tab closed while the worker ran — widgets are gone
                if current in pending:
                    pending.remove(current)
                set_busy(False)
                on_done(result)

            def failed(exc, current=worker) -> None:
                if page.closed:
                    return
                if current in pending:
                    pending.remove(current)
                set_busy(False)
                show_output("", f"Operation failed: {exc}")
                status_label.setText(f"Error: {exc}")

            worker.signals.finished.connect(done)
            worker.signals.error.connect(failed)
            QThreadPool.globalInstance().start(worker)

        def on_open(result) -> None:
            if not result["is_repo"]:
                set_repo_ops(False)
                show_output(
                    "",
                    f"Not a git repository: {path}\n\n"
                    "No .git folder was found here. Open a folder that contains a "
                    "repository, or use “Fetch all subfolders” if repositories live "
                    "in subdirectories.",
                )
                status_label.setText("Not a git repository — quick operations disabled.")
                return
            state["is_repo"] = True
            branch_pill.setText(result["branch"] or "unknown")
            set_repo_ops(True)
            status_label.setText(
                "● working tree has changes" if result["dirty"] else "● working tree clean"
            )
            if result["rows"]:
                show_commits(result["rows"])
            else:
                show_output("", "No commits yet in this repository.")
            pin_folder(path, os.path.basename(path.rstrip("/")) or path)

        def run_op(operation: str, header: str) -> None:
            if not state["path"] or state["busy"]:
                return
            if not state["is_repo"] and operation in ("fetch", "pull", "status"):
                status_label.setText("Not a git repository — open a repository folder first.")
                return
            set_busy(True)
            set_repo_ops(False)
            status_label.setText(f"Running {header}…")

            def on_done(result) -> None:
                show_output(header, result["output"])
                status_label.setText(f"{header} finished")

            run_worker(operation, on_done)

        def run_log() -> None:
            if not state["path"] or state["busy"]:
                return
            if not state["is_repo"]:
                status_label.setText("Not a git repository — open a repository folder first.")
                return
            set_busy(True)
            set_repo_ops(False)
            status_label.setText("Loading recent commits…")

            def on_done(result) -> None:
                if result["rows"]:
                    show_commits(result["rows"])
                else:
                    show_output("log", "No commits yet in this repository.")
                status_label.setText("Recent commits loaded")

            run_worker("log", on_done)

        def run_fetch_all() -> None:
            if not state["path"] or state["busy"]:
                return
            set_busy(True)
            set_repo_ops(False)
            status_label.setText("Scanning subfolders for git repositories…")

            def on_done(result) -> None:
                repos = result["repos"]
                if not repos:
                    show_output("fetch --all --prune", "No git repositories found under this folder.")
                    status_label.setText("No nested repositories found.")
                    return
                lines = [f"Found {len(repos)} git repository(ies) — fetching each:"]
                for item in result["results"]:
                    marker = "✓" if item["ok"] else "✕"
                    lines.append(f"{marker} {item['path']}")
                    if item["output"]:
                        lines.extend("    " + line for line in item["output"].splitlines()[:6])
                show_output("fetch --all --prune", "\n".join(lines))
                ok = sum(1 for r in result["results"] if r["ok"])
                status_label.setText(f"Fetched {ok} / {len(repos)} repositories")

            run_worker("fetch_all", on_done)

        # -- wiring ---------------------------------------------------------------
        back_button.clicked.connect(lambda: tabs.setCurrentIndex(0))
        fetch_button.clicked.connect(lambda: run_op("fetch", "fetch --all --prune"))
        pull_button.clicked.connect(lambda: run_op("pull", "pull --rebase"))
        status_button.clicked.connect(lambda: run_op("status", "status --short --branch"))
        commits_button.clicked.connect(run_log)
        fetch_all_button.clicked.connect(run_fetch_all)

        title = os.path.basename(path.rstrip("/")) or path
        index = tabs.addTab(page, title)
        tabs.setTabToolTip(index, path)
        tabs.setCurrentIndex(index)

        if demo or favorites_repo is None:
            show_output("", "Demo data — open a real folder to run git operations.")
            set_repo_ops(False)
            status_label.setText("No database in this session (demo mode).")
            return
        if history_repo is not None:
            history_repo.add_recent_folder(path)
        set_busy(True)
        set_repo_ops(False)
        status_label.setText("Opening repository…")
        run_worker("open", on_open)

    def close_tab(index: int) -> None:
        """Close a repository tab; the landing page (index 0) never closes.

        Cancellation is cooperative: a worker blocked in a subprocess call
        (e.g. a long fetch) keeps running until that call returns, but the
        ``page.closed`` flag makes every queued signal a no-op, so the tab
        can be destroyed without any risk of touching stale widgets.
        """
        if index == 0:
            return
        widget = tabs.widget(index)
        widget.closed = True
        for worker in getattr(widget, "pending_workers", ()):
            worker.cancel()
        tabs.removeTab(index)
        widget.deleteLater()
        # Closing a non-active tab does not fire currentChanged — flush so the
        # persisted tab list never keeps a tab the user already closed.
        _persist_view_state()

    # ------------------------------------------------------------ view state
    # The home page's filters and open repository tabs survive restarts: they
    # are persisted through ConfigurationService as hidden schema keys — the
    # search text and group filter are debounced while typing, tab changes
    # flush immediately, and everything is restored when the module builds.
    def _persist_view_state() -> None:
        """Save the search text, selected group, open tabs and the active tab."""
        if service is None:
            return
        try:
            open_paths = [
                tabs.widget(i).property("repoPath")
                for i in range(1, tabs.count())
                if tabs.widget(i).property("repoPath")
            ]
            active = tabs.currentWidget().property("repoPath") if tabs.currentIndex() > 0 else ""
            service.set("git.home.search", search_edit.text().strip())
            service.set("git.home.group", "" if landing_state["group"] is None else landing_state["group"])
            service.set("git.home.tabs", json.dumps(open_paths))
            service.set("git.home.active", active)
        except Exception:  # noqa: BLE001 — persistence must never break the UI
            logger.exception("failed to persist git home view state")

    def _restore_view_state() -> None:
        """Reopen the saved search text, selected group and repository tabs."""
        if service is None:
            return
        try:
            search = str(service.get("git.home.search") or "")
            group = service.get("git.home.group")
            active = str(service.get("git.home.active") or "")
            raw_tabs = service.get("git.home.tabs") or "[]"
            try:
                open_paths = json.loads(raw_tabs) if isinstance(raw_tabs, str) else []
            except (TypeError, ValueError):
                open_paths = []
        except Exception:  # noqa: BLE001 — a bad saved value must never crash the view
            logger.exception("failed to restore git home view state")
            return
        if search:
            search_edit.blockSignals(True)
            search_edit.setText(search)
            search_edit.blockSignals(False)
        if group is not None and str(group) != "":
            landing_state["group"] = str(group)
        for path in open_paths:
            # Folders that no longer exist are skipped; each surviving path
            # opens in its own tab (deduped, repo check runs off-thread).
            if isinstance(path, str) and path and os.path.isdir(path):
                open_folder(path)
        if active:
            for i in range(1, tabs.count()):
                if tabs.widget(i).property("repoPath") == active:
                    tabs.setCurrentIndex(i)
                    break

    persist_timer = QTimer(root)
    persist_timer.setSingleShot(True)
    persist_timer.setInterval(400)
    persist_timer.timeout.connect(_persist_view_state)

    # Remote status auto-refresh: only while the landing page is on screen.
    status_timer = QTimer(root)
    status_timer.setInterval(120_000)
    status_timer.timeout.connect(
        lambda: refresh_all_statuses() if landing.isVisible() else None
    )
    status_timer.start()

    # ---------------------------------------------------------------- wiring
    def choose_folder() -> None:
        chosen = QFileDialog.getExistingDirectory(root, "Choose a git folder", os.path.expanduser("~"))
        if chosen:
            open_folder(chosen)

    def scan_for_repos() -> None:
        """Scan a folder for nested git repositories; add the chosen ones."""
        if favorites_repo is None:
            return
        dialog = ScanReposDialog(
            root,
            favorites_repo,
            existing_groups=known_groups(favorites_repo.by_kind("folder")),
            executable=git_exe(),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for favorite in dialog.added:
                persist_new(favorite)  # deduped insert + recent-folders history
            refresh_favorites()

    def _on_filter_changed() -> None:
        refresh_favorites()
        persist_timer.start()  # debounce while typing in the search field

    open_button.clicked.connect(choose_folder)
    scan_button.clicked.connect(scan_for_repos)
    add_button.clicked.connect(add_repository)
    search_edit.textChanged.connect(lambda _text: _on_filter_changed())
    manage_button.clicked.connect(manage_groups)
    refresh_button.clicked.connect(refresh_favorites)
    favorites_list.customContextMenuRequested.connect(show_card_menu)
    # Group rows are QPushButtons that call open_group directly — do not hook
    # currentItemChanged (it re-enters clear_list_widget and can segfault).
    bulk_fetch.clicked.connect(lambda: run_bulk("fetch"))
    bulk_status.clicked.connect(lambda: run_bulk("status"))
    bulk_reset.clicked.connect(lambda: run_bulk("reset"))
    console_toggle.clicked.connect(toggle_console)
    console_clear.clicked.connect(clear_console)
    tabs.tabCloseRequested.connect(close_tab)
    tabs.currentChanged.connect(lambda _index: _persist_view_state())

    _restore_view_state()
    refresh_favorites()
    # Defer status workers until after the landing widgets are fully built.
    QTimer.singleShot(0, refresh_all_statuses)
    return root


git_module = Module(
    id="git",
    title="Git",
    icon="git",
    build=build_view,
    navigator=(
        ("Favorites", ("Pin folders from the landing page",)),
        ("Quick ops", ("Fetch", "Pull (rebase)", "Recent commits")),
    ),
    details=(
        ("Source", "Favorite folders"),
        ("Quick ops", "Fetch · Pull (rebase) · Status · Commits"),
        ("Fetch all", "All subfolder repositories"),
    ),
    status="Git · choose a folder",
)
