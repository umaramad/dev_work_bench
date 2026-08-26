"""Maven Dependency Tree pane — transitive dependency tree via ``mvn dependency:tree``."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QMenu,
    QPlainTextEdit,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devworkbench.ui.theme import token_qcolor
from devworkbench.ui.widgets.common import button, search_field, styled_label
from devworkbench.workers.maven_worker import (
    MavenTreeWorker,
    gav_string,
    maven_xml_snippet,
)


def build_maven_tree_pane(
    *,
    repo_path: str,
    pending_workers: list,
    is_closed: Callable[[], bool],
    git_executable: Callable[[], str] | None = None,
    config_service=None,
) -> QWidget:
    """Build the Tree view for one opened repository."""
    root = QWidget()
    root.setObjectName("mavenTreePane")
    layout = QVBoxLayout(root)
    layout.setContentsMargins(0, 8, 0, 0)
    layout.setSpacing(8)

    # -- Toolbar ---------------------------------------------------------------
    toolbar = QWidget()
    toolbar_layout = QHBoxLayout(toolbar)
    toolbar_layout.setContentsMargins(0, 0, 0, 0)
    toolbar_layout.setSpacing(6)

    search = search_field("Filter groupId, artifactId…")
    search.setObjectName("mavenTreeSearch")
    toolbar_layout.addWidget(search, 1)

    scope_combo = QComboBox()
    scope_combo.setObjectName("mavenTreeScope")
    scope_combo.addItem("All scopes", "")
    for scope in ("compile", "test", "provided", "runtime", "system", "import"):
        scope_combo.addItem(scope, scope)
    toolbar_layout.addWidget(scope_combo)

    verbose_btn = QToolButton()
    verbose_btn.setObjectName("mavenTreeVerbose")
    verbose_btn.setText("Verbose")
    verbose_btn.setCheckable(True)
    verbose_btn.setToolTip("Show omitted/conflict dependencies (re-runs mvn)")
    toolbar_layout.addWidget(verbose_btn)

    def _maven_exe() -> str:
        if config_service is None:
            return "mvn"
        try:
            return str(config_service.get("maven.executable") or "mvn")
        except Exception:  # noqa: BLE001
            return "mvn"

    def _maven_args() -> str:
        if config_service is None:
            return ""
        try:
            return str(config_service.get("maven.args") or "")
        except Exception:  # noqa: BLE001
            return ""

    resolve_btn = button("Resolve tree", "primary")
    resolve_btn.setObjectName("mavenTreeResolve")
    toolbar_layout.addWidget(resolve_btn)

    log_toggle = QToolButton()
    log_toggle.setObjectName("mavenTreeLogToggle")
    log_toggle.setText("Show log")
    log_toggle.setCheckable(True)
    log_toggle.setToolTip("Show/hide raw Maven console output")
    toolbar_layout.addWidget(log_toggle)

    status = styled_label("Click 'Resolve tree' to load the transitive dependency tree.", "hint")
    status.setObjectName("mavenTreeStatus")
    layout.addWidget(toolbar)
    layout.addWidget(status)

    # -- Splitter: tree + log console -----------------------------------------
    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.setChildrenCollapsible(False)

    # -- Tree widget -----------------------------------------------------------
    tree = QTreeWidget()
    tree.setObjectName("mavenTreeWidget")
    tree.setHeaderLabels(["artifactId", "groupId", "Version", "Scope"])
    tree.setRootIsDecorated(True)
    tree.setAlternatingRowColors(True)
    tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    header = tree.header()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, header.ResizeMode.Stretch)
    header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(3, header.ResizeMode.ResizeToContents)
    splitter.addWidget(tree)

    # -- Log console -----------------------------------------------------------
    log_console = QPlainTextEdit()
    log_console.setObjectName("mavenTreeLog")
    log_console.setReadOnly(True)
    log_console.setMaximumBlockCount(5000)
    log_console.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    log_font = QFont("monospace")
    log_font.setStyleHint(QFont.StyleHint.Monospace)
    log_console.setFont(log_font)
    log_console.setPlaceholderText("Maven output will appear here…")
    log_console.setVisible(False)
    splitter.addWidget(log_console)
    splitter.setStretchFactor(0, 3)
    splitter.setStretchFactor(1, 1)
    layout.addWidget(splitter, 1)

    # -- State -----------------------------------------------------------------
    state: dict = {
        "raw_tree": [],       # raw parser output (list of root node dicts)
        "loaded": False,
        "busy": False,
        "verbose": False,
        "raw_output": [],     # accumulated raw Maven output lines
    }

    # -- Colors ----------------------------------------------------------------
    conflict_bg = token_qcolor("redSoft", fallback="#e06c6c")
    if conflict_bg.alpha() == 255:
        conflict_bg.setAlphaF(0.12)
    winner_bg = token_qcolor("green", fallback="#4cc38a")
    if winner_bg.alpha() == 255:
        winner_bg.setAlphaF(0.10)

    # Log color tokens
    _log_green = token_qcolor("green", fallback="#4cc38a")
    _log_dim = token_qcolor("text3", fallback="#6d7686")
    _log_red = token_qcolor("red", fallback="#e06c6c")
    _log_amber = token_qcolor("amber", fallback="#e2a94f")
    _log_default = token_qcolor("text", fallback="#e3e7ef")

    def _log_color_for_line(line: str) -> QColor:
        """Pick a color based on line content."""
        if not line:
            return _log_default
        if line.startswith("$"):
            return _log_green
        if line.startswith(("[ERROR]", "***")):
            return _log_red
        if line.startswith("[WARNING]"):
            return _log_amber
        if line.startswith("[INFO]"):
            return _log_dim
        return _log_default

    def _set_enabled(enabled: bool) -> None:
        resolve_btn.setEnabled(enabled)
        verbose_btn.setEnabled(enabled)

    # -- Log helpers ----------------------------------------------------------
    def _clear_log() -> None:
        log_console.clear()
        state["raw_output"] = []

    def _append_log_line(line: str) -> None:
        state["raw_output"].append(line)
        cursor = log_console.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if log_console.document().blockCount() > 1 or cursor.block().length() > 1:
            cursor.insertBlock()  # newline before new content
        fmt = QTextCharFormat()
        fmt.setForeground(_log_color_for_line(line))
        cursor.setCharFormat(fmt)
        cursor.insertText(line)
        log_console.setTextCursor(cursor)
        # Auto-scroll to bottom
        sb = log_console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _toggle_log_console(checked: bool) -> None:
        log_console.setVisible(checked)
        log_toggle.setText("Hide log" if checked else "Show log")
        if checked:
            log_console.setFocus()

    # -- Tree building ---------------------------------------------------------
    def _add_nodes(parent_item: QTreeWidgetItem | None, nodes: list[dict]) -> None:
        for node in nodes:
            item = QTreeWidgetItem()
            item.setText(0, node.get("artifact_id") or "")
            item.setText(1, node.get("group_id") or "")
            item.setText(2, node.get("version") or "")
            item.setText(3, node.get("scope") or "")
            item.setData(0, Qt.ItemDataRole.UserRole, node)

            tip_parts = [
                f"{node.get('group_id', '')}:{node.get('artifact_id', '')}:{node.get('version', '')}",
                f"Scope: {node.get('scope', 'compile')}",
            ]
            if node.get("omitted"):
                tip_parts.append(node.get("omitted_reason") or "omitted")
            item.setToolTip(0, "\n".join(tip_parts))

            # Conflict styling
            if node.get("omitted"):
                # Loser: red tint + italic + strikethrough
                font = item.font(0)
                font.setItalic(True)
                font.setStrikeOut(True)
                for col in range(4):
                    item.setFont(col, font)
                    item.setBackground(col, conflict_bg)
            elif node.get("children"):
                # Has children — could be a winner in verbose mode
                # Apply subtle green tint if this node is a "winner"
                # (only meaningful when verbose is on and siblings are omitted)
                pass

            if parent_item is not None:
                parent_item.addChild(item)
            else:
                tree.addTopLevelItem(item)

            # Recurse
            children = node.get("children") or []
            if children:
                _add_nodes(item, children)

    def rebuild_tree() -> None:
        tree.clear()
        _add_nodes(None, state["raw_tree"])
        # Expand top-level, collapse deep levels
        for i in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(i)
            if top is None:
                continue
            top.setExpanded(True)
            for j in range(top.childCount()):
                child = top.child(j)
                if child is not None:
                    child.setExpanded(True)

    # -- Filtering -------------------------------------------------------------
    def _item_matches(item: QTreeWidgetItem, query: str, scope: str) -> bool:
        if query:
            hay = (item.text(0) + " " + item.text(1)).lower()
            if query not in hay:
                return False
        if scope:
            item_scope = item.text(3) or "compile"
            if item_scope != scope:
                return False
        return True

    def apply_filters() -> None:
        query = search.text().strip().lower()
        scope = scope_combo.currentData() or ""

        def _filter_item(item: QTreeWidgetItem) -> bool:
            """Returns True if this item or any descendant matches."""
            self_match = _item_matches(item, query, scope)
            any_child_match = False
            for i in range(item.childCount()):
                if _filter_item(item.child(i)):
                    any_child_match = True
            visible = self_match or any_child_match
            item.setHidden(not visible)
            return visible

        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item is not None:
                _filter_item(item)

    # -- Resolve ---------------------------------------------------------------
    def resolve() -> None:
        if state["busy"] or is_closed():
            return
        state["busy"] = True
        _set_enabled(False)
        _clear_log()
        status.setText("Resolving dependency tree…")
        worker = MavenTreeWorker(repo_path, verbose=state["verbose"], executable=_maven_exe(), extra_args=_maven_args())
        pending_workers.append(worker)

        # Stream raw output lines into the log console as they arrive
        worker.signals.line.connect(_append_log_line)

        def done(result, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            if is_closed():
                return
            state["busy"] = False
            _set_enabled(True)
            if not isinstance(result, dict):
                status.setText("Unexpected result from tree worker.")
                return
            mvn_err = result.get("mvn_error") or ""
            if mvn_err:
                status.setText(f"Maven error: {mvn_err}")
                state["raw_tree"] = []
                state["loaded"] = False
                tree.clear()
                return
            state["raw_tree"] = result.get("tree") or []
            state["loaded"] = True
            total = result.get("total_deps", 0)
            omitted = result.get("omitted_deps", 0)
            rebuild_tree()
            apply_filters()
            extra = f" · {omitted} omitted" if omitted else ""
            mode = "verbose" if state["verbose"] else ""
            mode_str = f" ({mode})" if mode else ""
            status.setText(f"{total} deps{extra}{mode_str}")

        def failed(exc, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            if is_closed():
                return
            state["busy"] = False
            _set_enabled(True)
            status.setText(f"Tree resolve failed: {exc}")

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    # -- Context menu ----------------------------------------------------------
    def show_context_menu(pos) -> None:
        item = tree.itemAt(pos)
        if item is None:
            return
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(node, dict):
            return
        menu = QMenu(tree)
        menu.addAction("Copy GAV").triggered.connect(
            lambda: _copy_gav(node)
        )
        menu.addAction("Copy Maven XML").triggered.connect(
            lambda: _copy_xml(node)
        )
        menu.exec(tree.viewport().mapToGlobal(pos))

    def _copy_gav(node: dict) -> None:
        row = {
            "group_id": node.get("group_id", ""),
            "artifact_id": node.get("artifact_id", ""),
            "version": node.get("version", ""),
        }
        QApplication.clipboard().setText(gav_string(row))
        status.setText(f"Copied {gav_string(row)}")

    def _copy_xml(node: dict) -> None:
        row = {
            "group_id": node.get("group_id", ""),
            "artifact_id": node.get("artifact_id", ""),
            "version": node.get("version", ""),
            "scope": node.get("scope", ""),
        }
        QApplication.clipboard().setText(maven_xml_snippet(row))
        status.setText(f"Copied Maven XML for {node.get('artifact_id', '')}")

    # -- Signals ---------------------------------------------------------------
    filter_timer = QTimer(root)
    filter_timer.setSingleShot(True)
    filter_timer.setInterval(150)
    filter_timer.timeout.connect(apply_filters)

    search.textChanged.connect(lambda _t: filter_timer.start())
    scope_combo.currentIndexChanged.connect(lambda _i: apply_filters())
    resolve_btn.clicked.connect(resolve)
    verbose_btn.toggled.connect(lambda checked: state.__setitem__("verbose", checked))
    log_toggle.toggled.connect(_toggle_log_console)
    tree.customContextMenuRequested.connect(show_context_menu)

    root.rescan = resolve  # type: ignore[attr-defined]
    return root
