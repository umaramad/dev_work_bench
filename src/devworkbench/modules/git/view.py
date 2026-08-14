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
import re
import uuid

from PySide6.QtCore import QSize, QThreadPool, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from devworkbench.models.persistence import Favorite
from devworkbench.modules.base import Module
from devworkbench.modules.git.dialog import (
    ActionPlaceholdersDialog,
    CopyGroupActionsDialog,
    EditGroupActionsDialog,
    GroupManagerDialog,
    RepoDialog,
    ScanReposDialog,
)
from devworkbench.ui.samples import GIT_REPOS
from devworkbench.ui.theme import current_colors
from devworkbench.ui.widgets.common import button, clear_list_widget, icon_button, search_field, styled_label
from devworkbench.workers.git_worker import GitWorker

logger = logging.getLogger("devworkbench.modules.git")

# Fixed card row height keeps the favorites list scroll smooth while status
# pills / toasts update (no per-update sizeHint thrashing).
_CARD_ROW_HEIGHT = 118
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
_DEFAULT_GROUP_ACTIONS = (
    ("Add", "git add ."),
    ("Commit", 'git commit -m "{{message}}"'),
    ("Push", "git push"),
)


def _bump_font(widget, delta: int = 2, min_pt: int = 13, *, bold: bool | None = None) -> None:
    """Increase a widget's point size for the Git landing page."""
    font = widget.font()
    font.setPointSize(max(font.pointSize() + delta, min_pt))
    if bold is not None:
        font.setBold(bold)
    widget.setFont(font)


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
    _bump_font(title, 4, 18, bold=True)
    heading_layout.addWidget(title)
    subtitle = styled_label("Groups on the left · repos on the right · Open opens a tab", "hint")
    _bump_font(subtitle, 2, 13)
    heading_layout.addWidget(subtitle)
    heading_layout.addStretch(1)
    open_button = button("Open folder…", "ghost")
    open_button.setObjectName("openFolderButton")
    open_button.setToolTip("Pick a folder and open it — it is added to your favorites automatically")
    _bump_font(open_button, 2, 13)
    heading_layout.addWidget(open_button)
    scan_button = button("Scan for repositories…", "ghost")
    scan_button.setObjectName("scanForReposButton")
    scan_button.setToolTip("Scan a folder's subdirectories for git repositories, then add the ones you want")
    _bump_font(scan_button, 2, 13)
    heading_layout.addWidget(scan_button)
    add_button = button("Add repository…", "primary")
    add_button.setObjectName("addRepositoryButton")
    add_button.setToolTip("Add a repository with a name and an optional group")
    _bump_font(add_button, 2, 13)
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
    _bump_font(search_edit, 2, 13)
    toolbar_layout.addWidget(search_edit, 1)
    manage_button = button("Manage groups", "ghost")
    manage_button.setObjectName("manageGroupsButton")
    manage_button.setToolTip("Rename, merge or delete repository groups")
    _bump_font(manage_button, 2, 13)
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
    groups_heading = styled_label("Groups", "hint")
    _bump_font(groups_heading, 2, 13)
    groups_pane_layout.addWidget(groups_heading)
    groups_list = QListWidget()
    groups_list.setObjectName("groupList")
    groups_list.setFrameStyle(0)
    groups_list.setSpacing(4)
    groups_list.setUniformItemSizes(True)
    groups_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    groups_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
    groups_pane_layout.addWidget(groups_list, 1)
    groups_empty = styled_label(
        "No repositories yet — click “Add repository…” or “Scan for repositories…” to get started.",
        "hint",
    )
    groups_empty.setWordWrap(True)
    _bump_font(groups_empty, 2, 13)
    groups_pane_layout.addWidget(groups_empty)
    split.addWidget(groups_pane)

    repos_pane = QWidget()
    repos_pane.setObjectName("reposPane")
    repos_pane_layout = QVBoxLayout(repos_pane)
    repos_pane_layout.setContentsMargins(0, 0, 0, 0)
    repos_pane_layout.setSpacing(8)
    repos_title = styled_label("", "muted")
    repos_title.setObjectName("groupTitle")
    _bump_font(repos_title, 3, 16, bold=True)
    repos_pane_layout.addWidget(repos_title)

    # Shared branch list (common for all repos) + checkout/fetch for the group.
    branch_bar = QWidget()
    branch_bar.setObjectName("branchBar")
    branch_layout = QHBoxLayout(branch_bar)
    branch_layout.setContentsMargins(0, 0, 0, 0)
    branch_layout.setSpacing(6)
    branch_label = styled_label("Branch", "hint")
    _bump_font(branch_label, 2, 13)
    branch_layout.addWidget(branch_label)
    branch_combo = QComboBox()
    branch_combo.setObjectName("branchCombo")
    branch_combo.setMinimumWidth(160)
    branch_combo.setToolTip("Shared branch list — used for Checkout & reset across this group")
    _bump_font(branch_combo, 2, 13)
    branch_layout.addWidget(branch_combo, 1)
    branch_fetch = button("Checkout & reset", "primary")
    branch_fetch.setObjectName("branchFetchButton")
    branch_fetch.setToolTip(
        "In each repo: fetch, checkout origin/<branch> (create/reset local), "
        "hard-reset — discards local changes; errors show on the card"
    )
    _bump_font(branch_fetch, 2, 13)
    branch_layout.addWidget(branch_fetch)
    actions_button = QToolButton()
    actions_button.setObjectName("groupActionsButton")
    actions_button.setText("Actions")
    actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    actions_button.setToolTip("Run a custom git command across every repo in this group")
    _bump_font(actions_button, 2, 13)
    actions_menu = QMenu(actions_button)
    actions_button.setMenu(actions_menu)
    branch_layout.addWidget(actions_button)
    branch_edit = button("Edit branches…", "ghost")
    branch_edit.setObjectName("branchEditButton")
    branch_edit.setToolTip("Configure the shared list of branch names")
    _bump_font(branch_edit, 2, 13)
    branch_layout.addWidget(branch_edit)
    repos_pane_layout.addWidget(branch_bar)

    bulk_bar = QWidget()
    bulk_bar.setObjectName("bulkBar")
    bulk_layout = QHBoxLayout(bulk_bar)
    bulk_layout.setContentsMargins(0, 0, 0, 0)
    bulk_layout.setSpacing(6)
    bulk_fetch = button("Fetch all", "ghost")
    bulk_fetch.setObjectName("bulkFetchButton")
    bulk_fetch.setToolTip("Fetch every repository in this group")
    _bump_font(bulk_fetch, 2, 13)
    bulk_status = button("Status all", "ghost")
    bulk_status.setObjectName("bulkStatusButton")
    bulk_status.setToolTip("Run git status on every repository in this group")
    _bump_font(bulk_status, 2, 13)
    bulk_reset = button("Reset all", "ghost")
    bulk_reset.setObjectName("bulkResetButton")
    bulk_reset.setToolTip("Run soft git reset on every repository in this group")
    _bump_font(bulk_reset, 2, 13)
    bulk_busy_label = styled_label("", "hint")
    bulk_busy_label.setObjectName("bulkBusyLabel")
    _bump_font(bulk_busy_label, 2, 13)
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
    favorites_list.setSpacing(6)
    favorites_list.setUniformItemSizes(True)
    favorites_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    favorites_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    favorites_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    repos_pane_layout.addWidget(favorites_list, 1)

    empty_state = styled_label(
        "Select a group on the left to see its repositories.",
        "hint",
    )
    empty_state.setWordWrap(True)
    _bump_font(empty_state, 2, 13)
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
    _bump_font(console_toggle, 2, 13)
    console_header_layout.addWidget(console_toggle)
    console_status = styled_label("idle", "hint")
    console_status.setObjectName("consoleStatus")
    _bump_font(console_status, 2, 13)
    console_header_layout.addWidget(console_status, 1)
    console_clear = button("Clear", "ghost")
    console_clear.setObjectName("consoleClear")
    _bump_font(console_clear, 2, 13)
    console_clear.hide()
    console_header_layout.addWidget(console_clear)
    console_wrap_layout.addWidget(console_header)
    console_log = QPlainTextEdit()
    console_log.setObjectName("consoleLog")
    console_log.setReadOnly(True)
    console_log.setMaximumBlockCount(500)
    console_log.setFont(QFont("Menlo", 12))
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
        "status_gen": 0,
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
        row.setObjectName("repoCard")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 12, 12, 12)
        row_layout.setSpacing(12)

        labels = QVBoxLayout()
        labels.setSpacing(6)
        labels.setContentsMargins(0, 0, 0, 0)

        name = QLabel(label or os.path.basename(path.rstrip("/")) or path)
        _bump_font(name, 3, 16, bold=True)
        name.setWordWrap(False)
        labels.addWidget(name)

        path_text = QLabel(path)
        path_text.setObjectName("hint")
        path_text.setToolTip(path)
        path_text.setWordWrap(False)
        path_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        _bump_font(path_text, 2, 13)
        # Single-line elided path — full path stays in the tooltip.
        metrics = path_text.fontMetrics()
        path_text.setText(metrics.elidedText(path, Qt.TextElideMode.ElideMiddle, 480))
        path_text.setFixedHeight(metrics.height() + 2)
        labels.addWidget(path_text)

        # Persistent branch / sync summary — fixed height so list rows don't jump.
        remote_pill = QLabel("…")
        remote_pill.setObjectName("statusPill")
        remote_pill.setProperty("role", "cardRemoteStatus")
        remote_pill.setProperty("state", "")
        remote_pill.setWordWrap(False)
        remote_pill.setFixedHeight(26)
        _bump_font(remote_pill, 2, 13)
        labels.addWidget(remote_pill, 0, Qt.AlignmentFlag.AlignLeft)

        # Short op feedback — reserved slot (clear text instead of hide/show).
        status_label = styled_label("", "hint")
        status_label.setProperty("role", "cardStatus")
        status_label.setWordWrap(False)
        _bump_font(status_label, 2, 13)
        status_label.setFixedHeight(metrics.height() + 2)
        labels.addWidget(status_label)

        row_layout.addLayout(labels, 1)

        if demo:
            pill = QLabel("demo")
            pill.setObjectName("statusPill")
            pill.setProperty("state", "warn")
            _bump_font(pill, 1, 12)
            row_layout.addWidget(pill, 0, Qt.AlignmentFlag.AlignTop)

        actions = QHBoxLayout()
        actions.setSpacing(4)
        actions.setContentsMargins(0, 0, 0, 0)
        open_btn = button("Open", "ghost")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _bump_font(open_btn, 2, 13)
        open_btn.clicked.connect(lambda _checked=False, p=path: open_folder(p, demo=demo))
        actions.addWidget(open_btn)
        if not demo:
            fetch_btn = icon_button(icons, "download", "Fetch this repository")
            fetch_btn.setObjectName("cardFetchButton")
            fetch_btn.clicked.connect(
                lambda _checked=False, p=path: fetch_repo(p, fetch_btn, status_label)
            )
            actions.addWidget(fetch_btn)
            status_refresh = icon_button(icons, "refresh", "Refresh remote status")
            status_refresh.setObjectName("cardStatusRefresh")
            status_refresh.clicked.connect(
                lambda _checked=False, p=path: refresh_card_status(p)
            )
            actions.addWidget(status_refresh)
            edit_btn = icon_button(icons, "edit", "Edit repository")
            edit_btn.setObjectName("editRepoButton")
            edit_btn.clicked.connect(lambda _checked=False, p=path: edit_repository(p))
            actions.addWidget(edit_btn)
            unpin = icon_button(icons, "close", "Remove from favorites")
            unpin.setObjectName("unpinButton")
            unpin.clicked.connect(lambda _checked=False, p=path: remove_favorite(p))
            actions.addWidget(unpin)
        row_layout.addLayout(actions, 0)
        row_layout.setAlignment(actions, Qt.AlignmentFlag.AlignTop)
        return row

    def add_card(path: str, label: str, demo: bool) -> None:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, path)
        widget = card_widget(path, label, demo)
        # Fixed height — status updates must not resize rows (scroll jank).
        item.setSizeHint(QSize(0, _CARD_ROW_HEIGHT))
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
        row_layout.setContentsMargins(8, 8, 8, 8)
        row_layout.setSpacing(8)
        icon_label = QLabel()
        icon_label.setPixmap(icons.get("folder", 18).pixmap(18, 18))
        icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row_layout.addWidget(icon_label)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name_label = QLabel(name)
        _bump_font(name_label, 2, 14, bold=True)
        name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_col.addWidget(name_label)
        count_label = styled_label(f"{count} {noun}", "hint")
        _bump_font(count_label, 2, 12)
        count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_col.addWidget(count_label)
        row_layout.addLayout(text_col, 1)
        item.setSizeHint(QSize(200, 56))
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
        branch_fetch.setEnabled(enabled and bool(branch_combo.currentText().strip()))
        branch_combo.setEnabled(not landing_state["bulk_busy"])
        branch_edit.setEnabled(not landing_state["bulk_busy"])
        actions_button.setEnabled(
            enabled
            and landing_state["group"] not in (None, "__demo__")
            and not landing_state["bulk_busy"]
        )
        if landing_state["bulk_busy"]:
            bulk_busy_label.setText("Running…")
            bulk_busy_label.show()
        else:
            bulk_busy_label.hide()
        _refresh_actions_menu()

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

    def _sync_group_checked(key: str | None) -> None:
        """Update checked styles without rebuilding the left list."""
        if key is None:
            return
        for i in range(groups_list.count()):
            item = groups_list.item(i)
            widget = groups_list.itemWidget(item)
            if isinstance(widget, QPushButton):
                widget.setChecked(item.data(Qt.ItemDataRole.UserRole) == key)
        _select_group_item(key)

    def favorites_for_group() -> list:
        group = landing_state["group"]
        if favorites_repo is None or group is None or group == "__demo__":
            return []
        return [
            favorite
            for favorite in favorites_repo.by_kind("folder")
            if (favorite.group_name or "").strip() == group
        ]

    def refresh_repo_cards() -> None:
        """Rebuild only the right-hand repo list for the current group."""
        if landing_state.get("refreshing"):
            return
        landing_state["refreshing"] = True
        # Invalidate staggered status timers from a previous group selection.
        landing_state["status_gen"] = int(landing_state.get("status_gen") or 0) + 1
        favorites_list.setUpdatesEnabled(False)
        try:
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
            favorites_list.setUpdatesEnabled(True)
            landing_state["refreshing"] = False

    def refresh_favorites() -> None:
        """Rebuild both panes: group list + cards for the selected group."""
        if landing_state.get("refreshing"):
            return
        ensure_group_selection()
        refresh_groups()
        refresh_repo_cards()

    def open_group(key: str) -> None:
        """Select a group and refresh the right-hand repo list in place."""
        if key is None or landing_state.get("refreshing"):
            return
        if key == landing_state["group"] and favorites_list.count() > 0:
            # Already showing this group — still sync the checked row state.
            _sync_group_checked(key)
            return
        landing_state["group"] = key
        _sync_group_checked(key)
        # Only rebuild the repo pane — rebuilding groups on every click was janky.
        refresh_repo_cards()
        if landing_state["group"] not in (None, "__demo__") and not landing_state["bulk_busy"]:
            # Let the list paint first, then kick off status workers.
            QTimer.singleShot(0, refresh_all_statuses)
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
        """Report a short fetch result on the card, then auto-clear it.

        The landing list may be rebuilt while a fetch runs (e.g. a repo tab
        opens and auto-pins, refreshing the cards) — a RuntimeError means the
        old card is gone and there is nothing left to update.
        """
        try:
            fetch_btn._fetching = False
            fetch_btn.setEnabled(True)
            fetch_btn.setToolTip("Fetch this repository")
            _show_card_toast(status_label, "Fetched" if ok else "Fetch failed", ok=ok, path=path)
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

    def _summarize_op(op: str, ok: bool, output: str) -> str:
        """Human card toast — never dump raw ``## branch...`` git lines."""
        if not ok:
            return {
                "fetch": "Fetch failed",
                "status": "Status failed",
                "reset": "Reset failed",
            }.get(op, "Failed")
        if op == "fetch":
            return "Fetched"
        if op == "reset":
            return "Reset"
        if op == "status":
            lines = [line for line in (output or "").splitlines() if line.strip()]
            branch_line = next((line for line in lines if line.startswith("##")), "")
            dirty = [line for line in lines if not line.startswith("##")]
            branch = "—"
            if branch_line:
                rest = branch_line[2:].strip()
                branch = rest.split("...")[0].strip().split()[0] if rest else "—"
            if dirty:
                n = len(dirty)
                return f"{branch} · {n} change{'s' if n != 1 else ''}"
            return f"{branch} · clean"
        return "Done"

    def _show_card_toast(label, text: str, ok: bool = True, ms: int = 5000, path: str | None = None) -> None:
        """Show a brief op result, then clear so the branch pill stays the focus."""
        try:
            label.setText(f"{'✓' if ok else '✕'}  {text}")
            label.setProperty("state", "ok" if ok else "err")
            label.style().unpolish(label)
            label.style().polish(label)

            def _hide() -> None:
                try:
                    label.setText("")
                    label.setProperty("state", "")
                    label.style().unpolish(label)
                    label.style().polish(label)
                except RuntimeError:
                    pass

            timer = QTimer(label)
            timer.setSingleShot(True)
            timer.setInterval(ms)
            timer.timeout.connect(_hide)
            timer.start()
        except RuntimeError:
            pass

    def _set_card_op_status(path: str, text: str, ok: bool | None = None) -> None:
        """Update the per-card toast line if the card is still on screen."""
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
                        if ok is None:
                            label.setText(text)
                        else:
                            _show_card_toast(label, text, ok=ok, path=path)
                        return
        except RuntimeError:
            pass

    _DEFAULT_BRANCHES = ("main", "master", "develop")

    def _load_branch_names() -> list[str]:
        raw = '["main","master","develop"]'
        if service is not None:
            try:
                raw = str(service.get("git.home.branches") or raw)
            except Exception:  # noqa: BLE001
                pass
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            data = []
        names: list[str] = []
        if isinstance(data, list):
            for item in data:
                name = str(item).strip()
                if name and name not in names:
                    names.append(name)
        return names or list(_DEFAULT_BRANCHES)

    def _save_branch_names(names: list[str]) -> None:
        if service is None:
            return
        try:
            service.set("git.home.branches", json.dumps(names))
        except Exception:  # noqa: BLE001
            logger.exception("failed to save git.home.branches")

    def _refresh_branch_combo(prefer: str | None = None) -> None:
        names = _load_branch_names()
        current = prefer if prefer is not None else branch_combo.currentText().strip()
        if service is not None and not current:
            try:
                current = str(service.get("git.home.branch") or "")
            except Exception:  # noqa: BLE001
                current = ""
        branch_combo.blockSignals(True)
        branch_combo.clear()
        for name in names:
            branch_combo.addItem(name)
        index = branch_combo.findText(current)
        branch_combo.setCurrentIndex(index if index >= 0 else 0)
        branch_combo.blockSignals(False)

    def _edit_branches_dialog() -> None:
        dialog = QDialog(root)
        dialog.setWindowTitle("Edit shared branches")
        dialog.setModal(True)
        layout_d = QVBoxLayout(dialog)
        layout_d.addWidget(
            styled_label("One branch name per line (shared across all repositories).", "hint")
        )
        editor = QPlainTextEdit()
        editor.setPlainText("\n".join(_load_branch_names()))
        editor.setMinimumSize(360, 200)
        layout_d.addWidget(editor)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        layout_d.addWidget(buttons)

        def save() -> None:
            names: list[str] = []
            for line in editor.toPlainText().splitlines():
                name = line.strip()
                if name and name not in names:
                    names.append(name)
            if not names:
                names = list(_DEFAULT_BRANCHES)
            _save_branch_names(names)
            selected = branch_combo.currentText().strip()
            _refresh_branch_combo(prefer=selected if selected in names else names[0])
            persist_timer.start()
            dialog.accept()

        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

    def run_branch_fetch() -> None:
        """Fetch, checkout origin/<branch>, hard-reset — per group repo (no local check)."""
        if landing_state["bulk_busy"]:
            return
        branch = branch_combo.currentText().strip()
        if not branch:
            console_append("  · pick a branch in the dropdown first", ok=False)
            set_console_status("idle")
            return
        group = landing_state["group"]
        if group in (None, "__demo__"):
            console_append("  · select a real group first", ok=False)
            return
        members = favorites_for_group()
        if not members:
            console_append("  · no repositories in this group", ok=False)
            return
        paths = [favorite.ref for favorite in members]
        landing_state["bulk_busy"] = True
        bulk_queue[:] = list(paths)
        bulk_meta.update({
            "op": "branch_reset",
            "branch": branch,
            "done": 0,
            "total": len(paths),
            "ok": 0,
        })
        _set_bulk_enabled(False)
        if not landing_state.get("console_open"):
            # Surface progress when the console is collapsed.
            set_console_status(f"reset 0/{len(paths)}…")
        console_append(
            f"$ checkout+reset origin/{branch} — {len(paths)} repos in {group or 'Ungrouped'}"
        )
        set_console_status(f"reset 0/{len(paths)}…")
        _run_next_branch_fetch()

    def _set_card_branch_pill(path: str, branch: str, state: str = "ok") -> None:
        """Immediately show the new branch on the card after a successful switch."""
        label = _card_remote_pill(path)
        if label is None:
            return
        text = f"{branch} · up to date"
        try:
            label.setText(text)
            label.setProperty("state", state)
            label.setToolTip(text)
            label.style().unpolish(label)
            label.style().polish(label)
            status_cache[path] = {
                **(status_cache.get(path) or {}),
                "is_repo": True,
                "branch": branch,
                "ahead": 0,
                "behind": 0,
                "upstream": f"origin/{branch}",
            }
        except RuntimeError:
            pass

    def _run_next_branch_fetch() -> None:
        if not bulk_queue:
            _finish_branch_fetch()
            return
        branch = str(bulk_meta.get("branch") or "")
        path = bulk_queue.pop(0)
        bulk_meta["done"] += 1
        set_console_status(f"reset {bulk_meta['done']}/{bulk_meta['total']}…")

        def _err_line(output: str, fallback: str) -> str:
            for line in (output or "").splitlines():
                text = line.strip()
                if text:
                    return text[:120]
            return fallback

        # Always fetch first — local branch presence does not matter.
        console_append(f"$ git fetch — {path}")
        _set_card_op_status(path, "Fetching…")
        fetch = GitWorker("fetch", path, executable=git_exe())
        bulk_workers.append(fetch)

        def after_fetch(fres, fworker=fetch, fp=path, fb=branch) -> None:
            if fworker in bulk_workers:
                bulk_workers.remove(fworker)
            fok = bool(isinstance(fres, dict) and fres.get("ok"))
            fout = str((fres or {}).get("output") or "").strip() if isinstance(fres, dict) else ""
            summary = _err_line(fout, "ok" if fok else "failed")
            console_append(f"  {'✓' if fok else '✕'} fetch: {summary[:180]}", ok=fok)
            if not fok:
                _set_card_op_status(fp, f"Fetch failed — {summary}", ok=False)
                _run_next_branch_fetch()
                return

            # Require origin/<branch> after fetch; otherwise error on the card.
            console_append(f"$ has_remote origin/{fb} — {fp}")
            _set_card_op_status(fp, f"Checking origin/{fb}…")
            remote_check = GitWorker(
                "has_branch", fp, args=(fb, "origin"), executable=git_exe()
            )
            bulk_workers.append(remote_check)

            def after_remote(rres, rworker=remote_check, rp=fp, rb=fb) -> None:
                if rworker in bulk_workers:
                    bulk_workers.remove(rworker)
                rexists = bool(isinstance(rres, dict) and rres.get("exists"))
                if not rexists:
                    msg = f"No origin/{rb}"
                    console_append(f"  ✕ {msg}", ok=False)
                    _set_card_op_status(rp, msg, ok=False)
                    _run_next_branch_fetch()
                    return

                # Create/reset local branch from remote tip; -f discards local dirt.
                target = f"origin/{rb}"
                console_append(f"$ git checkout -f -B {rb} {target} — {rp}")
                _set_card_op_status(rp, f"Checkout {rb}…")
                checkout = GitWorker(
                    "checkout", rp, args=(rb, "force", "origin"), executable=git_exe()
                )
                bulk_workers.append(checkout)

                def after_checkout(cres, cworker=checkout, cp=rp, cb=rb) -> None:
                    if cworker in bulk_workers:
                        bulk_workers.remove(cworker)
                    cok = bool(isinstance(cres, dict) and cres.get("ok"))
                    cout = (
                        str((cres or {}).get("output") or "").strip()
                        if isinstance(cres, dict)
                        else ""
                    )
                    if not cok:
                        err = _err_line(cout, "checkout failed")
                        console_append(f"  ✕ checkout failed: {err}", ok=False)
                        _set_card_op_status(cp, f"Checkout failed — {err}", ok=False)
                        _run_next_branch_fetch()
                        return
                    console_append(f"  ✓ checked out {cb} from origin/{cb}", ok=True)
                    _set_card_branch_pill(cp, cb)

                    # Hard-reset to remote tip — wipe remaining local divergence.
                    rtarget = f"origin/{cb}"
                    console_append(f"$ git reset --hard {rtarget} — {cp}")
                    _set_card_op_status(cp, f"Reset {rtarget}…")
                    reset = GitWorker(
                        "reset", cp, args=("hard", rtarget), executable=git_exe()
                    )
                    bulk_workers.append(reset)

                    def after_reset(zres, zworker=reset, zp=cp, zb=cb) -> None:
                        if zworker in bulk_workers:
                            bulk_workers.remove(zworker)
                        zok = bool(isinstance(zres, dict) and zres.get("ok"))
                        zout = (
                            str((zres or {}).get("output") or "").strip()
                            if isinstance(zres, dict)
                            else ""
                        )
                        zsum = _err_line(zout, "ok" if zok else "reset failed")
                        console_append(f"  {'✓' if zok else '✕'} {zsum[:200]}", ok=zok)
                        if zok:
                            bulk_meta["ok"] += 1
                            _set_card_op_status(zp, f"{zb} · reset to origin", ok=True)
                            _set_card_branch_pill(zp, zb, state="ok")
                            refresh_card_status(zp)
                        else:
                            _set_card_op_status(zp, f"Reset failed — {zsum}", ok=False)
                            _set_card_branch_pill(zp, zb, state="warn")
                            refresh_card_status(zp)
                        _run_next_branch_fetch()

                    def reset_failed(exc, zworker=reset, zp=cp, zb=cb) -> None:
                        if zworker in bulk_workers:
                            bulk_workers.remove(zworker)
                        err = str(exc)
                        console_append(f"  ✕ {err}", ok=False)
                        _set_card_op_status(zp, f"Reset failed — {err[:100]}", ok=False)
                        _set_card_branch_pill(zp, zb, state="warn")
                        _run_next_branch_fetch()

                    reset.signals.finished.connect(after_reset)
                    reset.signals.error.connect(reset_failed)
                    QThreadPool.globalInstance().start(reset)

                def checkout_failed(exc, cworker=checkout, cp=rp) -> None:
                    if cworker in bulk_workers:
                        bulk_workers.remove(cworker)
                    err = str(exc)
                    console_append(f"  ✕ {err}", ok=False)
                    _set_card_op_status(cp, f"Checkout failed — {err[:100]}", ok=False)
                    _run_next_branch_fetch()

                checkout.signals.finished.connect(after_checkout)
                checkout.signals.error.connect(checkout_failed)
                QThreadPool.globalInstance().start(checkout)

            def remote_failed(exc, rworker=remote_check, rp=fp) -> None:
                if rworker in bulk_workers:
                    bulk_workers.remove(rworker)
                err = str(exc)
                console_append(f"  ✕ {err}", ok=False)
                _set_card_op_status(rp, f"Remote check failed — {err[:100]}", ok=False)
                _run_next_branch_fetch()

            remote_check.signals.finished.connect(after_remote)
            remote_check.signals.error.connect(remote_failed)
            QThreadPool.globalInstance().start(remote_check)

        def fetch_failed(exc, fworker=fetch, fp=path) -> None:
            if fworker in bulk_workers:
                bulk_workers.remove(fworker)
            err = str(exc)
            console_append(f"  ✕ {err}", ok=False)
            _set_card_op_status(fp, f"Fetch failed — {err[:100]}", ok=False)
            _run_next_branch_fetch()

        fetch.signals.finished.connect(after_fetch)
        fetch.signals.error.connect(fetch_failed)
        QThreadPool.globalInstance().start(fetch)

    def _finish_branch_fetch() -> None:
        branch = str(bulk_meta.get("branch") or "")
        ok_count = bulk_meta["ok"]
        total = bulk_meta["total"]
        failed = max(0, total - ok_count)
        landing_state["bulk_busy"] = False
        console_append(
            f"  checkout+reset origin/{branch} done — {ok_count}/{total} ok"
            + (f", {failed} failed" if failed else ""),
            ok=failed == 0,
        )
        set_console_status("idle")
        _set_bulk_enabled(
            landing_state["group"] not in (None, "__demo__")
            and bool(favorites_for_group())
        )

    # -------------------------------------------------------- group actions
    def _load_group_actions_map() -> dict:
        raw = "{}"
        if service is not None:
            try:
                raw = str(service.get("git.home.group_actions") or "{}")
            except Exception:  # noqa: BLE001
                pass
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def _save_group_actions_map(data: dict) -> None:
        if service is None:
            return
        try:
            service.set("git.home.group_actions", json.dumps(data))
        except Exception:  # noqa: BLE001
            logger.exception("failed to save git.home.group_actions")

    def _normalize_action(action: dict) -> dict:
        return {
            "id": str(action.get("id") or "") or str(uuid.uuid4()),
            "label": str(action.get("label") or "").strip(),
            "command": str(action.get("command") or "").strip(),
        }

    def _seed_actions() -> list[dict]:
        return [
            {"id": str(uuid.uuid4()), "label": label, "command": command}
            for label, command in _DEFAULT_GROUP_ACTIONS
        ]

    def _actions_for_group(group: str | None, *, seed: bool = True) -> list[dict]:
        if group is None or group == "__demo__":
            return []
        key = group
        data = _load_group_actions_map()
        if key not in data or not isinstance(data.get(key), list) or not data.get(key):
            if not seed:
                return []
            seeded = _seed_actions()
            data[key] = seeded
            _save_group_actions_map(data)
            return [dict(item) for item in seeded]
        return [_normalize_action(item) for item in data[key] if isinstance(item, dict)]

    def _set_actions_for_group(group: str, actions: list[dict]) -> None:
        data = _load_group_actions_map()
        data[group] = [_normalize_action(item) for item in actions]
        _save_group_actions_map(data)

    def _placeholder_names(command: str) -> list[str]:
        names: list[str] = []
        for match in _PLACEHOLDER_RE.finditer(command or ""):
            name = match.group(1)
            if name not in names:
                names.append(name)
        return names

    def _substitute_placeholders(command: str, values: dict[str, str]) -> str:
        def repl(match: re.Match) -> str:
            return values.get(match.group(1), match.group(0))

        return _PLACEHOLDER_RE.sub(repl, command or "")

    def _refresh_actions_menu() -> None:
        actions_menu.clear()
        group = landing_state["group"]
        if group in (None, "__demo__") or landing_state["bulk_busy"]:
            return
        for action in _actions_for_group(group):
            label = action["label"]
            act = actions_menu.addAction(label)
            act.triggered.connect(
                lambda _checked=False, a=dict(action): run_group_action(a)
            )
        actions_menu.addSeparator()
        edit_act = actions_menu.addAction("Edit actions…")
        edit_act.triggered.connect(_edit_group_actions_dialog)
        copy_act = actions_menu.addAction("Copy actions to groups…")
        copy_act.triggered.connect(_copy_group_actions_dialog)
        counts = section_counts()
        targets = [g for g in sorted(counts, key=lambda x: (x == "", x.casefold())) if g != group]
        copy_act.setEnabled(bool(targets) and bool(_actions_for_group(group)))

    def _edit_group_actions_dialog() -> None:
        group = landing_state["group"]
        if group in (None, "__demo__"):
            return
        dialog = EditGroupActionsDialog(
            root,
            group_name=group,
            actions=_actions_for_group(group),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            _set_actions_for_group(group, dialog.result_actions())
            _refresh_actions_menu()

    def _copy_group_actions_dialog() -> None:
        group = landing_state["group"]
        if group in (None, "__demo__") or favorites_repo is None:
            return
        actions = _actions_for_group(group)
        counts = section_counts()
        targets = tuple(
            g for g in sorted(counts, key=lambda x: (x == "", x.casefold())) if g != group
        )
        if not targets:
            console_append("  · no other groups to copy into", ok=False)
            return
        dialog = CopyGroupActionsDialog(
            root,
            source_group=group,
            actions=actions,
            target_groups=targets,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_actions()
        data = _load_group_actions_map()
        copied = 0
        skipped = 0
        for target in dialog.selected_groups():
            existing = [
                _normalize_action(item)
                for item in (data.get(target) or [])
                if isinstance(item, dict)
            ]
            labels = {(item.get("label") or "").casefold() for item in existing}
            for action in selected:
                label = str(action.get("label") or "").strip()
                if not label:
                    continue
                if label.casefold() in labels:
                    skipped += 1
                    continue
                existing.append(
                    _normalize_action({
                        "id": str(uuid.uuid4()),
                        "label": label,
                        "command": str(action.get("command") or "").strip(),
                    })
                )
                labels.add(label.casefold())
                copied += 1
            data[target] = existing
        _save_group_actions_map(data)
        console_append(
            f"  copied {copied} action(s)"
            + (f", skipped {skipped} duplicate label(s)" if skipped else ""),
            ok=True,
        )

    def run_group_action(action: dict) -> None:
        """Run one custom action across every repo in the selected group."""
        if landing_state["bulk_busy"]:
            return
        group = landing_state["group"]
        if group in (None, "__demo__"):
            return
        label = str(action.get("label") or "Action").strip() or "Action"
        command = str(action.get("command") or "").strip()
        if not command:
            console_append(f"  · “{label}” has an empty command", ok=False)
            return
        members = favorites_for_group()
        if not members:
            console_append("  · no repositories in this group", ok=False)
            return

        placeholders = _placeholder_names(command)
        values: dict[str, str] = {}
        if placeholders:
            dialog = ActionPlaceholdersDialog(
                root, action_label=label, placeholders=placeholders
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            values = dialog.values()
        resolved = _substitute_placeholders(command, values)

        paths = [favorite.ref for favorite in members]
        landing_state["bulk_busy"] = True
        bulk_queue[:] = list(paths)
        bulk_meta.update({
            "op": "group_action",
            "label": label,
            "command": resolved,
            "done": 0,
            "total": len(paths),
            "ok": 0,
        })
        _set_bulk_enabled(False)
        console_append(f"$ {resolved} — {len(paths)} repos in {group or 'Ungrouped'}")
        set_console_status(f"{label} 0/{len(paths)}…")
        _run_next_group_action()

    def _run_next_group_action() -> None:
        if not bulk_queue:
            _finish_group_action()
            return
        label = str(bulk_meta.get("label") or "Action")
        command = str(bulk_meta.get("command") or "")
        path = bulk_queue.pop(0)
        bulk_meta["done"] += 1
        set_console_status(f"{label} {bulk_meta['done']}/{bulk_meta['total']}…")
        console_append(f"$ {command} — {path}")
        _set_card_op_status(path, f"{label}…")

        worker = GitWorker("run_cmd", path, args=(command,), executable=git_exe())
        bulk_workers.append(worker)

        def done(result, current=worker, p=path, op_label=label) -> None:
            if current in bulk_workers:
                bulk_workers.remove(current)
            ok = bool(isinstance(result, dict) and result.get("ok"))
            output = str((result or {}).get("output") or "").strip() if isinstance(result, dict) else ""
            summary = next((line.strip() for line in output.splitlines() if line.strip()), "ok" if ok else "failed")
            console_append(f"  {'✓' if ok else '✕'} {summary[:200]}", ok=ok)
            if ok:
                bulk_meta["ok"] += 1
                _set_card_op_status(p, f"{op_label} · ok", ok=True)
            else:
                _set_card_op_status(p, f"{op_label} failed — {summary[:100]}", ok=False)
            refresh_card_status(p)
            _run_next_group_action()

        def failed(exc, current=worker, p=path, op_label=label) -> None:
            if current in bulk_workers:
                bulk_workers.remove(current)
            err = str(exc)
            console_append(f"  ✕ {err}", ok=False)
            _set_card_op_status(p, f"{op_label} failed — {err[:100]}", ok=False)
            _run_next_group_action()

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def _finish_group_action() -> None:
        label = str(bulk_meta.get("label") or "Action")
        ok_count = bulk_meta["ok"]
        total = bulk_meta["total"]
        failed = max(0, total - ok_count)
        landing_state["bulk_busy"] = False
        console_append(
            f"  {label} done — {ok_count}/{total} ok"
            + (f", {failed} failed" if failed else ""),
            ok=failed == 0,
        )
        set_console_status("idle")
        _set_bulk_enabled(
            landing_state["group"] not in (None, "__demo__")
            and bool(favorites_for_group())
        )

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
        _set_card_op_status(path, f"Running {op}…")

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
            _set_card_op_status(p, _summarize_op(operation, ok, output), ok=ok)
            if operation in ("fetch", "status"):
                refresh_card_status(p)
            _run_next_bulk()

        def failed(exc, current=worker, p=path, operation=op) -> None:
            if current in bulk_workers:
                bulk_workers.remove(current)
            console_append(f"  ✕ {exc}", ok=False)
            _set_card_op_status(p, _summarize_op(operation, False, ""), ok=False)
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
        """Render a status result as a compact card pill."""
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

    def _apply_remote_status(label, result: dict, path: str | None = None) -> None:
        """Fill a card's branch pill; plain folders stay as an empty reserved row."""
        try:
            if not result.get("is_repo"):
                label.setText("")
                label.setProperty("state", "")
                label.setToolTip("")
                label.style().unpolish(label)
                label.style().polish(label)
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
            label.setToolTip(text)
            label.style().unpolish(label)
            label.style().polish(label)
        except RuntimeError:
            pass  # the card was rebuilt while the worker ran

    def _card_remote_pill(path: str):
        """The branch/sync pill of the live card for ``path`` (None if gone)."""
        try:
            for i in range(favorites_list.count()):
                item = favorites_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) != path:
                    continue
                widget = favorites_list.itemWidget(item)
                if widget is None:
                    return None
                for label in widget.findChildren(QLabel):
                    if label.property("role") == "cardRemoteStatus":
                        return label
        except RuntimeError:
            return None
        return None

    def _render_card_statuses() -> None:
        """Push cached statuses onto the current cards (after a rebuild)."""
        try:
            for i in range(favorites_list.count()):
                item = favorites_list.item(i)
                path = item.data(Qt.ItemDataRole.UserRole)
                if not path:
                    continue
                result = status_cache.get(path)
                if result is None:
                    continue
                label = _card_remote_pill(path)
                if label is not None:
                    _apply_remote_status(label, result, path)
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
            label = _card_remote_pill(path)
            if label is not None:
                _apply_remote_status(label, result, path)

        def failed(_exc, current=worker) -> None:
            if current in status_workers:
                status_workers.remove(current)
            status_cache[path] = {
                "is_repo": False, "branch": "", "ahead": 0, "behind": 0, "upstream": None,
            }
            label = _card_remote_pill(path)
            if label is not None:
                try:
                    label.setText("")
                    label.setProperty("state", "")
                    label.setToolTip("")
                    label.style().unpolish(label)
                    label.style().polish(label)
                except RuntimeError:
                    pass

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def refresh_all_statuses() -> None:
        """Kick off a status refresh for every non-demo card on the landing."""
        try:
            gen = int(landing_state.get("status_gen") or 0)
            paths: list[str] = []
            for i in range(favorites_list.count()):
                item = favorites_list.item(i)
                path = item.data(Qt.ItemDataRole.UserRole)
                widget = favorites_list.itemWidget(item)
                if widget is None or not path:
                    continue
                paths.append(path)
            # Stagger starts so the UI stays scrollable while pills fill in.
            for index, path in enumerate(paths):
                QTimer.singleShot(
                    index * 12,
                    lambda p=path, g=gen: (
                        refresh_card_status(p)
                        if landing_state.get("status_gen") == g
                        else None
                    ),
                )
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
            service.set("git.home.branch", branch_combo.currentText().strip())
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
            prefer_branch = str(service.get("git.home.branch") or "")
            raw_tabs = service.get("git.home.tabs") or "[]"
            try:
                open_paths = json.loads(raw_tabs) if isinstance(raw_tabs, str) else []
            except (TypeError, ValueError):
                open_paths = []
        except Exception:  # noqa: BLE001 — a bad saved value must never crash the view
            logger.exception("failed to restore git home view state")
            return
        _refresh_branch_combo(prefer=prefer_branch)
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
        refresh_repo_cards()
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
    branch_fetch.clicked.connect(run_branch_fetch)
    branch_edit.clicked.connect(_edit_branches_dialog)
    branch_combo.currentTextChanged.connect(lambda _text: persist_timer.start())
    console_toggle.clicked.connect(toggle_console)
    console_clear.clicked.connect(clear_console)
    tabs.tabCloseRequested.connect(close_tab)
    tabs.currentChanged.connect(lambda _index: _persist_view_state())

    _refresh_branch_combo()
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
