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
import subprocess
import time
import uuid
from typing import Any

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, QThreadPool, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
    RepoFilesDialog,
    ScanReposDialog,
    rename_favorite_group,
)
from devworkbench.modules.git.maven_deps import build_maven_deps_pane
from devworkbench.modules.git.maven_tree import build_maven_tree_pane
from devworkbench.services.configuration_service import TOPIC_GIT_OPEN_GROUP
from devworkbench.ui.samples import GIT_REPOS
from devworkbench.ui.theme import current_colors
from devworkbench.ui.widgets.common import button, clear_list_widget, icon_button, search_field, styled_label
from devworkbench.workers.git_worker import GitWorker

logger = logging.getLogger("devworkbench.modules.git")

# Fixed card height keeps the grid scroll smooth while status / toasts update.
# Width is shared evenly across 1–2 QGridLayout columns (not IconMode).
_CARD_HEIGHT = 186
_CARD_MIN_WIDTH = 280
# Cards materialized per batch on the landing. The full filtered member list
# lives in ``landing_state["members"]``; only this many card widgets exist at
# a time, and scrolling near the bottom loads the next batch (lazy
# pagination for very large groups).
_LANDING_CHUNK = 80


def _repo_initials(label: str) -> str:
    """Two-letter avatar initials from a repo name or path basename."""
    base = (label or "").strip() or "?"
    parts = [p for p in re.split(r"[-_\s.]+", base) if p]
    if len(parts) >= 2:
        return (parts[0][:1] + parts[1][:1]).upper()
    return base[:2].upper()


