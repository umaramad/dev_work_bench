"""Maven Dependencies pane for an opened Git repository tab."""

from __future__ import annotations

import os
import subprocess
from typing import Callable

from PySide6.QtCore import QThreadPool, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
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

from devworkbench.services.configuration_service import TOPIC_SETTINGS_CHANGED
from devworkbench.ui.theme import token_qcolor
from devworkbench.ui.widgets.common import button, search_field, styled_label
from devworkbench.workers.maven_worker import (
    MavenCompareWorker,
    MavenPomWorker,
    current_branch_name,
    dependencies_table_text,
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
    events=None,
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
    profile_combo = QComboBox()
    profile_combo.setObjectName("mavenDepProfile")
    profile_combo.addItem("All profiles", "")
    profile_combo.addItem("direct", "__direct__")
    profile_combo.setToolTip("Filter by the profile that declares the dependency")
    toolbar_layout.addWidget(profile_combo)
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
    copy_table_btn = QToolButton()
    copy_table_btn.setObjectName("mavenDepCopyTable")
    copy_table_btn.setText("Copy table")
    copy_table_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    copy_table_btn.setToolTip(
        "Copy selected rows (or all filtered if none selected) with headers"
    )
    copy_table_menu = QMenu(copy_table_btn)
    copy_tsv_action = copy_table_menu.addAction("Tab-separated (Excel / email)")
    copy_md_action = copy_table_menu.addAction("Markdown (Slack / Teams)")
    copy_table_btn.setMenu(copy_table_menu)
    toolbar_layout.addWidget(copy_table_btn)
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

    table = QTableWidget(0, 7)
    table.setObjectName("mavenDepsTable")
    table.setHorizontalHeaderLabels(
        ["groupId (family)", "artifactId", "Version", "Used in", "Scope", "Module", "Profile"]
    )
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
    header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
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
            copy_table_btn,
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
        prof = profile_combo.currentData() or ""
        if prof:
            if prof == "__direct__":
                rows = [r for r in rows if not (r.get("profile") or "")]
            else:
                rows = [r for r in rows if (r.get("profile") or "") == prof]
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
        # Soft red tint for version conflicts (must use token_qcolor — QColor
        # does not parse CSS rgba() and would paint solid black).
        conflict_bg = token_qcolor("redSoft", fallback="#e06c6c")
        if conflict_bg.alpha() == 255:
            conflict_bg.setAlphaF(0.12)
        # Soft cyan tint for profile-declared dependencies.
        profile_bg = token_qcolor("cyan", fallback="#5cc8d6")
        if profile_bg.alpha() == 255:
            profile_bg.setAlphaF(0.10)
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
                row.get("profile") or "",
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
                # Profile column (6): italic + soft tint for profile-declared deps.
                if col == 6 and row.get("profile"):
                    font = item.font()
                    font.setItalic(True)
                    item.setFont(font)
                    item.setBackground(profile_bg)
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

    def rebuild_profile_combo() -> None:
        profile_combo.blockSignals(True)
        prev = profile_combo.currentData()
        profile_combo.clear()
        profile_combo.addItem("All profiles", "")
        profile_combo.addItem("direct", "__direct__")
        profiles = sorted({
            str(r.get("profile") or "").strip()
            for r in state["rows"]
            if str(r.get("profile") or "").strip()
        })
        for prof in profiles:
            profile_combo.addItem(prof, prof)
        # Restore previous selection if still available
        idx = profile_combo.findData(prev)
        if idx >= 0:
            profile_combo.setCurrentIndex(idx)
        profile_combo.blockSignals(False)

    def apply_result(result: dict) -> None:
        raw = list(result.get("dependencies") or [])
        state["rows"] = enrich_dependency_rows(raw)
        state["modules"] = list(result.get("modules") or [])
        state["errors"] = list(result.get("errors") or [])
        state["loaded"] = True
        state["family_filter"] = ""
        rebuild_modules()
        rebuild_profile_combo()
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

    def selected_rows() -> list[dict]:
        """Return selected dependency rows in visual top-to-bottom order."""
        rows = state.get("row_by_index") or []
        seen: set[int] = set()
        ordered: list[tuple[int, dict]] = []
        for item in table.selectedItems():
            idx = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(idx, int) or idx in seen or not (0 <= idx < len(rows)):
                continue
            seen.add(idx)
            ordered.append((item.row(), rows[idx]))
        ordered.sort(key=lambda pair: pair[0])
        return [row for _, row in ordered]

    def selected_row() -> dict | None:
        rows = selected_rows()
        return rows[0] if rows else None

    def copy_gav(as_xml: bool = False) -> None:
        rows = selected_rows()
        if not rows:
            status.setText("Select one or more dependency rows first.")
            return
        if as_xml:
            text = "\n\n".join(maven_xml_snippet(row) for row in rows)
            QApplication.clipboard().setText(text)
            status.setText(f"Copied Maven XML for {len(rows)} row(s)")
            return
        text = "\n".join(gav_string(row) for row in rows)
        QApplication.clipboard().setText(text)
        if len(rows) == 1:
            status.setText(f"Copied {gav_string(rows[0])}")
        else:
            status.setText(f"Copied {len(rows)} GAV(s)")

    def copy_filtered_table(fmt: str = "tsv") -> None:
        selected = selected_rows()
        rows = selected if selected else visible_rows()
        if not rows:
            status.setText("No rows to copy.")
            return
        text = dependencies_table_text(rows, fmt=fmt)
        QApplication.clipboard().setText(text)
        kind = "Markdown" if fmt == "markdown" else "tab-separated"
        scope = "selected" if selected else "filtered"
        status.setText(f"Copied {len(rows)} {scope} row(s) as {kind} table")

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
        index = table.indexAt(pos)
        if index.isValid():
            # Keep multi-selection if the clicked row is already selected.
            item = table.item(index.row(), 0)
            already = bool(item and item.isSelected())
            if not already:
                table.selectRow(index.row())
        rows = selected_rows()
        if not rows:
            return
        menu = QMenu(table)
        menu.addAction("Copy GAV").triggered.connect(lambda: copy_gav(False))
        menu.addAction("Copy Maven XML").triggered.connect(lambda: copy_gav(True))
        menu.addAction("Copy selected/filtered table (TSV)").triggered.connect(
            lambda: copy_filtered_table("tsv")
        )
        menu.addAction("Copy selected/filtered table (Markdown)").triggered.connect(
            lambda: copy_filtered_table("markdown")
        )
        menu.addSeparator()
        menu.addAction("Reveal pom.xml").triggered.connect(reveal_pom)
        menu.addAction("Open pom.xml").triggered.connect(open_pom)
        menu.exec(table.viewport().mapToGlobal(pos))

    def download_html() -> None:
        rows = list(state["rows"])
        default_name = f"{os.path.basename(repo_path.rstrip('/')) or 'maven'}-deps.html"
        target, _ = QFileDialog.getSaveFileName(
            root, "Download dependencies as HTML", default_name, "HTML (*.html)"
        )
        if not target:
            return
        if not target.lower().endswith(".html"):
            target += ".html"
        initial_filters = {
            "search": search.text().strip(),
            "scope": scope_combo.currentData() or "",
            "conflicts": bool(conflicts_btn.isChecked()),
            "module": state.get("selected_module") or "",
            "profile": profile_combo.currentData() or "",
        }
        html = dependencies_to_html(
            repo_path=repo_path,
            branch=branch_text() or "—",
            rows=rows,
            title="Maven dependencies (declared)",
            initial_filters=initial_filters,
        )
        try:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(html)
            status.setText(
                f"Saved interactive report · {len(rows)} embedded row(s) → {target}"
            )
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
    profile_combo.currentIndexChanged.connect(lambda _i: render_table())
    view_combo.currentIndexChanged.connect(lambda _i: render_table())
    conflicts_btn.toggled.connect(lambda _c: render_table())
    modules_list.currentItemChanged.connect(lambda _c, _p: on_module_changed())
    refresh_btn.clicked.connect(scan)
    download_btn.clicked.connect(download_html)
    csv_btn.clicked.connect(download_csv)
    copy_btn.clicked.connect(lambda: copy_gav(False))
    copy_table_btn.clicked.connect(lambda: copy_filtered_table("tsv"))
    copy_tsv_action.triggered.connect(lambda: copy_filtered_table("tsv"))
    copy_md_action.triggered.connect(lambda: copy_filtered_table("markdown"))
    compare_btn.clicked.connect(compare_branches)
    table.customContextMenuRequested.connect(show_row_menu)

    # Re-tint conflict rows when Appearance theme flips.
    if events is not None:
        def _on_setting_changed(key: str = None, value=None) -> None:
            if key == "appearance.theme" and state.get("loaded"):
                render_table()

        events.subscribe(TOPIC_SETTINGS_CHANGED, _on_setting_changed)

    root.ensure_scanned = lambda: scan() if not state["loaded"] and not state["busy"] else None  # type: ignore[attr-defined]
    root.rescan = scan  # type: ignore[attr-defined]
    return root
