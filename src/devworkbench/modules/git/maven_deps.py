"""Maven Dependencies pane for an opened Git repository tab."""

from __future__ import annotations

import os
import subprocess
from typing import Callable

from PySide6.QtCore import QThreadPool, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QApplication,
)

from devworkbench.ui.theme import current_colors
from devworkbench.ui.widgets.common import button, search_field, styled_label
from devworkbench.workers.maven_worker import (
    MavenCompareWorker,
    MavenPomWorker,
    current_branch_name,
    dependencies_to_csv,
    dependencies_to_html,
    enrich_dependency_rows,
    family_prefix,
    gav_string,
    list_local_branches,
    maven_xml_snippet,
    top_families,
)


def build_maven_deps_pane(
    *,
    repo_path: str,
    pending_workers: list,
    is_closed: Callable[[], bool],
    branch_text: Callable[[], str],
    git_executable: Callable[[], str] | None = None,
) -> QWidget:
    """Build the Dependencies view for one opened repository."""
    git_exe = git_executable or (lambda: "git")
    root = QWidget()
    root.setObjectName("mavenDepsPane")
    layout = QVBoxLayout(root)
    layout.setContentsMargins(0, 8, 0, 0)
    layout.setSpacing(8)

    toolbar = QWidget()
    toolbar_layout = QHBoxLayout(toolbar)
    toolbar_layout.setContentsMargins(0, 0, 0, 0)
    toolbar_layout.setSpacing(6)
    search = search_field("Filter groupId, artifactId, version…")
    search.setObjectName("mavenDepSearch")
    toolbar_layout.addWidget(search, 1)
    scope_combo = QComboBox()
    scope_combo.setObjectName("mavenDepScope")
    scope_combo.addItem("All scopes", "")
    for scope in ("compile", "test", "provided", "runtime", "system", "import"):
        scope_combo.addItem(scope, scope)
    toolbar_layout.addWidget(scope_combo)
    view_combo = QComboBox()
    view_combo.setObjectName("mavenDepView")
    view_combo.addItem("Per-module", "per_module")
    view_combo.addItem("Unique GAV", "unique")
    toolbar_layout.addWidget(view_combo)
    conflicts_btn = QToolButton()
    conflicts_btn.setObjectName("mavenDepConflicts")
    conflicts_btn.setText("Conflicts")
    conflicts_btn.setCheckable(True)
    conflicts_btn.setToolTip("Show only jars with different versions across modules")
    toolbar_layout.addWidget(conflicts_btn)
    refresh_btn = button("Refresh", "ghost")
    refresh_btn.setObjectName("mavenDepRefresh")
    toolbar_layout.addWidget(refresh_btn)
    compare_btn = button("Compare branches…", "ghost")
    compare_btn.setObjectName("mavenDepCompare")
    compare_btn.setToolTip("Diff declared dependencies between two local branches")
    toolbar_layout.addWidget(compare_btn)
    copy_btn = button("Copy GAV", "ghost")
    copy_btn.setObjectName("mavenDepCopy")
    toolbar_layout.addWidget(copy_btn)
    csv_btn = button("Download CSV", "ghost")
    csv_btn.setObjectName("mavenDepCsv")
    toolbar_layout.addWidget(csv_btn)
    download_btn = button("Download HTML", "primary")
    download_btn.setObjectName("mavenDepDownload")
    download_btn.setToolTip("Save the current filtered view as a self-contained HTML file")
    toolbar_layout.addWidget(download_btn)
    layout.addWidget(toolbar)

    chips_row = QWidget()
    chips_layout = QHBoxLayout(chips_row)
    chips_layout.setContentsMargins(0, 0, 0, 0)
    chips_layout.setSpacing(6)
    chips_label = styled_label("Family:", "hint")
    chips_layout.addWidget(chips_label)
    chips_host = QWidget()
    chips_host_layout = QHBoxLayout(chips_host)
    chips_host_layout.setContentsMargins(0, 0, 0, 0)
    chips_host_layout.setSpacing(6)
    chips_layout.addWidget(chips_host, 1)
    layout.addWidget(chips_row)

    status = styled_label("Scan local pom.xml files for declared dependencies.", "hint")
    status.setObjectName("mavenDepStatus")
    layout.addWidget(status)

    split = QSplitter(Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)

    modules_list = QListWidget()
    modules_list.setObjectName("mavenModuleList")
    modules_list.setMinimumWidth(160)
    modules_list.setMaximumWidth(260)
    split.addWidget(modules_list)

    table = QTableWidget(0, 6)
    table.setObjectName("mavenDepsTable")
    table.setHorizontalHeaderLabels(
        ["groupId (family)", "artifactId", "Version", "Used in", "Scope", "Module"]
    )
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setSortingEnabled(True)
    table.setAlternatingRowColors(True)
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
    split.addWidget(table)
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setSizes([200, 700])
    layout.addWidget(split, 1)

    state: dict = {
        "rows": [],
        "modules": [],
        "errors": [],
        "loaded": False,
        "busy": False,
        "selected_module": "",
        "family_filter": "",  # "" = all
        "row_by_index": [],
    }
    chip_buttons: list[QPushButton] = []

    def _set_toolbar_enabled(enabled: bool) -> None:
        for widget in (
            refresh_btn,
            download_btn,
            csv_btn,
            copy_btn,
            compare_btn,
            conflicts_btn,
        ):
            widget.setEnabled(enabled)

    def visible_rows() -> list[dict]:
        rows = list(state["rows"])
        module = state["selected_module"]
        if module:
            rows = [r for r in rows if r.get("module_id") == module]
        scope = scope_combo.currentData() or ""
        if scope:
            rows = [r for r in rows if (r.get("scope") or "compile") == scope]
        family = state.get("family_filter") or ""
        if family:
            rows = [r for r in rows if family_prefix(str(r.get("group_id") or "")) == family]
        if conflicts_btn.isChecked():
            rows = [r for r in rows if r.get("conflict")]
        query = search.text().strip().lower()
        if query:
            rows = [
                r
                for r in rows
                if query
                in " ".join(
                    (
                        str(r.get("group_id") or ""),
                        str(r.get("artifact_id") or ""),
                        str(r.get("version") or ""),
                        str(r.get("module_id") or ""),
                    )
                ).lower()
            ]
        if view_combo.currentData() == "unique":
            merged: dict[tuple, dict] = {}
            for row in rows:
                key = (
                    row.get("group_id"),
                    row.get("artifact_id"),
                    row.get("version"),
                    row.get("scope"),
                )
                existing = merged.get(key)
                if existing is None:
                    item = dict(row)
                    item["_modules"] = {row.get("module_id")}
                    merged[key] = item
                else:
                    existing["_modules"].add(row.get("module_id"))
            out = []
            for item in merged.values():
                modules = sorted(m for m in item.pop("_modules") if m)
                if len(modules) > 1:
                    item["module_id"] = f"{modules[0]} +{len(modules) - 1}"
                elif modules:
                    item["module_id"] = modules[0]
                out.append(item)
            rows = out
        return rows

    def render_table() -> None:
        rows = visible_rows()
        colors = current_colors()
        conflict_bg = QColor(colors.get("redSoft") or "rgba(224,108,108,0.13)")
        table.setSortingEnabled(False)
        table.setRowCount(0)
        table.setRowCount(len(rows))
        state["row_by_index"] = rows
        for index, row in enumerate(rows):
            used_n = row.get("used_in_n")
            used_text = str(used_n) if used_n is not None else ""
            version_text = str(row.get("version") or "")
            if row.get("conflict") and row.get("conflict_versions"):
                version_text = " · ".join(row.get("conflict_versions") or []) if view_combo.currentData() == "unique" else version_text
            values = (
                row.get("group_id") or "",
                row.get("artifact_id") or "",
                version_text,
                used_text,
                row.get("scope") or "",
                row.get("module_id") or "",
            )
            tip_parts = [str(row.get("pom_path") or "")]
            if row.get("used_in_modules"):
                tip_parts.append("Modules: " + ", ".join(row["used_in_modules"]))
            if row.get("conflict"):
                tip_parts.append(
                    "Conflict versions: " + ", ".join(row.get("conflict_versions") or [])
                )
            if row.get("managed"):
                tip_parts.append("managed")
            tip = "\n".join(p for p in tip_parts if p)
            for col, value in enumerate(values):
                if col == 3:
                    item = QTableWidgetItem()
                    try:
                        item.setData(Qt.ItemDataRole.DisplayRole, int(used_n or 0))
                    except (TypeError, ValueError):
                        item.setData(Qt.ItemDataRole.DisplayRole, 0)
                    item.setData(Qt.ItemDataRole.UserRole, index)
                    item.setToolTip(tip)
                    if row.get("conflict"):
                        item.setBackground(conflict_bg)
                    table.setItem(index, col, item)
                    continue
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, index)
                item.setToolTip(tip)
                if row.get("conflict"):
                    item.setBackground(conflict_bg)
                table.setItem(index, col, item)
        table.setSortingEnabled(True)
        err_n = len(state["errors"])
        conflict_n = len({(r.get("group_id"), r.get("artifact_id")) for r in state["rows"] if r.get("conflict")})
        extra = f" · {err_n} pom error(s)" if err_n else ""
        conflict_extra = f" · {conflict_n} conflict(s)" if conflict_n else ""
        status.setText(
            f"{len(rows)} shown · {len(state['rows'])} declared · "
            f"{len(state['modules'])} module(s){conflict_extra}{extra}"
        )

    def rebuild_family_chips() -> None:
        while chips_host_layout.count():
            item = chips_host_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        chip_buttons.clear()
        all_btn = QPushButton("All")
        all_btn.setObjectName("chip")
        all_btn.setCheckable(True)
        all_btn.setChecked(not state.get("family_filter"))
        all_btn.clicked.connect(lambda: _select_family(""))
        chips_host_layout.addWidget(all_btn)
        chip_buttons.append(all_btn)
        for fam, count in top_families(state["rows"]):
            btn = QPushButton(f"{fam} ({count})")
            btn.setObjectName("chip")
            btn.setCheckable(True)
            btn.setChecked(state.get("family_filter") == fam)
            btn.setToolTip(f"Filter family {fam}")
            btn.clicked.connect(lambda _c=False, f=fam: _select_family(f))
            chips_host_layout.addWidget(btn)
            chip_buttons.append(btn)
        chips_host_layout.addStretch(1)

    def _select_family(family: str) -> None:
        state["family_filter"] = family
        for btn in chip_buttons:
            data = btn.text()
            if family == "":
                btn.setChecked(data.startswith("All"))
            else:
                btn.setChecked(data.startswith(family + " ") or data == family)
        render_table()

    def rebuild_modules() -> None:
        modules_list.blockSignals(True)
        modules_list.clear()
        all_item = QListWidgetItem("All modules")
        all_item.setData(Qt.ItemDataRole.UserRole, "")
        modules_list.addItem(all_item)
        for module in state["modules"]:
            label = module.get("module_id") or module.get("rel_path") or "module"
            rel = module.get("rel_path") or ""
            text = label if rel in ("", ".") else f"{label}  ({rel})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, module.get("module_id") or "")
            item.setToolTip(str(module.get("pom_path") or ""))
            modules_list.addItem(item)
        modules_list.setCurrentRow(0)
        state["selected_module"] = ""
        modules_list.blockSignals(False)

    def on_module_changed() -> None:
        item = modules_list.currentItem()
        state["selected_module"] = (
            str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        )
        render_table()

    def apply_result(result: dict) -> None:
        raw = list(result.get("dependencies") or [])
        state["rows"] = enrich_dependency_rows(raw)
        state["modules"] = list(result.get("modules") or [])
        state["errors"] = list(result.get("errors") or [])
        state["loaded"] = True
        state["family_filter"] = ""
        rebuild_modules()
        rebuild_family_chips()
        render_table()
        if not state["modules"] and not state["rows"]:
            status.setText("No pom.xml found in this folder (local checkout).")

    def scan() -> None:
        if state["busy"] or is_closed():
            return
        state["busy"] = True
        _set_toolbar_enabled(False)
        status.setText("Scanning local pom.xml files…")
        worker = MavenPomWorker(repo_path)
        pending_workers.append(worker)

        def done(result, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            if is_closed():
                return
            state["busy"] = False
            _set_toolbar_enabled(True)
            if isinstance(result, dict):
                apply_result(result)
            else:
                status.setText("Unexpected scan result.")

        def failed(exc, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            if is_closed():
                return
            state["busy"] = False
            _set_toolbar_enabled(True)
            status.setText(f"Scan failed: {exc}")

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def selected_row() -> dict | None:
        items = table.selectedItems()
        if not items:
            return None
        # Map through UserRole because sorting may reorder visual rows.
        idx = items[0].data(Qt.ItemDataRole.UserRole)
        rows = state.get("row_by_index") or []
        if isinstance(idx, int) and 0 <= idx < len(rows):
            return rows[idx]
        row = table.currentRow()
        if 0 <= row < len(rows):
            return rows[row]
        return None

    def copy_gav(as_xml: bool = False) -> None:
        row = selected_row()
        if row is None:
            status.setText("Select a dependency row first.")
            return
        text = maven_xml_snippet(row) if as_xml else gav_string(row)
        QApplication.clipboard().setText(text)
        status.setText("Copied Maven XML" if as_xml else f"Copied {gav_string(row)}")

    def reveal_pom() -> None:
        row = selected_row()
        if row is None or not row.get("pom_path"):
            status.setText("Select a dependency row with a pom path.")
            return
        path = str(row["pom_path"])
        if not os.path.isfile(path):
            status.setText(f"pom not found: {path}")
            return
        try:
            subprocess.run(["open", "-R", path], check=False)
            status.setText(f"Revealed {path}")
        except OSError as exc:
            status.setText(f"Reveal failed: {exc}")

    def open_pom() -> None:
        row = selected_row()
        if row is None or not row.get("pom_path"):
            status.setText("Select a dependency row with a pom path.")
            return
        path = str(row["pom_path"])
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            status.setText(f"Could not open {path}")
        else:
            status.setText(f"Opened {path}")

    def show_row_menu(pos) -> None:
        row = selected_row()
        if row is None:
            # Try select under cursor
            index = table.indexAt(pos)
            if index.isValid():
                table.selectRow(index.row())
                row = selected_row()
        if row is None:
            return
        menu = QMenu(table)
        menu.addAction("Copy GAV").triggered.connect(lambda: copy_gav(False))
        menu.addAction("Copy Maven XML").triggered.connect(lambda: copy_gav(True))
        menu.addSeparator()
        menu.addAction("Reveal pom.xml").triggered.connect(reveal_pom)
        menu.addAction("Open pom.xml").triggered.connect(open_pom)
        menu.exec(table.viewport().mapToGlobal(pos))

    def download_html() -> None:
        rows = visible_rows()
        default_name = f"{os.path.basename(repo_path.rstrip('/')) or 'maven'}-deps.html"
        target, _ = QFileDialog.getSaveFileName(
            root, "Download dependencies as HTML", default_name, "HTML (*.html)"
        )
        if not target:
            return
        if not target.lower().endswith(".html"):
            target += ".html"
        html = dependencies_to_html(
            repo_path=repo_path,
            branch=branch_text() or "—",
            rows=rows,
            title="Maven dependencies (declared)",
        )
        try:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(html)
            status.setText(f"Saved {len(rows)} row(s) → {target}")
        except OSError as exc:
            status.setText(f"Could not save HTML: {exc}")

    def download_csv() -> None:
        rows = visible_rows()
        default_name = f"{os.path.basename(repo_path.rstrip('/')) or 'maven'}-deps.csv"
        target, _ = QFileDialog.getSaveFileName(
            root, "Download dependencies as CSV", default_name, "CSV (*.csv)"
        )
        if not target:
            return
        if not target.lower().endswith(".csv"):
            target += ".csv"
        try:
            with open(target, "w", encoding="utf-8", newline="") as handle:
                handle.write(dependencies_to_csv(rows))
            status.setText(f"Saved {len(rows)} row(s) → {target}")
        except OSError as exc:
            status.setText(f"Could not save CSV: {exc}")

    def compare_branches() -> None:
        if state["busy"] or is_closed():
            return
        exe = git_exe()
        branches = list_local_branches(repo_path, exe)
        if len(branches) < 2:
            QMessageBox.information(
                root,
                "Compare branches",
                "Need at least two local branches to compare.",
            )
            return
        current = current_branch_name(repo_path, exe) or branch_text() or branches[0]
        dialog = QDialog(root)
        dialog.setWindowTitle("Compare dependency versions")
        dialog.setMinimumWidth(420)
        d_layout = QVBoxLayout(dialog)
        d_layout.addWidget(
            styled_label(
                "Compares declared dependencies using temporary git worktrees "
                "(local only — your current branch is not switched).",
                "hint",
            )
        )
        row = QHBoxLayout()
        combo_a = QComboBox()
        combo_b = QComboBox()
        for name in branches:
            combo_a.addItem(name)
            combo_b.addItem(name)
        if current in branches:
            combo_a.setCurrentText(current)
        if len(branches) > 1:
            other = next((b for b in branches if b != combo_a.currentText()), branches[0])
            combo_b.setCurrentText(other)
        row.addWidget(QLabel("Branch A"))
        row.addWidget(combo_a, 1)
        row.addWidget(QLabel("Branch B"))
        row.addWidget(combo_b, 1)
        d_layout.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        d_layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        branch_a = combo_a.currentText().strip()
        branch_b = combo_b.currentText().strip()
        if not branch_a or not branch_b or branch_a == branch_b:
            status.setText("Pick two different local branches.")
            return

        state["busy"] = True
        _set_toolbar_enabled(False)
        status.setText(f"Comparing {branch_a} → {branch_b} (worktrees)…")
        worker = MavenCompareWorker(repo_path, branch_a, branch_b, executable=exe)
        pending_workers.append(worker)

        def done(result, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            if is_closed():
                return
            state["busy"] = False
            _set_toolbar_enabled(True)
            if not isinstance(result, dict):
                status.setText("Compare failed.")
                return
            _show_compare_result(result)

        def failed(exc, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            if is_closed():
                return
            state["busy"] = False
            _set_toolbar_enabled(True)
            status.setText(f"Compare failed: {exc}")

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def _show_compare_result(result: dict) -> None:
        diff = list(result.get("diff") or [])
        dialog = QDialog(root)
        dialog.setWindowTitle(
            f"Deps: {result.get('branch_a')} vs {result.get('branch_b')}"
        )
        dialog.resize(720, 420)
        d_layout = QVBoxLayout(dialog)
        d_layout.addWidget(
            styled_label(
                f"{len(diff)} change(s) · "
                f"A={result.get('count_a')} deps · B={result.get('count_b')} deps",
                "hint",
            )
        )
        grid = QTableWidget(0, 5)
        grid.setHorizontalHeaderLabels(
            ["Change", "groupId", "artifactId", "Version A", "Version B"]
        )
        grid.verticalHeader().setVisible(False)
        grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        grid.horizontalHeader().setStretchLastSection(True)
        grid.setRowCount(len(diff))
        for i, row in enumerate(diff):
            values = (
                row.get("change") or "",
                row.get("group_id") or "",
                row.get("artifact_id") or "",
                row.get("version_a") or "",
                row.get("version_b") or "",
            )
            for col, value in enumerate(values):
                grid.setItem(i, col, QTableWidgetItem(str(value)))
        d_layout.addWidget(grid, 1)
        close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn.rejected.connect(dialog.reject)
        close_btn.accepted.connect(dialog.accept)
        d_layout.addWidget(close_btn)
        status.setText(
            f"Compare done — {len(diff)} change(s) between "
            f"{result.get('branch_a')} and {result.get('branch_b')}"
        )
        dialog.exec()

    filter_timer = QTimer(root)
    filter_timer.setSingleShot(True)
    filter_timer.setInterval(150)
    filter_timer.timeout.connect(render_table)

    search.textChanged.connect(lambda _t: filter_timer.start())
    scope_combo.currentIndexChanged.connect(lambda _i: render_table())
    view_combo.currentIndexChanged.connect(lambda _i: render_table())
    conflicts_btn.toggled.connect(lambda _c: render_table())
    modules_list.currentItemChanged.connect(lambda _c, _p: on_module_changed())
    refresh_btn.clicked.connect(scan)
    download_btn.clicked.connect(download_html)
    csv_btn.clicked.connect(download_csv)
    copy_btn.clicked.connect(lambda: copy_gav(False))
    compare_btn.clicked.connect(compare_branches)
    table.customContextMenuRequested.connect(show_row_menu)

    root.ensure_scanned = lambda: scan() if not state["loaded"] and not state["busy"] else None  # type: ignore[attr-defined]
    root.rescan = scan  # type: ignore[attr-defined]
    return root
