"""Maven Dependencies pane for an opened Git repository tab."""

from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import QThreadPool, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devworkbench.ui.widgets.common import button, search_field, styled_label
from devworkbench.workers.maven_worker import MavenPomWorker, dependencies_to_html


def build_maven_deps_pane(
    *,
    repo_path: str,
    pending_workers: list,
    is_closed: Callable[[], bool],
    branch_text: Callable[[], str],
) -> QWidget:
    """Build the Dependencies view for one opened repository."""
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
    refresh_btn = button("Refresh", "ghost")
    refresh_btn.setObjectName("mavenDepRefresh")
    toolbar_layout.addWidget(refresh_btn)
    download_btn = button("Download HTML", "primary")
    download_btn.setObjectName("mavenDepDownload")
    download_btn.setToolTip("Save the current filtered view as a self-contained HTML file")
    toolbar_layout.addWidget(download_btn)
    layout.addWidget(toolbar)

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

    table = QTableWidget(0, 5)
    table.setObjectName("mavenDepsTable")
    table.setHorizontalHeaderLabels(
        ["groupId (family)", "artifactId", "Version", "Scope", "Module"]
    )
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSortingEnabled(True)
    table.setAlternatingRowColors(True)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
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
        "selected_module": "",  # "" = All
    }

    def visible_rows() -> list[dict]:
        rows = list(state["rows"])
        module = state["selected_module"]
        if module:
            rows = [r for r in rows if r.get("module_id") == module]
        scope = scope_combo.currentData() or ""
        if scope:
            rows = [r for r in rows if (r.get("scope") or "compile") == scope]
        query = search.text().strip().lower()
        if query:
            rows = [
                r
                for r in rows
                if query in " ".join(
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
        table.setSortingEnabled(False)
        table.setRowCount(0)
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.get("group_id") or "",
                row.get("artifact_id") or "",
                row.get("version") or "",
                row.get("scope") or "",
                row.get("module_id") or "",
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col in (0, 1, 2):
                    item.setToolTip(str(row.get("pom_path") or ""))
                if row.get("managed") and col == 2:
                    item.setToolTip((item.toolTip() + "\nmanaged").strip())
                table.setItem(index, col, item)
        table.setSortingEnabled(True)
        err_n = len(state["errors"])
        extra = f" · {err_n} pom error(s)" if err_n else ""
        status.setText(
            f"{len(rows)} shown · {len(state['rows'])} declared · "
            f"{len(state['modules'])} module(s){extra}"
        )

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
        state["rows"] = list(result.get("dependencies") or [])
        state["modules"] = list(result.get("modules") or [])
        state["errors"] = list(result.get("errors") or [])
        state["loaded"] = True
        rebuild_modules()
        render_table()
        if not state["modules"] and not state["rows"]:
            status.setText("No pom.xml found in this folder (local checkout).")

    def scan() -> None:
        if state["busy"] or is_closed():
            return
        state["busy"] = True
        refresh_btn.setEnabled(False)
        download_btn.setEnabled(False)
        status.setText("Scanning local pom.xml files…")
        worker = MavenPomWorker(repo_path)
        pending_workers.append(worker)

        def done(result, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            if is_closed():
                return
            state["busy"] = False
            refresh_btn.setEnabled(True)
            download_btn.setEnabled(True)
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
            refresh_btn.setEnabled(True)
            download_btn.setEnabled(True)
            status.setText(f"Scan failed: {exc}")

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def download_html() -> None:
        rows = visible_rows()
        default_name = f"{os.path.basename(repo_path.rstrip('/')) or 'maven'}-deps.html"
        target, _ = QFileDialog.getSaveFileName(
            root,
            "Download dependencies as HTML",
            default_name,
            "HTML (*.html)",
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

    filter_timer = QTimer(root)
    filter_timer.setSingleShot(True)
    filter_timer.setInterval(150)
    filter_timer.timeout.connect(render_table)

    search.textChanged.connect(lambda _t: filter_timer.start())
    scope_combo.currentIndexChanged.connect(lambda _i: render_table())
    view_combo.currentIndexChanged.connect(lambda _i: render_table())
    modules_list.currentItemChanged.connect(lambda _c, _p: on_module_changed())
    refresh_btn.clicked.connect(scan)
    download_btn.clicked.connect(download_html)

    # Expose for the repo tab to trigger on first Dependencies visit.
    root.ensure_scanned = lambda: scan() if not state["loaded"] and not state["busy"] else None  # type: ignore[attr-defined]
    root.rescan = scan  # type: ignore[attr-defined]
    return root