_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
_DEFAULT_GROUP_ACTIONS = (
    ("Add", "git add ."),
    ("Commit", 'git commit -m "{{message}}"'),
    ("Push", "git push"),
    ("Branch", "git checkout -b {{branch}}"),
)
# The exact default set before "Branch" existed — groups seeded before the
# upgrade keep the old three actions in storage, so ``_actions_for_group``
# migrates an untouched legacy set to the current defaults instead of
# silently missing the new option.
_LEGACY_DEFAULT_ACTIONS = (
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


class DriftBar(QWidget):
    """Tiny 3-segment health bar for a group row: clean / ahead / behind.

    The signature element of the landing — fleet health at a glance, using
    the app's semantic duotone (green = clean, amber = your commits ahead,
    cyan = upstream commits behind). Diverged repos count toward both amber
    and cyan, which is literally what ``git status`` reports.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("groupDriftBar")
        self.setFixedSize(64, 6)
        self._segments = (0, 0, 0)  # clean, ahead, behind
        self.setToolTip("")

    def set_segments(self, clean: int, ahead: int, behind: int) -> None:
        self._segments = (max(0, clean), max(0, ahead), max(0, behind))
        total = sum(self._segments)
        if total:
            self.setToolTip(
                f"{clean} clean · {ahead} ahead · {behind} behind"
            )
        else:
            self.setToolTip("")
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = current_colors()
        track = QColor(colors["surface2"])
        clean, ahead, behind = self._segments
        total = clean + ahead + behind
        w, h, gap = self.width(), self.height(), 2
        radius = h / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(self.rect(), radius, radius)
        if total == 0:
            return
        usable = w - gap * 2
        widths = [count / total * usable for count in (clean, ahead, behind)]
        x = 0.0
        for count, width, token in zip(
            (clean, ahead, behind), widths, ("green", "amber", "cyan")
        ):
            if count <= 0 or width <= 0:
                continue
            painter.setBrush(QColor(colors[token]))
            painter.drawRoundedRect(QRectF(x, 0.0, width, h), radius, radius)
            x += width + gap
        painter.end()


def build_view(icons, ctx=None) -> QWidget:
    service = ctx.resolve("services.configuration") if ctx is not None and ctx.has("services.configuration") else None
    favorites_repo = ctx.resolve("database.repositories.favorites") if ctx is not None and ctx.has("database.repositories.favorites") else None
    history_repo = ctx.resolve("database.repositories.history") if ctx is not None and ctx.has("database.repositories.history") else None
    events = ctx.resolve("core.events") if ctx is not None and ctx.has("core.events") else None
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
    groups_heading.setObjectName("groupsHeading")
    _bump_font(groups_heading, 0, 12, bold=True)
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

    # The two action rows read as one “command deck” (see QSS #commandDeck).
    command_deck = QWidget()
    command_deck.setObjectName("commandDeck")
    command_deck_layout = QVBoxLayout(command_deck)
    command_deck_layout.setContentsMargins(8, 8, 8, 8)
    command_deck_layout.setSpacing(6)

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
    command_deck_layout.addWidget(branch_bar)

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
    refresh_status = button("Refresh status", "ghost")
    refresh_status.setObjectName("refreshStatusButton")
    refresh_status.setToolTip(
        "Fetch branch + ahead/behind for every repo in this group (on demand)"
    )
    _bump_font(refresh_status, 2, 13)
    last_refresh_label = styled_label("", "hint")
    last_refresh_label.setObjectName("lastRefreshLabel")
    last_refresh_label.setProperty("role", "lastRefresh")
    _bump_font(last_refresh_label, 1, 11)
    last_refresh_label.hide()
    bulk_busy_label = styled_label("", "hint")
    bulk_busy_label.setObjectName("bulkBusyLabel")
    _bump_font(bulk_busy_label, 2, 13)
    bulk_busy_label.hide()
    bulk_layout.addWidget(bulk_fetch)
    bulk_layout.addWidget(bulk_status)
    bulk_layout.addWidget(bulk_reset)
    bulk_layout.addWidget(refresh_status)
    bulk_layout.addWidget(last_refresh_label)
    bulk_layout.addWidget(bulk_busy_label)
    bulk_layout.addStretch(1)
    command_deck_layout.addWidget(bulk_bar)
    repos_pane_layout.addWidget(command_deck)

    favorites_scroll = QScrollArea()
    favorites_scroll.setObjectName("favoritesList")
    favorites_scroll.setWidgetResizable(True)
    favorites_scroll.setFrameStyle(0)
    favorites_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    favorites_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    favorites_scroll.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    favorites_host = QWidget()
    favorites_host.setObjectName("favoritesHost")
    favorites_grid = QGridLayout(favorites_host)
    favorites_grid.setContentsMargins(4, 4, 4, 4)
    favorites_grid.setHorizontalSpacing(12)
    favorites_grid.setVerticalSpacing(12)
    favorites_grid.setAlignment(
        Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
    )
    favorites_scroll.setWidget(favorites_host)
    repos_pane_layout.addWidget(favorites_scroll, 1)

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
    landing_state: dict[str, Any] = {
        "group": None,
        "console_open": False,
        "bulk_busy": False,
        "refreshing": False,
        "chunk": 0,       # how many lazy batches have been materialized
        "members": [],    # full filtered member list for the current group
        "status_refreshing": False,  # on-demand status refresh in flight
    }
    # Per-group epoch (seconds) of the last on-demand status refresh, loaded
    # from ``git.home.last_refresh`` so the deck timestamp survives restarts.
    last_refresh_map: dict[str, float] = {}
    last_refresh_loaded = False
    # Last known remote status per repo path, rendered onto fresh cards after
    # a rebuild; refreshed on demand, after each fetch and on a timer.
    status_cache: dict[str, dict] = {}
    # Per-path epoch of the last successful remote-status fill (card footer).
    status_times: dict[str, float] = {}
    status_workers: list = []
    bulk_workers: list = []
    bulk_queue: list = []  # remaining paths for the active bulk run
    bulk_meta = {"op": "", "done": 0, "total": 0, "ok": 0}
    # Ordered paths + widgets for the QGridLayout card grid (avoids IconMode overlap).
    card_order: list[str] = []
    card_by_path: dict[str, QWidget] = {}

    def _grid_column_count() -> int:
        width = max(1, favorites_scroll.viewport().width())
        gap = max(0, favorites_grid.horizontalSpacing())
        return 2 if width >= (_CARD_MIN_WIDTH * 2 + gap * 3) else 1

    def _relayout_card_grid() -> None:
        """Place cards into 1–2 equal columns without overlapping."""
        cols = _grid_column_count()
        widgets = [card_by_path[p] for p in card_order if p in card_by_path]
        while favorites_grid.count():
            favorites_grid.takeAt(0)
        for index, widget in enumerate(widgets):
            favorites_grid.addWidget(widget, index // cols, index % cols)
        favorites_grid.setColumnStretch(0, 1)
        favorites_grid.setColumnStretch(1, 1 if cols > 1 else 0)

    def _clear_cards() -> None:
        """Destroy every repo card (equivalent of clear_list_widget for the grid)."""
        for path in list(card_order):
            widget = card_by_path.pop(path, None)
            if widget is None:
                continue
            favorites_grid.removeWidget(widget)
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        card_order.clear()
        while favorites_grid.count():
            favorites_grid.takeAt(0)

    class _FavoritesResizeFilter(QObject):
        def eventFilter(self, obj, event):  # noqa: N802
            if event.type() == QEvent.Type.Resize:
                QTimer.singleShot(0, _relayout_card_grid)
            return False

    _favorites_resize_filter = _FavoritesResizeFilter(favorites_scroll)
    favorites_scroll.viewport().installEventFilter(_favorites_resize_filter)

    def _repolish(widget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    def card_widget(path: str, label: str, demo: bool) -> QWidget:
        """Vertical repo card: avatar, branch, action chips, sync footer."""
        display = label or os.path.basename(path.rstrip("/")) or path
        card = QWidget()
        card.setObjectName("repoCard")
        card.setProperty("repoPath", path)
        card.setMinimumHeight(_CARD_HEIGHT)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root = QVBoxLayout(card)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)

        avatar = QLabel(_repo_initials(display))
        avatar.setObjectName("repoAvatar")
        avatar.setProperty("tone", str(sum(ord(c) for c in display) % 5))
        avatar.setFixedSize(40, 40)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _bump_font(avatar, 1, 12, bold=True)
        _repolish(avatar)
        head.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        name = QLabel(display)
        name.setObjectName("repoName")
        _bump_font(name, 2, 14, bold=True)
        name.setWordWrap(False)
        title_col.addWidget(name)

        path_text = QLabel()
        path_text.setObjectName("repoPath")
        path_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_text.setToolTip(path)
        path_text.setWordWrap(False)
        _bump_font(path_text, 0, 11)
        metrics = path_text.fontMetrics()
        path_text.setText(metrics.elidedText(path, Qt.TextElideMode.ElideMiddle, 220))
        path_text.setFixedHeight(metrics.height() + 2)
        title_col.addWidget(path_text)
        head.addLayout(title_col, 1)

        branch_pill = QLabel("—")
        branch_pill.setObjectName("cardBranch")
        branch_pill.setProperty("role", "cardBranch")
        branch_pill.setWordWrap(False)
        branch_pill.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        branch_pill.setMaximumWidth(150)
        _bump_font(branch_pill, 0, 11)
        head.addWidget(branch_pill, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        chips = QGridLayout()
        chips.setContentsMargins(0, 2, 0, 0)
        chips.setHorizontalSpacing(6)
        chips.setVerticalSpacing(6)

        def _chip(text: str, kind: str) -> QPushButton:
            btn = QPushButton(text)
            btn.setObjectName("cardChip")
            btn.setProperty("kind", kind)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            _bump_font(btn, 0, 11)
            _repolish(btn)
            return btn

        open_btn = _chip("Open", "primary")
        open_btn.clicked.connect(lambda _checked=False, p=path: open_folder(p, demo=demo))
        chips.addWidget(open_btn, 0, 0)

        if demo:
            demo_pill = QLabel("demo")
            demo_pill.setObjectName("statusPill")
            demo_pill.setProperty("state", "warn")
            _bump_font(demo_pill, 0, 11)
            chips.addWidget(demo_pill, 0, 1)
        else:
            vscode_btn = _chip("VS Code", "accent")
            vscode_btn.setToolTip("Open in VS Code")
            vscode_btn.clicked.connect(lambda _checked=False, p=path: open_in_vscode(p))
            chips.addWidget(vscode_btn, 0, 1)

            finder_btn = _chip("Finder", "ghost")
            finder_btn.setToolTip("Reveal in Finder")
            finder_btn.clicked.connect(lambda _checked=False, p=path: reveal_in_finder(p))
            chips.addWidget(finder_btn, 0, 2)

            terminal_btn = _chip("Terminal", "ghost")
            terminal_btn.setToolTip("Open in Terminal")
            terminal_btn.clicked.connect(lambda _checked=False, p=path: open_in_terminal(p))
            chips.addWidget(terminal_btn, 1, 0)

            edit_btn = _chip("Edit", "ghost")
            edit_btn.setToolTip("Edit repository")
            edit_btn.clicked.connect(lambda _checked=False, p=path: edit_repository(p))
            chips.addWidget(edit_btn, 1, 1)

            unpin_btn = _chip("Unpin", "danger")
            unpin_btn.setToolTip("Remove from favorites")
            unpin_btn.clicked.connect(lambda _checked=False, p=path: remove_favorite(p))
            chips.addWidget(unpin_btn, 1, 2)

        root.addLayout(chips)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 4, 0, 0)
        foot.setSpacing(8)
        # Sync summary (Clean / Ahead / Behind) — filled by Refresh status.
        remote_pill = QLabel("…")
        remote_pill.setObjectName("cardRemoteStatus")
        remote_pill.setProperty("role", "cardRemoteStatus")
        remote_pill.setProperty("state", "")
        remote_pill.setWordWrap(False)
        _bump_font(remote_pill, 1, 12)
        foot.addWidget(remote_pill, 0, Qt.AlignmentFlag.AlignLeft)

        # Local working-tree dirty count — click opens Local changes modal.
        changes_btn = QPushButton("")
        changes_btn.setObjectName("cardLocalChanges")
        changes_btn.setProperty("role", "cardLocalChanges")
        changes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        changes_btn.setToolTip("Local working-tree changes — click to view")
        changes_btn.setFlat(True)
        changes_btn.hide()
        _bump_font(changes_btn, 1, 12)
        changes_btn.clicked.connect(
            lambda _checked=False, p=path, n=display: open_repo_files_dialog(
                p, n, RepoFilesDialog.MODE_LOCAL
            )
        )
        foot.addWidget(changes_btn, 0, Qt.AlignmentFlag.AlignLeft)

        # Brief op toast (checkout/fetch feedback) — sits between status and stamp.
        status_label = styled_label("", "hint")
        status_label.setProperty("role", "cardStatus")
        status_label.setWordWrap(False)
        _bump_font(status_label, 0, 11)
        foot.addWidget(status_label, 1)

        updated = QLabel("")
        updated.setObjectName("cardUpdated")
        updated.setProperty("role", "cardUpdated")
        updated.setWordWrap(False)
        updated.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        _bump_font(updated, 0, 11)
        foot.addWidget(updated, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(foot)
        return card

    def add_card(path: str, label: str, demo: bool) -> None:
        widget = card_widget(path, label, demo)
        card_order.append(path)
        card_by_path[path] = widget
        cols = _grid_column_count()
        index = len(card_order) - 1
        favorites_grid.addWidget(widget, index // cols, index % cols)
        favorites_grid.setColumnStretch(0, 1)
        favorites_grid.setColumnStretch(1, 1 if cols > 1 else 0)

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
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(8)
        count_label = styled_label(f"{count} {noun}", "hint")
        _bump_font(count_label, 1, 11)
        count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        meta_row.addWidget(count_label)
        drift_bar = DriftBar()
        drift_bar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        meta_row.addWidget(drift_bar)
        meta_row.addStretch(1)
        text_col.addLayout(meta_row)
        row_layout.addLayout(text_col, 1)
        item.setSizeHint(QSize(200, 56))
        groups_list.addItem(item)
        groups_list.setItemWidget(item, row)
        row.clicked.connect(lambda _checked=False, k=key: open_group(k))
        row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        row.customContextMenuRequested.connect(
            lambda pos, k=key, btn=row: show_group_context_menu(btn.mapToGlobal(pos), k)
        )

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

    def _load_last_refresh_map() -> dict:
        if service is None:
            return {}
        try:
            raw = str(service.get("git.home.last_refresh") or "{}")
            data = json.loads(raw) if isinstance(raw, str) else {}
            return data if isinstance(data, dict) else {}
        except Exception:
            logger.exception("failed to load git.home.last_refresh")
            return {}

    def _save_last_refresh_map() -> None:
        if service is None:
            return
        try:
            service.set("git.home.last_refresh", json.dumps(last_refresh_map))
        except Exception:
            logger.exception("failed to save git.home.last_refresh")

    def _relative_refresh_text(epoch: float) -> str:
        """"Updated 2 min ago"-style text for a stored refresh epoch."""
        delta = max(0, int(time.time() - epoch))
        if delta < 60:
            return "updated just now"
        if delta < 3600:
            minutes = delta // 60
            return f"updated {minutes} min ago" if minutes > 1 else "updated 1 min ago"
        if delta < 86400:
            hours = delta // 3600
            return f"updated {hours} hr ago" if hours > 1 else "updated 1 hr ago"
        days = delta // 86400
        return f"updated {days} days ago" if days > 1 else "updated 1 day ago"

    def _show_last_refresh(group: str | None) -> None:
        """Show the deck timestamp for ``group`` if a refresh was ever done."""
        epoch = last_refresh_map.get(str(group or "")) if group is not None else None
        if epoch:
            last_refresh_label.setText(_relative_refresh_text(float(epoch)))
            last_refresh_label.show()
        else:
            last_refresh_label.hide()
        _render_card_updated_labels()

    def _updated_text_for_path(path: str) -> str:
        """Relative 'updated … ago' for a card — per-repo stamp, else group."""
        epoch = status_times.get(path)
        if epoch is None:
            group = landing_state.get("group")
            if group is not None and group != "__demo__":
                epoch = last_refresh_map.get(str(group))
        if not epoch:
            return ""
        return _relative_refresh_text(float(epoch))

    def _card_updated_label(path: str):
        try:
            widget = card_by_path.get(path)
            if widget is None:
                return None
            for label in widget.findChildren(QLabel):
                if label.property("role") == "cardUpdated":
                    return label
        except RuntimeError:
            return None
        return None

    def _apply_card_updated(path: str) -> None:
        label = _card_updated_label(path)
        if label is None:
            return
        try:
            label.setText(_updated_text_for_path(path))
        except RuntimeError:
            pass

    def _render_card_updated_labels() -> None:
        """Refresh every visible card's bottom-right updated stamp."""
        try:
            for path in card_order:
                _apply_card_updated(path)
        except RuntimeError:
            pass

    def _set_bulk_enabled(enabled: bool) -> None:
        bulk_fetch.setEnabled(enabled)
        bulk_status.setEnabled(enabled)
        bulk_reset.setEnabled(enabled)
        refresh_status.setEnabled(enabled and not landing_state.get("status_refreshing"))
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
            manage_button.setEnabled(bool(favorites))
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
        """Rebuild only the right-hand repo list for the current group.

        The full filtered member list is stored in ``landing_state["members"]``
        but only the first chunk of cards is materialized; scrolling near the
        bottom loads more (see ``_render_next_chunk`` / ``_maybe_load_more``).
        That keeps very large groups (1000+ repos) responsive — a keystroke in
        the search field rebuilds only the visible chunk, not every card.
        """
        if landing_state.get("refreshing"):
            return
        landing_state["refreshing"] = True
        favorites_host.setUpdatesEnabled(False)
        try:
            group = landing_state["group"]
            _clear_cards()
            landing_state["chunk"] = 0
            landing_state["members"] = []
            # Show the last-refresh timestamp for this group (persisted per
            # group, so it survives restarts); none yet → stays hidden.
            _show_last_refresh(group)

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
            landing_state["members"] = members
            _render_next_chunk()
            _set_bulk_enabled(not landing_state["bulk_busy"])
            _update_group_drift_bars()
        finally:
            favorites_host.setUpdatesEnabled(True)
            landing_state["refreshing"] = False
            QTimer.singleShot(0, _relayout_card_grid)

    def _render_next_chunk() -> None:
        """Materialize the next batch of repo cards (lazy pagination)."""
        members = landing_state.get("members") or []
        start = int(landing_state.get("chunk") or 0) * _LANDING_CHUNK
        if start >= len(members):
            return
        end = min(start + _LANDING_CHUNK, len(members))
        for favorite in members[start:end]:
            add_card(favorite.ref, favorite.label, demo=False)
        landing_state["chunk"] = int(landing_state.get("chunk") or 0) + 1
        # Push cached statuses onto the freshly added cards so their pills are
        # populated without a separate worker round-trip.
        _render_card_statuses()
        # A short list (or a viewport already pinned to the bottom) finishes
        # loading without waiting for another scroll event.
        _maybe_load_more()

    def _maybe_load_more() -> None:
        """Append the next chunk when the user has scrolled near the bottom."""
        if landing_state.get("refreshing"):
            return
        group = landing_state["group"]
        if group in (None, "__demo__") or favorites_repo is None:
            return
        scrollbar = favorites_scroll.verticalScrollBar()
        if scrollbar.maximum() - scrollbar.value() <= 60:
            QTimer.singleShot(0, _render_next_chunk)

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
        if key == landing_state["group"] and card_order:
            # Already showing this group — still sync the checked row state.
            _sync_group_checked(key)
            return
        landing_state["group"] = key
        _sync_group_checked(key)
        # Only rebuild the repo pane — rebuilding groups on every click was janky.
        # No git work here: with very large groups nothing hits the remote
        # until the user explicitly asks (Status all, per-card refresh, fetch).
        refresh_repo_cards()
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

    def show_group_context_menu(global_pos, key: str) -> None:
        """Right-click on a left-rail group row: Rename (incl. Ungrouped) + Manage."""
        menu = QMenu(root)
        if key != "__demo__":
            rename_act = menu.addAction("Rename group…")
            rename_act.triggered.connect(lambda _c=False, k=key: rename_group_prompt(k))
            menu.addSeparator()
        manage_act = menu.addAction("Manage groups…")
        manage_act.triggered.connect(manage_groups)
        menu.popup(global_pos)

    def rename_group_prompt(key: str) -> None:
        """Ask for a new name and rename all favorites in that group."""
        if favorites_repo is None or key == "__demo__":
            return
        display = key or "Ungrouped"
        new_name, ok = QInputDialog.getText(
            root,
            "Rename group",
            f"Rename “{display}” to:",
            text=key,
        )
        if not ok:
            return
        error = rename_favorite_group(favorites_repo, key, new_name)
        if error:
            QMessageBox.warning(root, "Rename group", error)
            return
        new_name = new_name.strip()
        if landing_state.get("group") == key:
            landing_state["group"] = new_name
            if service is not None:
                try:
                    service.set("git.home.group", new_name)
                except Exception:  # noqa: BLE001
                    pass
        # Preserve last-refresh stamp under the new key when possible.
        if key in last_refresh_map:
            last_refresh_map[new_name] = last_refresh_map.pop(key)
            _save_last_refresh_map()
        refresh_favorites()

    def open_repo_files_dialog(path: str, repo_name: str, mode: str) -> None:
        """Open Local changes or Diff vs remote modal for one repository card."""
        if not path or not os.path.isdir(path):
            return
        dialog = RepoFilesDialog(
            root,
            path=path,
            repo_name=repo_name or os.path.basename(path.rstrip("/")) or path,
            mode=mode,
            git_executable=git_exe(),
            pending_workers=status_workers,
        )
        dialog.exec()

    def show_card_menu(position) -> None:
        """Right-click menu on a repository card: group actions + Open / Edit / Remove.

        The custom Actions configured for the selected group appear first so a
        single repository can run them on its own — the same command set as the
        top Actions button, just scoped to that card. Built with the
        non-blocking ``popup`` pattern — every action calls the same handler as
        the card's buttons, so the behavior is identical while staying testable
        (no modal ``exec`` loop).
        """
        host_pos = favorites_host.mapFrom(favorites_scroll.viewport(), position)
        child = favorites_host.childAt(host_pos)
        card = child
        while card is not None and card.objectName() != "repoCard":
            card = card.parentWidget()
        if card is None:
            return
        path = card.property("repoPath")
        if not path:
            return
        menu = QMenu(favorites_scroll)
        # Same custom actions as the top Actions button, scoped to this repo.
        busy = landing_state["bulk_busy"]
        group = landing_state["group"]
        for action in _actions_for_group(group if isinstance(group, str) else None):
            act = menu.addAction(str(action.get("label") or "Action"))
            act.setEnabled(not busy)
            act.triggered.connect(
                lambda _checked=False, a=dict(action), p=path: run_card_action(a, p)
            )
        # Same Packs as Actions ▾ — scoped to this repository only.
        packs = menu.addMenu("Packs")
        packs.setEnabled(not busy)
        pack_act = packs.addAction("Add → Commit → Push")
        pack_act.setToolTip(
            "git add . · commit with one message · git push — this repository only"
        )
        pack_act.setEnabled(not busy)
        pack_act.triggered.connect(
            lambda _checked=False, p=path: run_card_pack_add_commit_push(p)
        )
        if menu.actions():
            menu.addSeparator()
        menu.addAction("Local changes…").triggered.connect(
            lambda _checked=False, p=path: open_repo_files_dialog(
                p, os.path.basename(p.rstrip("/")) or p, RepoFilesDialog.MODE_LOCAL
            )
        )
        menu.addAction("Diff vs remote…").triggered.connect(
            lambda _checked=False, p=path: open_repo_files_dialog(
                p, os.path.basename(p.rstrip("/")) or p, RepoFilesDialog.MODE_REMOTE
            )
        )
        menu.addSeparator()
        menu.addAction("Open").triggered.connect(
            lambda _checked=False, p=path: open_folder(p)
        )
        menu.addAction("Open in VS Code").triggered.connect(
            lambda _checked=False, p=path: open_in_vscode(p)
        )
        menu.addAction("Reveal in Finder").triggered.connect(
            lambda _checked=False, p=path: reveal_in_finder(p)
        )
        menu.addAction("Open in Terminal").triggered.connect(
            lambda _checked=False, p=path: open_in_terminal(p)
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
        menu.popup(favorites_scroll.viewport().mapToGlobal(position))

    def open_in_vscode(path: str) -> None:
        """Open the repository folder in Visual Studio Code (``code`` CLI or .app)."""
        if not path or not os.path.isdir(path):
            console_append(f"  · path not found: {path}", ok=False)
            return
        # Prefer `code` on PATH (VS Code / Cursor shell command).
        try:
            proc = subprocess.run(
                ["code", path],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode == 0:
                console_append(f"  ✓ opened in VS Code — {path}", ok=True)
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
        # macOS app bundle fallbacks
        for app in (
            "Visual Studio Code",
            "Visual Studio Code - Insiders",
            "Code",
        ):
            try:
                proc = subprocess.run(
                    ["open", "-na", app, "--args", path],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if proc.returncode == 0:
                    console_append(f"  ✓ opened in {app} — {path}", ok=True)
                    return
            except (OSError, subprocess.TimeoutExpired):
                continue
        console_append(
            "  ✕ could not open VS Code — install the “code” shell command "
            "(VS Code → Command Palette → “Shell Command: Install 'code' command in PATH”)",
            ok=False,
        )

    def reveal_in_finder(path: str) -> None:
        """Reveal the repository folder in Finder."""
        if not path or not os.path.isdir(path):
            console_append(f"  · path not found: {path}", ok=False)
            return
        try:
            subprocess.run(["open", path], check=False, timeout=15)
            console_append(f"  ✓ revealed in Finder — {path}", ok=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            console_append(f"  ✕ Finder: {exc}", ok=False)

    def open_in_terminal(path: str) -> None:
        """Open a Terminal window with cwd set to the repository."""
        if not path or not os.path.isdir(path):
            console_append(f"  · path not found: {path}", ok=False)
            return
        # Pass path as argv so spaces/quotes are safe.
        script = (
            "on run argv\n"
            "  set p to item 1 of argv\n"
            '  tell application "Terminal"\n'
            "    activate\n"
            '    do script "cd " & quoted form of p\n'
            "  end tell\n"
            "end run"
        )
        try:
            proc = subprocess.run(
                ["osascript", "-e", script, path],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode == 0:
                console_append(f"  ✓ opened Terminal — {path}", ok=True)
            else:
                err = (proc.stderr or proc.stdout or "failed").strip()
                console_append(f"  ✕ Terminal: {err[:160]}", ok=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            console_append(f"  ✕ Terminal: {exc}", ok=False)

    def remove_favorite(path: str) -> None:
        if favorites_repo is not None:
            favorites_repo.remove_ref("folder", path)
        status_cache.pop(path, None)  # don't keep a stale entry for unpinned repos
        status_times.pop(path, None)
        refresh_favorites()

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
            widget = card_by_path.get(path)
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
        stored = data.get(key)
        if not isinstance(stored, list) or not stored:
            if not seed:
                return []
            seeded = _seed_actions()
            data[key] = seeded
            _save_group_actions_map(data)
            return [dict(item) for item in seeded]
        items = [_normalize_action(item) for item in stored if isinstance(item, dict)]
        # Upgrade groups still holding the pre-Branch default set so the new
        # option appears without the user re-editing their actions.
        if items and [(a.get("label"), a.get("command")) for a in items] == list(
            _LEGACY_DEFAULT_ACTIONS
        ):
            seeded = _seed_actions()
            data[key] = seeded
            _save_group_actions_map(data)
            return [dict(item) for item in seeded]
        return items

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
        packs = actions_menu.addMenu("Packs")
        pack_act = packs.addAction("Add → Commit → Push")
        pack_act.setToolTip(
            "git add . · commit with one message · git push — for every repo in this group"
        )
        pack_act.triggered.connect(run_pack_add_commit_push)
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

    def run_pack_add_commit_push() -> None:
        """Built-in pack: git add . → commit (one message) → push per group repo."""
        if landing_state["bulk_busy"]:
            return
        group = landing_state["group"]
        if group in (None, "__demo__"):
            return
        members = favorites_for_group()
        if not members:
            console_append("  · no repositories in this group", ok=False)
            return
        _start_pack_add_commit_push(
            [favorite.ref for favorite in members],
            scope=f"{len(members)} repos in {group or 'Ungrouped'}",
        )

    def run_card_pack_add_commit_push(path: str) -> None:
        """Same Add→Commit→Push pack as the group menu, scoped to one repo card."""
        if landing_state["bulk_busy"]:
            return
        if not path or not os.path.isdir(path):
            console_append(f"  · path not found: {path}", ok=False)
            return
        name = os.path.basename(path.rstrip("/")) or path
        _start_pack_add_commit_push([path], scope=name)

    def _start_pack_add_commit_push(paths: list[str], *, scope: str) -> None:
        """Prompt for commit message, then run add → commit → push on ``paths``."""
        if not paths:
            return
        dialog = ActionPlaceholdersDialog(
            root, action_label="Add → Commit → Push", placeholders=["message"]
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        message = dialog.values().get("message", "").strip()
        if not message:
            return
        # Escape for shell-style argv via shlex in run_cmd — use simple quoting.
        safe_msg = message.replace('"', '\\"')
        steps = [
            "git add .",
            f'git commit -m "{safe_msg}"',
            "git push",
        ]
        landing_state["bulk_busy"] = True
        bulk_queue[:] = list(paths)
        bulk_meta.update({
            "op": "action_pack",
            "label": "Add → Commit → Push",
            "steps": steps,
            "step_index": 0,
            "pack_path": "",
            "done": 0,
            "total": len(paths),
            "ok": 0,
        })
        _set_bulk_enabled(False)
        console_append(f"$ pack Add→Commit→Push — {scope}")
        set_console_status(f"pack 0/{len(paths)}…")
        _run_next_group_action()

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

    def run_card_action(action: dict, path: str) -> None:
        """Run one custom action against a single repository (card menu).

        Reuses the sequential bulk queue with a one-item run so the busy lock,
        card feedback, console lines and the ``N/M ok`` summary behave exactly
        like the group-wide Actions run — just scoped to one path.
        """
        if landing_state["bulk_busy"]:
            return
        label = str(action.get("label") or "Action").strip() or "Action"
        command = str(action.get("command") or "").strip()
        if not command:
            console_append(f"  · “{label}” has an empty command", ok=False)
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

        landing_state["bulk_busy"] = True
        bulk_queue[:] = [path]
        bulk_meta.update({
            "op": "card_action",
            "label": label,
            "command": resolved,
            "done": 0,
            "total": 1,
            "ok": 0,
        })
        _set_bulk_enabled(False)
        # The per-repo ``$ command — path`` line is logged by the queue runner.
        set_console_status(f"{label} 0/1…")
        _run_next_group_action()

    def _run_next_group_action() -> None:
        if not bulk_queue and not (
            bulk_meta.get("op") == "action_pack" and bulk_meta.get("pack_path")
        ):
            _finish_group_action()
            return

        # Multi-step pack: finish steps for current pack_path before next repo.
        if bulk_meta.get("op") == "action_pack":
            _run_next_pack_step()
            return

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

    def _commit_nothing_to_commit(output: str) -> bool:
        text = (output or "").lower()
        return (
            "nothing to commit" in text
            or "no changes added to commit" in text
            or "working tree clean" in text
        )

    def _run_next_pack_step() -> None:
        steps = list(bulk_meta.get("steps") or [])
        path = str(bulk_meta.get("pack_path") or "")
        step_index = int(bulk_meta.get("step_index") or 0)
        label = str(bulk_meta.get("label") or "Pack")

        if not path:
            if not bulk_queue:
                _finish_group_action()
                return
            path = bulk_queue.pop(0)
            bulk_meta["pack_path"] = path
            bulk_meta["step_index"] = 0
            bulk_meta["done"] += 1
            step_index = 0
            set_console_status(f"{label} {bulk_meta['done']}/{bulk_meta['total']}…")
            _set_card_op_status(path, f"{label}…")

        if step_index >= len(steps):
            bulk_meta["ok"] += 1
            _set_card_op_status(path, f"{label} · ok", ok=True)
            refresh_card_status(path)
            bulk_meta["pack_path"] = ""
            bulk_meta["step_index"] = 0
            _run_next_pack_step()
            return

        command = str(steps[step_index])
        console_append(f"$ {command} — {path}")
        worker = GitWorker("run_cmd", path, args=(command,), executable=git_exe())
        bulk_workers.append(worker)

        def done(result, current=worker, p=path, cmd=command, idx=step_index) -> None:
            if current in bulk_workers:
                bulk_workers.remove(current)
            ok = bool(isinstance(result, dict) and result.get("ok"))
            output = str((result or {}).get("output") or "").strip() if isinstance(result, dict) else ""
            summary = next(
                (line.strip() for line in output.splitlines() if line.strip()),
                "ok" if ok else "failed",
            )
            soft_ok = (not ok) and cmd.startswith("git commit") and _commit_nothing_to_commit(output)
            if ok or soft_ok:
                console_append(
                    f"  ✓ {summary[:200]}" + (" (nothing to commit)" if soft_ok else ""),
                    ok=True,
                )
                bulk_meta["step_index"] = idx + 1
                _run_next_pack_step()
                return
            console_append(f"  ✕ {summary[:200]}", ok=False)
            _set_card_op_status(p, f"{label} failed — {summary[:100]}", ok=False)
            refresh_card_status(p)
            # Abort remaining steps for this repo; continue with next repo.
            bulk_meta["pack_path"] = ""
            bulk_meta["step_index"] = 0
            _run_next_pack_step()

        def failed(exc, current=worker, p=path) -> None:
            if current in bulk_workers:
                bulk_workers.remove(current)
            err = str(exc)
            console_append(f"  ✕ {err}", ok=False)
            _set_card_op_status(p, f"{label} failed — {err[:100]}", ok=False)
            bulk_meta["pack_path"] = ""
            bulk_meta["step_index"] = 0
            _run_next_pack_step()

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

    def _format_branch_name(result: dict) -> str:
        branch = str(result.get("branch") or "").strip()
        if not branch or branch == "HEAD (no branch)":
            return "detached"
        return branch

    def _format_remote_status(result: dict) -> str:
        """Sync summary only (branch lives in the top-right pill)."""
        upstream = result.get("upstream")
        ahead = int(result.get("ahead") or 0)
        behind = int(result.get("behind") or 0)
        if not upstream:
            return "No upstream"
        if ahead and behind:
            return f"Diverged · ↑{ahead} ↓{behind}"
        if ahead:
            return f"Ahead {ahead}"
        if behind:
            return f"Behind {behind}"
        return "Clean"

    def _apply_remote_status(label, result: dict, path: str | None = None) -> None:
        """Fill branch (top-right) + sync footer + local Changes chip on the card."""
        try:
            card = label
            while card is not None and card.objectName() != "repoCard":
                card = card.parentWidget()
            branch_label = None
            changes_btn = None
            if card is not None:
                for child in card.findChildren(QLabel):
                    if child.property("role") == "cardBranch":
                        branch_label = child
                for child in card.findChildren(QPushButton):
                    if child.property("role") == "cardLocalChanges":
                        changes_btn = child
                        break

            if not result.get("is_repo"):
                label.setText("")
                label.setProperty("state", "")
                label.setToolTip("")
                label.style().unpolish(label)
                label.style().polish(label)
                if branch_label is not None:
                    branch_label.setText("—")
                    branch_label.setToolTip("")
                if changes_btn is not None:
                    changes_btn.hide()
                    changes_btn.setText("")
                return

            ahead = int(result.get("ahead") or 0)
            behind = int(result.get("behind") or 0)
            state = (
                "diverged" if ahead and behind
                else "ahead" if ahead
                else "behind" if behind
                else "ok" if result.get("upstream")
                else "none"
            )
            text = _format_remote_status(result)
            label.setText(text)
            label.setProperty("state", state)
            label.setToolTip(text)
            label.style().unpolish(label)
            label.style().polish(label)

            if branch_label is not None:
                branch = _format_branch_name(result)
                metrics = branch_label.fontMetrics()
                branch_label.setToolTip(branch)
                branch_label.setText(
                    metrics.elidedText(branch, Qt.TextElideMode.ElideMiddle, 140)
                )

            if changes_btn is not None:
                dirty = int(result.get("dirty_count") or 0)
                if dirty > 0:
                    changes_btn.setText(f"Changes · {dirty}")
                    changes_btn.show()
                else:
                    changes_btn.setText("")
                    changes_btn.hide()
        except RuntimeError:
            pass  # the card was rebuilt while the worker ran

    def _card_remote_pill(path: str):
        """The sync-status label of the live card for ``path`` (None if gone)."""
        try:
            widget = card_by_path.get(path)
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
            for path in card_order:
                result = status_cache.get(path)
                if result is not None:
                    label = _card_remote_pill(path)
                    if label is not None:
                        _apply_remote_status(label, result, path)
                _apply_card_updated(path)
        except RuntimeError:
            pass

    def _update_group_drift_bars() -> None:
        """Recompute each group row's clean/ahead/behind segments from the cache.

        Debounced (see ``drift_timer``) because status workers complete in a
        burst; the loop itself is one cheap pass over the favorites.
        """
        if favorites_repo is None:
            return
        counts: dict[str, list[int]] = {}
        for favorite in favorites_repo.by_kind("folder"):
            result = status_cache.get(favorite.ref)
            if not (result and result.get("is_repo")):
                continue
            group = (favorite.group_name or "").strip()
            ahead = int(result.get("ahead") or 0)
            behind = int(result.get("behind") or 0)
            entry = counts.setdefault(group, [0, 0, 0])
            if ahead and behind:
                entry[1] += 1  # diverged counts as both — it literally is
                entry[2] += 1
            elif ahead:
                entry[1] += 1
            elif behind:
                entry[2] += 1
            else:
                entry[0] += 1
        try:
            for i in range(groups_list.count()):
                item = groups_list.item(i)
                widget = groups_list.itemWidget(item)
                if widget is None:
                    continue
                key = item.data(Qt.ItemDataRole.UserRole)
                bar = widget.findChild(DriftBar)
                if bar is None:
                    continue
                clean, ahead, behind = counts.get(key or "", [0, 0, 0])
                bar.set_segments(clean, ahead, behind)
        except RuntimeError:
            pass

    def _status_worker_finished() -> None:
        """One status worker completed — re-enable the Refresh status button
        once the whole on-demand pass has landed."""
        if not landing_state.get("status_refreshing"):
            return
        pending = max(0, int(landing_state.get("status_pending") or 0) - 1)
        landing_state["status_pending"] = pending
        if pending == 0:
            landing_state["status_refreshing"] = False
            group = landing_state.get("status_group")
            if group is not None:
                # Persist the completed refresh so the timestamp survives a
                # restart and shows immediately when reopening the group.
                last_refresh_map[str(group)] = time.time()
                _save_last_refresh_map()
                # Only timestamp when the group the pass started for is still
                # on screen — switching groups mid-pass hides the label.
                if landing_state.get("group") == group:
                    _show_last_refresh(group)
            _set_bulk_enabled(not landing_state["bulk_busy"])

    def refresh_card_status(path: str, *, counts_pending: bool = False) -> None:
        """Fetch branch + ahead/behind for one card in the background.

        ``counts_pending`` marks workers spawned by the on-demand group
        refresh so only they drive the pass completion counter (the per-card
        button and post-fetch refreshes must not).
        """
        if favorites_repo is None:
            return
        worker = GitWorker("remote_status", path, executable=git_exe())
        status_workers.append(worker)

        def done(result, current=worker, counts=counts_pending) -> None:
            if current in status_workers:
                status_workers.remove(current)
            status_cache[path] = result
            if result.get("is_repo"):
                status_times[path] = time.time()
            label = _card_remote_pill(path)
            if label is not None:
                _apply_remote_status(label, result, path)
            _apply_card_updated(path)
            # Status workers complete in a burst — debounce the drift bars.
            drift_timer.start()
            if counts:
                _status_worker_finished()

        def failed(_exc, current=worker, counts=counts_pending) -> None:
            if current in status_workers:
                status_workers.remove(current)
            status_cache[path] = {
                "is_repo": False, "branch": "", "ahead": 0, "behind": 0, "upstream": None,
            }
            label = _card_remote_pill(path)
            if label is not None:
                _apply_remote_status(label, status_cache[path], path)
            if counts:
                _status_worker_finished()

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def refresh_group_statuses() -> None:
        """Fetch remote status for every repo in the current group on demand.

        The explicit counterpart to the old automatic refreshes: fills the
        card pills as results land and recomputes the group drift bars (each
        completed worker debounces ``_update_group_drift_bars``). Only runs
        when the user clicks Refresh status — never on load or group select.
        """
        if favorites_repo is None or landing_state.get("status_refreshing"):
            return
        group = landing_state["group"]
        if group in (None, "__demo__"):
            return
        members = favorites_for_group()
        paths = [favorite.ref for favorite in members]
        if not paths:
            return
        landing_state["status_refreshing"] = True
        landing_state["status_pending"] = len(paths)
        landing_state["status_group"] = group
        refresh_status.setEnabled(False)
        set_console_status(f"Refreshing status for {len(paths)} repo{'s' if len(paths) != 1 else ''}…")
        # Stagger starts so the UI stays responsive while pills fill in.
        for index, path in enumerate(paths):
            QTimer.singleShot(
                index * 12, lambda p=path: refresh_card_status(p, counts_pending=True)
            )

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

        # Inner tabs: Git ops (existing) | Maven Dependencies (declared, local).
        repo_inner = QTabWidget()
        repo_inner.setObjectName("repoInnerTabs")
        repo_inner.setDocumentMode(True)

        git_page = QWidget()
        git_page_layout = QVBoxLayout(git_page)
        git_page_layout.setContentsMargins(0, 8, 0, 0)
        git_page_layout.setSpacing(8)

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
        git_page_layout.addWidget(ops_row)

        status_label = styled_label("", "hint")
        status_label.setObjectName("gitStatusLabel")
        status_label.setWordWrap(True)
        git_page_layout.addWidget(status_label)

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
        git_page_layout.addWidget(result_stack, 1)
        repo_inner.addTab(git_page, "Git")

        # -- per-tab state ---------------------------------------------------------
        state = {"path": path, "busy": False, "is_repo": False}
        pending: list = []
        # Retained on the page so close_tab can cancel in-flight workers.
        page.pending_workers = pending

        deps_pane = build_maven_deps_pane(
            repo_path=path,
            pending_workers=pending,
            is_closed=lambda: bool(getattr(page, "closed", False)),
            branch_text=lambda: branch_pill.text().strip(),
            git_executable=git_exe,
            events=events,
        )
        repo_inner.addTab(deps_pane, "Dependencies")

        tree_pane = build_maven_tree_pane(
            repo_path=path,
            pending_workers=pending,
            is_closed=lambda: bool(getattr(page, "closed", False)),
            git_executable=git_exe,
            config_service=service,
        )
        repo_inner.addTab(tree_pane, "Tree")

        def _on_repo_inner_changed(index: int) -> None:
            if index == 1 and hasattr(deps_pane, "ensure_scanned"):
                deps_pane.ensure_scanned()

        repo_inner.currentChanged.connect(_on_repo_inner_changed)
        page_layout.addWidget(repo_inner, 1)

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

    # Debounced search filtering — see ``_on_filter_changed``.
    search_timer = QTimer(root)
    search_timer.setSingleShot(True)
    search_timer.setInterval(150)
    search_timer.timeout.connect(refresh_repo_cards)

    # Debounced recompute of the group drift bars (status workers complete
    # in bursts; one recompute per burst is plenty).
    drift_timer = QTimer(root)
    drift_timer.setSingleShot(True)
    drift_timer.setInterval(250)
    drift_timer.timeout.connect(_update_group_drift_bars)

    # The relative deck timestamp re-renders on the 120 s boundary — purely a
    # text refresh, never a git fetch (status is fetched only on demand).
    stamp_timer = QTimer(root)
    stamp_timer.setInterval(120_000)
    stamp_timer.timeout.connect(lambda: _show_last_refresh(landing_state["group"]))
    stamp_timer.start()

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
        # Debounce the rebuild — typing in the search field must not rebuild
        # every card per keystroke (lazy chunks make each rebuild cheap, but
        # settling the filter after a short pause is smoother still).
        search_timer.start()
        persist_timer.start()  # debounce the persisted search text

    open_button.clicked.connect(choose_folder)
    scan_button.clicked.connect(scan_for_repos)
    add_button.clicked.connect(add_repository)
    search_edit.textChanged.connect(lambda _text: _on_filter_changed())
    manage_button.clicked.connect(manage_groups)
    refresh_button.clicked.connect(refresh_favorites)
    favorites_scroll.customContextMenuRequested.connect(show_card_menu)
    # Lazy pagination: scrolling near the bottom materializes the next chunk.
    favorites_scroll.verticalScrollBar().valueChanged.connect(
        lambda _value: _maybe_load_more()
    )
    # Group rows are QPushButtons that call open_group directly — do not hook
    # currentItemChanged (it re-enters clear_list_widget and can segfault).
    bulk_fetch.clicked.connect(lambda: run_bulk("fetch"))
    bulk_status.clicked.connect(lambda: run_bulk("status"))
    bulk_reset.clicked.connect(lambda: run_bulk("reset"))
    refresh_status.clicked.connect(refresh_group_statuses)
    branch_fetch.clicked.connect(run_branch_fetch)
    branch_edit.clicked.connect(_edit_branches_dialog)
    branch_combo.currentTextChanged.connect(lambda _text: persist_timer.start())
    console_toggle.clicked.connect(toggle_console)
    console_clear.clicked.connect(clear_console)
    tabs.tabCloseRequested.connect(close_tab)
    tabs.currentChanged.connect(lambda _index: _persist_view_state())

    _refresh_branch_combo()
    _restore_view_state()
    # Load persisted per-group last-refresh times before the first render so
    # the deck timestamp for the restored group shows immediately.
    if not last_refresh_loaded:
        last_refresh_map.update(_load_last_refresh_map())
        last_refresh_loaded = True
    refresh_favorites()
    # Deliberately no git work on load: very large groups must not touch the
    # remote until the user asks (Status all, per-card refresh, fetch, …).

    # Home (and others) can jump straight to a group via the event bus.
    if events is not None:
        def _on_open_group(group: str = "", **_kwargs) -> None:
            open_group("" if group is None else str(group))

        events.subscribe(TOPIC_GIT_OPEN_GROUP, _on_open_group)
        root._git_open_group_handler = _on_open_group  # noqa: SLF001 — retain

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
