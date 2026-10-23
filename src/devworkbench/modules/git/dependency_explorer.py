"""Group-Level Dependency Explorer — cross-repository Maven search pane.

Opens as its own tab inside the Git screen (a sibling of the landing page
and the repository tabs). Scans every repository of one group with
:class:`~devworkbench.workers.group_maven_worker.GroupScanWorker` (which
reuses the existing ``scan_declared_dependencies`` effective-model service —
no POM is parsed twice), classifies each repository as J2EE / Non-J2EE /
Unknown from explainable evidence, and searches the resulting in-memory
:class:`~devworkbench.services.dependency_explorer.ArtifactIndex` on every
keystroke.

The repository-level Dependencies menu is untouched — each usage row can
navigate into that view via the ``open_dependency_view`` callback.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Callable

from PySide6.QtCore import QThreadPool, Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devworkbench.services.dependency_explorer import (
    ArtifactIndex,
    OVERRIDE_AUTO,
    OVERRIDE_J2EE,
    OVERRIDE_NON_J2EE,
    VERDICT_LABELS,
    VERDICT_UNKNOWN,
    compare_versions,
    load_overrides,
    reclassify_entry,
    save_overrides,
)
from devworkbench.ui.theme import token_qcolor
from devworkbench.ui.widgets.common import button, search_field, styled_label
from devworkbench.workers.group_maven_worker import GroupScanWorker

logger = logging.getLogger("devworkbench.modules.git.dependency_explorer")

_OVERRIDE_KEY = "git.dep_explorer.overrides"

_GROUP_BY_ARTIFACT = "artifact"
_GROUP_BY_REPOSITORY = "repository"
_GROUP_BY_FLAT = "flat"

_COLUMNS = ("Name", "Version", "Repository", "Module", "Scope", "Origin", "POM")


def _bump(widget, delta: int = 2, min_pt: int = 13, *, bold: bool | None = None) -> None:
    font = widget.font()
    font.setPointSize(max(font.pointSize() + delta, min_pt))
    if bold is not None:
        font.setBold(bold)
    widget.setFont(font)


def _verdict_badge(classification: dict) -> str:
    verdict = str((classification or {}).get("verdict") or VERDICT_UNKNOWN)
    text = VERDICT_LABELS.get(verdict, verdict)
    if (classification or {}).get("overridden"):
        text += " *"
    return text


def _classification_summary(classification: dict) -> str:
    """Evidence summary line for a stored classification dict."""
    evidence = classification.get("evidence") or []
    parts = []
    for item in evidence[:4]:
        description = str(item.get("description") or item.get("kind") or "")
        weight = item.get("weight") or 0
        if description:
            parts.append(f"{description} ({weight:+g})" if weight else description)
    if classification.get("overridden"):
        parts.insert(0, "manual override")
    return " · ".join(parts) if parts else "no evidence found"


def build_dependency_explorer_pane(
    *,
    group_name: str,
    groups_provider: Callable[[], list[str]],
    repos_provider: Callable[[str], list[tuple[str, str]]],
    open_dependency_view: Callable[[str], None],
    pending_workers: list,
    is_closed: Callable[[], bool],
    config_service=None,
    events=None,
) -> QWidget:
    """Build the explorer pane for one Git-screen tab.

    ``repos_provider(group)`` returns a *fresh* ``[(label, path), …]``
    snapshot each scan, so added/removed repositories are always picked up;
    ``open_dependency_view(repo_path)`` navigates to the existing
    repository-level Dependencies tab.
    """
    root = QWidget()
    root.setObjectName("dependencyExplorerPane")
    layout = QVBoxLayout(root)
    layout.setContentsMargins(10, 10, 10, 8)
    layout.setSpacing(8)

    # ------------------------------------------------------------------ head
    head = QWidget()
    head_layout = QHBoxLayout(head)
    head_layout.setContentsMargins(0, 0, 0, 0)
    head_layout.setSpacing(8)
    title = QLabel("Dependency Explorer")
    title.setObjectName("explorerTitle")
    _bump(title, 4, 18, bold=True)
    head_layout.addWidget(title)
    group_combo = QComboBox()
    group_combo.setObjectName("explorerGroupCombo")
    group_combo.setToolTip("Repository group to scan")
    group_combo.setMinimumWidth(180)
    _bump(group_combo, 2, 13)
    head_layout.addWidget(group_combo)
    j2ee_only = QPushButton("J2EE projects only")
    j2ee_only.setObjectName("explorerJ2eeOnly")
    j2ee_only.setCheckable(True)
    j2ee_only.setChecked(True)
    j2ee_only.setToolTip(
        "Only search repositories classified as J2EE from Maven evidence\n"
        "(packaging, EE APIs, descriptors). Classification is explainable —\n"
        "open Classification… to review evidence or override a verdict."
    )
    _bump(j2ee_only, 2, 13)
    head_layout.addWidget(j2ee_only)
    head_layout.addStretch(1)
    classify_btn = button("Classification…", "ghost")
    classify_btn.setObjectName("explorerClassify")
    classify_btn.setToolTip("Review J2EE classification evidence and set manual overrides")
    _bump(classify_btn, 2, 13)
    head_layout.addWidget(classify_btn)
    refresh_btn = button("Refresh", "ghost")
    refresh_btn.setObjectName("explorerRefresh")
    refresh_btn.setToolTip("Rescan every repository in this group (POMs are only read here)")
    _bump(refresh_btn, 2, 13)
    head_layout.addWidget(refresh_btn)
    layout.addWidget(head)

    # ---------------------------------------------------------------- toolbar
    toolbar = QWidget()
    toolbar_layout = QHBoxLayout(toolbar)
    toolbar_layout.setContentsMargins(0, 0, 0, 0)
    toolbar_layout.setSpacing(6)
    search_edit = search_field("Search JAR names, groupId, artifactId, coordinates, versions…")
    search_edit.setObjectName("explorerSearch")
    _bump(search_edit, 2, 13)
    toolbar_layout.addWidget(search_edit, 1)

    def _filter_combo(name: str, tip: str) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(name)
        combo.setToolTip(tip)
        combo.setMinimumWidth(120)
        _bump(combo, 1, 12)
        toolbar_layout.addWidget(combo)
        return combo

    scope_combo = _filter_combo("explorerScope", "Filter by dependency scope")
    origin_combo = _filter_combo(
        "explorerOrigin", "Filter by declaration origin (direct / inherited / profile / managed)"
    )
    version_combo = _filter_combo(
        "explorerVersionSource",
        "Filter by version provenance (explicit / managed / unresolved / none)",
    )
    repo_combo = _filter_combo("explorerRepo", "Filter by repository")
    groupby_combo = _filter_combo("explorerGroupBy", "Group results by artifact or repository")
    groupby_combo.addItems(["Group by artifact", "Group by repository", "Flat list"])
    layout.addWidget(toolbar)

    # ------------------------------------------------------------ summary cards
    cards = QWidget()
    cards_layout = QHBoxLayout(cards)
    cards_layout.setContentsMargins(0, 0, 0, 0)
    cards_layout.setSpacing(8)

    def _card(name: str, caption: str) -> QLabel:
        label = QLabel("—")
        label.setObjectName(name)
        label.setProperty("role", "explorerCard")
        label.setToolTip(caption)
        label.setWordWrap(False)
        _bump(label, 1, 16, bold=True)
        cards_layout.addWidget(label, 1)
        return label

    card_repos = _card("explorerCardRepos", "Eligible J2EE repositories (of those scanned)")
    card_artifacts = _card("explorerCardArtifacts", "Artifacts matching the current filters")
    card_duplicates = _card(
        "explorerCardDuplicates", "Cross-repo duplicates (normalized coordinates or versions)"
    )
    cards_layout.addStretch(0)
    layout.addWidget(cards)

    # ---------------------------------------------------------------- banner
    banner = styled_label("", "hint")
    banner.setObjectName("explorerBanner")
    banner.setWordWrap(True)
    banner.hide()
    layout.addWidget(banner)

    # ---------------------------------------------------------------- results
    tree = QTreeWidget()
    tree.setObjectName("explorerTree")
    tree.setHeaderLabels(list(_COLUMNS))
    tree.setUniformRowHeights(True)
    tree.setAlternatingRowColors(True)
    tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tree.setSortingEnabled(True)
    tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    header = tree.header()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
    layout.addWidget(tree, 1)

    state_label = styled_label("", "hint")
    state_label.setObjectName("explorerStateLabel")
    state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    state_label.setWordWrap(True)
    _bump(state_label, 2, 14)
    state_label.hide()
    layout.addWidget(state_label, 1)

    state_action = button("", "ghost")
    state_action.setObjectName("explorerStateAction")
    state_action.hide()
    layout.addWidget(state_action, 0, Qt.AlignmentFlag.AlignHCenter)

    status = styled_label("Choose a group to scan.", "hint")
    status.setObjectName("explorerStatus")
    _bump(status, 1, 12)
    layout.addWidget(status)

    # ------------------------------------------------------------------ state
    overrides: dict[str, str] = {}
    state: dict = {
        "group": group_name,
        "index": None,
        "repos": [],
        "busy": False,
        "loaded": False,
        "epoch": 0,
        "group_by": _GROUP_BY_ARTIFACT,
        "last_query": "",
    }

    if config_service is not None:
        try:
            overrides = load_overrides(config_service.get(_OVERRIDE_KEY))
        except Exception:  # noqa: BLE001 — a bad stored blob must never block the pane
            logger.exception("failed to load dependency explorer overrides")
            overrides = {}

    def _save_overrides() -> None:
        if config_service is None:
            return
        try:
            config_service.set(_OVERRIDE_KEY, save_overrides(overrides))
        except Exception:  # noqa: BLE001
            logger.exception("failed to persist dependency explorer overrides")

    # ---------------------------------------------------------------- helpers
    def _set_state_message(message: str, action_text: str = "", action=None) -> None:
        tree.hide()
        state_label.setText(message)
        state_label.show()
        if action_text and action is not None:
            state_action.setText(action_text)
            if state.get("state_action_connected"):
                state_action.clicked.disconnect()
            state_action.clicked.connect(action)
            state["state_action_connected"] = True
            state_action.show()
        else:
            state_action.hide()

    def _hide_state_message() -> None:
        state_label.hide()
        state_action.hide()
        tree.show()

    def _busy(busy: bool) -> None:
        state["busy"] = busy
        refresh_btn.setEnabled(not busy)
        classify_btn.setEnabled(not busy)

    def _fill_combo(combo: QComboBox, all_label: str, entries: list[tuple[str, str]]) -> None:
        """Refill a filter combo, preserving the current selection."""
        combo.blockSignals(True)
        previous = combo.currentData()
        combo.clear()
        combo.addItem(all_label, "")
        for label, data in entries:
            combo.addItem(label, data)
        index = combo.findData(previous)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _refresh_filter_combos() -> None:
        index = state.get("index")
        if not isinstance(index, ArtifactIndex):
            return
        rows = index.usages
        scopes = sorted({u.scope for u in rows if u.scope})
        origins = sorted({u.origin for u in rows if u.origin})
        sources = sorted({u.version_source for u in rows if u.version_source})
        repos: list[tuple[str, str]] = []
        for usage in sorted(rows, key=lambda u: u.repo_name.casefold()):
            entry = (usage.repo_name, usage.repo_path)
            if entry not in repos:
                repos.append(entry)
        decorated: list[tuple[str, str]] = []
        for label, path in repos:
            badge = _verdict_badge(index.classification_for(path))
            decorated.append((f"{label}  [{badge}]", path))
        _fill_combo(scope_combo, "All scopes", [(s, s) for s in scopes])
        _fill_combo(origin_combo, "All origins", [(o, o) for o in origins])
        _fill_combo(version_combo, "All version states", [(s, s) for s in sources])
        _fill_combo(repo_combo, "All repositories", decorated)

    # ------------------------------------------------------------------ scan
    def _scan() -> None:
        if state["busy"] or is_closed():
            return
        repos = list(repos_provider(state["group"]) or [])
        state["repos"] = repos
        state["epoch"] += 1
        epoch = state["epoch"]
        _busy(True)
        tree.clear()
        if not repos:
            state["index"] = ArtifactIndex([])
            state["loaded"] = True
            _busy(False)
            _refresh_filter_combos()
            _set_state_message(
                "This group has no repositories yet.",
            )
            status.setText("No repositories in this group.")
            return
        status.setText(f"Scanning {len(repos)} repositories…")
        _set_state_message(f"Scanning {len(repos)} repositories — reading pom.xml files…")
        worker = GroupScanWorker(repos, overrides=overrides)
        pending_workers.append(worker)

        def progress(percent: int, payload=None, current=worker) -> None:
            if is_closed() or current is not worker or state["epoch"] != epoch:
                return
            info = payload or {}
            status.setText(
                f"Scanning {len(repos)} repositories… "
                f"({info.get('done', 0)}/{info.get('total', len(repos))} {info.get('name', '')})"
            )

        def done(result, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            if is_closed() or state["epoch"] != epoch:
                return  # stale: the user switched group / rescanned meanwhile
            _busy(False)
            if not isinstance(result, dict):
                status.setText("Scan returned an unexpected result — Refresh to retry.")
                _set_state_message("Scan failed: unexpected result. Refresh to retry.")
                return
            state["index"] = result.get("index") or ArtifactIndex(result.get("repos") or [])
            state["loaded"] = True
            _refresh_filter_combos()
            _render()
            if result.get("cancelled"):
                status.setText("Scan cancelled.")

        def failed(exc, current=worker) -> None:
            if current in pending_workers:
                pending_workers.remove(current)
            if is_closed() or state["epoch"] != epoch:
                return
            _busy(False)
            logger.warning("group dependency scan failed: %s", exc)
            status.setText(f"Scan failed: {exc}")
            _set_state_message(f"Scan failed: {exc}", "Retry", _scan)

        worker.signals.progress.connect(progress)
        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    # ----------------------------------------------------------------- render
    def _selected_filters() -> dict:
        return {
            "scopes": [scope_combo.currentData()] if scope_combo.currentData() else [],
            "origins": [origin_combo.currentData()] if origin_combo.currentData() else [],
            "version_sources": [version_combo.currentData()] if version_combo.currentData() else [],
            "repos": [repo_combo.currentData()] if repo_combo.currentData() else [],
        }

    def _usage_item(usage, *, name: str | None = None) -> QTreeWidgetItem:
        version_text = usage.version or ("unresolved" if usage.declared_version else "—")
        origin_text = usage.origin + (f" ({usage.profile})" if usage.profile else "")
        item = QTreeWidgetItem(
            [
                name or usage.coordinates,
                version_text,
                usage.repo_name,
                usage.module_id,
                usage.scope,
                origin_text,
                usage.pom_path,
            ]
        )
        item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "usage", "usage": usage})
        tip = [
            f"{usage.gav()}",
            f"type: {usage.type}" + (f" · classifier: {usage.classifier}" if usage.classifier else ""),
            f"optional: {usage.optional} · version source: {usage.version_source}",
        ]
        if usage.declared_version and usage.declared_version != usage.version:
            tip.append(f"declared: {usage.declared_version}")
        if usage.filename:
            tip.append(f"build output: {usage.filename}")
        elif usage.present_in_build_output:
            tip.append("present in build output")
        tip.append(usage.pom_path)
        item.setToolTip(0, "\n".join(tip))
        for column in range(len(_COLUMNS)):
            item.setToolTip(column, item.toolTip(0))
        return item

    def _artifact_item(group: dict) -> QTreeWidgetItem:
        versions = " · ".join(group["versions"]) if group["versions"] else "—"
        repos_text = ", ".join(sorted(group["repo_names"])) or "—"
        item = QTreeWidgetItem(
            [
                group["coordinates"],
                versions,
                f"{len(group['repos'])} repo(s)",
                "",
                ", ".join(sorted(group["scopes"])),
                ", ".join(sorted(group["origins"])),
                "",
            ]
        )
        item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "artifact", "group": group})
        flags = []
        if group["cross_repo"]:
            flags.append("used in multiple repositories")
        if group["version_divergence"]:
            flags.append("version divergence: " + " vs ".join(group["versions"]))
        if group["in_build_output"]:
            flags.append("present in build output")
        tip = [f"Repositories: {repos_text}"]
        if flags:
            tip.append(" · ".join(flags))
        item.setToolTip(0, "\n".join(tip))
        for column in range(len(_COLUMNS)):
            item.setToolTip(column, item.toolTip(0))
        if group["version_divergence"]:
            tint = token_qcolor("redSoft", fallback="#e06c6c")
            if tint.alpha() == 255:
                tint.setAlphaF(0.12)
            for column in range(len(_COLUMNS)):
                item.setBackground(column, tint)
        for usage in sorted(
            group["usages"],
            key=lambda u: (u.repo_name.casefold(), compare_versions(u.version, ""), u.module_id),
        ):
            item.addChild(_usage_item(usage))
        return item

    def _repo_item(group: dict) -> QTreeWidgetItem:
        classification = group.get("classification") or {}
        artifact_count = len(group.get("artifacts") or [])
        item = QTreeWidgetItem(
            [
                f"{group['repo_name']}  [{_verdict_badge(classification)}]",
                "",
                group["repo_path"],
                f"{artifact_count} artifact(s)",
                "",
                "",
                "",
            ]
        )
        item.setData(
            0,
            Qt.ItemDataRole.UserRole,
            {"kind": "repository", "repo_path": group["repo_path"], "group": group},
        )
        evidence = _classification_summary(classification)
        tip = [group["repo_path"], _verdict_badge(classification)]
        if evidence:
            tip.append(evidence)
        if group.get("error"):
            tip.append(f"scan error: {group['error']}")
        elif group.get("errors"):
            tip.append(f"{len(group['errors'])} malformed pom.xml file(s)")
        item.setToolTip(0, "\n".join(tip))
        for column in range(len(_COLUMNS)):
            item.setToolTip(column, item.toolTip(0))
        for usage in sorted(group.get("usages", []), key=lambda u: (
            u.group_id.casefold(), u.artifact_id.casefold(), compare_versions(u.version, "")
        )):
            item.addChild(_usage_item(usage, name=usage.coordinates))
        return item

    def _render() -> None:
        index = state.get("index")
        if not isinstance(index, ArtifactIndex):
            return
        query = search_edit.text().strip()
        state["last_query"] = query
        filters = _selected_filters()
        rows = index.search(
            query,
            j2ee_only=j2ee_only.isChecked(),
            **filters,
        )
        summary = index.summary(rows)
        scanned = summary["repos_total"]
        eligible = summary["repos_j2ee"]
        card_repos.setText(f"{eligible}/{scanned}")
        card_artifacts.setText(str(summary["artifacts"]))
        card_duplicates.setText(str(summary["duplicates"]))

        # Partial-scan banner (failed repos / malformed POMs).
        problems: list[str] = []
        if summary["repos_failed"]:
            problems.append(f"{summary['repos_failed']} repositor(ies) could not be scanned")
        if summary["pom_errors"]:
            problems.append(f"{summary['pom_errors']} malformed pom.xml file(s) skipped")
        if problems:
            banner.setText("Partial scan — " + "; ".join(problems) + ". Results are incomplete.")
            banner.show()
        else:
            banner.hide()

        tree.setSortingEnabled(False)
        tree.clear()
        group_by = state["group_by"]

        if not state["repos"]:
            _set_state_message("This group has no repositories yet.")
            status.setText("No repositories in this group.")
            return
        if j2ee_only.isChecked() and eligible == 0 and not query:
            _set_state_message(
                "No J2EE projects found in this group.\n"
                "Classification is evidence-based (packaging, EE APIs, descriptors) — "
                "review it under Classification… or search all repositories instead.",
                "Search all repositories",
                lambda: (j2ee_only.setChecked(False), _render()),
            )
            status.setText(f"0 eligible J2EE repositories of {scanned} scanned.")
            return
        if not rows:
            _set_state_message(
                "No artifacts match your search." if query else "No dependencies found.",
            )
            status.setText(
                f"0 artifacts · scanned {scanned} repositor(ies) · {eligible} eligible J2EE"
            )
            return

        _hide_state_message()
        if group_by == _GROUP_BY_REPOSITORY:
            for group in index.repository_groups(rows):
                tree.addTopLevelItem(_repo_item(group))
        elif group_by == _GROUP_BY_FLAT:
            for usage in sorted(
                rows,
                key=lambda u: (u.group_id.casefold(), u.artifact_id.casefold(),
                               u.repo_name.casefold(), compare_versions(u.version, "")),
            ):
                tree.addTopLevelItem(_usage_item(usage))
        else:
            for group in index.artifact_groups(rows):
                tree.addTopLevelItem(_artifact_item(group))
        if group_by != _GROUP_BY_FLAT:
            tree.expandAll()
        tree.setSortingEnabled(True)

        duplicates = summary["duplicates"]
        status.setText(
            f"{summary['artifacts']} artifact(s) · {summary['usages']} usage(s) · "
            f"{duplicates} duplicate(s) · {eligible}/{scanned} J2EE repositor(ies)"
            + (" · partial scan" if problems else "")
        )

    # ------------------------------------------------------- classification UI
    def _open_classification_dialog() -> None:
        index = state.get("index")
        if not isinstance(index, ArtifactIndex) or not state["repos"]:
            return
        dialog = QDialog(root)
        dialog.setObjectName("explorerClassificationDialog")
        dialog.setWindowTitle(f"J2EE classification — {state['group'] or 'Ungrouped'}")
        dialog.resize(880, 520)
        d_layout = QVBoxLayout(dialog)
        d_layout.addWidget(styled_label(
            "Verdicts come from Maven evidence — packaging, EE/Jakarta APIs, "
            "deployment descriptors and EAR/WAR structure. Spring Boot alone is "
            "never J2EE evidence. Overrides survive rescans.",
            "hint",
        ))
        table_headers = ("Repository", "Verdict", "Confidence", "Evidence")
        entries: list[dict] = []
        for repo in state["repos"]:
            name, path = repo[0], repo[1]
            entries.append(
                {"name": name, "path": path, "classification": index.classification_for(path)}
            )
        grid = QTableWidget(0, len(table_headers))
        grid.setObjectName("explorerClassificationTable")
        grid.setHorizontalHeaderLabels(list(table_headers))
        grid.verticalHeader().setVisible(False)
        grid.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        grid.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        grid.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        grid.setAlternatingRowColors(True)
        grid.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        grid.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        grid.setRowCount(len(entries))
        for row_index, entry in enumerate(entries):
            classification = entry["classification"] or {}
            cells = (
                entry["name"],
                VERDICT_LABELS.get(str(classification.get("verdict") or ""), "?"),
                str(classification.get("confidence") or ""),
                _classification_summary(classification),
            )
            for column, value in enumerate(cells):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(entry["path"])
                grid.setItem(row_index, column, cell)
        d_layout.addWidget(grid, 1)

        override_row = QHBoxLayout()
        override_label = styled_label("Override for selected repository:", "hint")
        override_combo = QComboBox()
        override_combo.setObjectName("explorerOverrideCombo")
        override_combo.addItem("Auto (evidence)", OVERRIDE_AUTO)
        override_combo.addItem("J2EE", OVERRIDE_J2EE)
        override_combo.addItem("Non-J2EE", OVERRIDE_NON_J2EE)
        apply_btn = button("Apply", "primary")
        apply_btn.setObjectName("explorerOverrideApply")
        override_row.addWidget(override_label)
        override_row.addWidget(override_combo, 1)
        override_row.addWidget(apply_btn)
        d_layout.addLayout(override_row)
        close_btn = button("Close", "ghost")
        close_btn.setObjectName("explorerClassificationClose")
        close_btn.clicked.connect(dialog.accept)
        d_layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

        def _selected_path() -> str:
            row = grid.currentRow()
            if 0 <= row < len(entries):
                return entries[row]["path"]
            return ""

        def _sync_combo() -> None:
            path = _selected_path()
            current = overrides.get(path, OVERRIDE_AUTO)
            index_of = override_combo.findData(current)
            override_combo.setCurrentIndex(index_of if index_of >= 0 else 0)

        grid.currentItemChanged.connect(lambda _c, _p: _sync_combo())

        def _apply_override() -> None:
            path = _selected_path()
            if not path:
                return
            value = override_combo.currentData() or OVERRIDE_AUTO
            if value == OVERRIDE_AUTO:
                overrides.pop(path, None)
            else:
                overrides[path] = value
            _save_overrides()
            entry = index.repo_info(path)
            # Re-apply the override from the cached scan — no POM re-parse.
            reclassify_entry(entry, overrides)
            index.set_classification(path, entry.get("classification") or {})
            row = grid.currentRow()
            if 0 <= row < len(entries):
                classification = index.classification_for(path)
                entries[row]["classification"] = classification
                grid.item(row, 1).setText(
                    VERDICT_LABELS.get(str(classification.get("verdict") or ""), "?")
                )
                grid.item(row, 2).setText(str(classification.get("confidence") or ""))
                grid.item(row, 3).setText(_classification_summary(classification))
            _refresh_filter_combos()
            _render()

        apply_btn.clicked.connect(_apply_override)
        dialog.exec()

    # ---------------------------------------------------------- context menu
    def _selected_usage():
        item = tree.currentItem()
        if item is None:
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("kind") == "usage":
            return data.get("usage")
        if data.get("kind") == "artifact" and data.get("group"):
            group = data["group"]
            return (group.get("usages") or [None])[0]
        if data.get("kind") == "repository" and data.get("group"):
            artifacts = data["group"].get("artifacts") or []
            if artifacts and artifacts[0]["usages"]:
                return artifacts[0]["usages"][0]
        return None

    def _open_repo_dependency_view() -> None:
        usage = _selected_usage()
        if usage is not None and usage.repo_path:
            open_dependency_view(usage.repo_path)

    def _copy_gav() -> None:
        usage = _selected_usage()
        if usage is None:
            return
        QApplication.clipboard().setText(usage.gav())
        status.setText(f"Copied {usage.gav()}")

    def _reveal_pom() -> None:
        usage = _selected_usage()
        if usage is None or not usage.pom_path or not os.path.isfile(usage.pom_path):
            status.setText("No pom.xml available for the selected row.")
            return
        try:
            subprocess.run(["open", "-R", usage.pom_path], check=False)
        except OSError as exc:
            status.setText(f"Reveal failed: {exc}")

    def _open_pom() -> None:
        usage = _selected_usage()
        if usage is None or not usage.pom_path or not os.path.isfile(usage.pom_path):
            status.setText("No pom.xml available for the selected row.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(usage.pom_path))

    def _show_context_menu(pos) -> None:
        item = tree.itemAt(pos)
        if item is not None:
            tree.setCurrentItem(item)
        if _selected_usage() is None:
            return
        menu = QMenu(tree)
        menu.addAction("Open repository Dependency view").triggered.connect(
            _open_repo_dependency_view
        )
        menu.addSeparator()
        menu.addAction("Copy GAV").triggered.connect(_copy_gav)
        menu.addAction("Open pom.xml").triggered.connect(_open_pom)
        menu.addAction("Reveal pom.xml").triggered.connect(_reveal_pom)
        menu.exec(tree.viewport().mapToGlobal(pos))

    # ----------------------------------------------------------------- groups
    def _rebuild_group_combo() -> None:
        group_combo.blockSignals(True)
        group_combo.clear()
        for key in groups_provider():
            group_combo.addItem(key or "Ungrouped", key)
        index_of = group_combo.findData(state["group"])
        group_combo.setCurrentIndex(index_of if index_of >= 0 else 0)
        state["group"] = group_combo.currentData()
        group_combo.blockSignals(False)

    def _on_group_changed(_index: int) -> None:
        new_group = group_combo.currentData()
        if new_group == state["group"] and state["loaded"]:
            return
        state["group"] = new_group
        state["loaded"] = False
        tree.clear()
        _scan()

    def _on_group_by_changed(_index: int) -> None:
        state["group_by"] = (
            _GROUP_BY_REPOSITORY
            if groupby_combo.currentIndex() == 1
            else _GROUP_BY_FLAT
            if groupby_combo.currentIndex() == 2
            else _GROUP_BY_ARTIFACT
        )
        _render()

    # ----------------------------------------------------------------- wiring
    filter_timer = QTimer(root)
    filter_timer.setSingleShot(True)
    filter_timer.setInterval(150)
    filter_timer.timeout.connect(_render)

    search_edit.textChanged.connect(lambda _t: filter_timer.start())
    j2ee_only.toggled.connect(lambda _c: _render())
    scope_combo.currentIndexChanged.connect(lambda _i: _render())
    origin_combo.currentIndexChanged.connect(lambda _i: _render())
    version_combo.currentIndexChanged.connect(lambda _i: _render())
    repo_combo.currentIndexChanged.connect(lambda _i: _render())
    groupby_combo.currentIndexChanged.connect(_on_group_by_changed)
    group_combo.currentIndexChanged.connect(_on_group_changed)
    refresh_btn.clicked.connect(_scan)
    classify_btn.clicked.connect(_open_classification_dialog)
    tree.customContextMenuRequested.connect(_show_context_menu)
    tree.itemDoubleClicked.connect(lambda _item, _column: _open_repo_dependency_view())

    if events is not None:
        def _on_setting_changed(key: str = None, value=None) -> None:
            if key == "appearance.theme" and state.get("loaded"):
                _render()  # re-tint divergence rows for the new theme

        from devworkbench.services.configuration_service import TOPIC_SETTINGS_CHANGED

        events.subscribe(TOPIC_SETTINGS_CHANGED, _on_setting_changed)

    # ----------------------------------------------------------------- public
    _rebuild_group_combo()
    root.state = state  # type: ignore[attr-defined]
    root.overrides = overrides  # type: ignore[attr-defined]
    root.rescan = _scan  # type: ignore[attr-defined]
    root.render = _render  # type: ignore[attr-defined]
    root.open_classification = _open_classification_dialog  # type: ignore[attr-defined]
    root.open_repo_dependency_view = _open_repo_dependency_view  # type: ignore[attr-defined]
    root.ensure_scanned = (  # type: ignore[attr-defined]
        (lambda: _scan()) if not state["loaded"] else (lambda: None)
    )
    root.visible_statuses = lambda: [  # type: ignore[attr-defined]
        str(status.text()), str(state_label.text()) if state_label.isVisible() else ""
    ]
    # First scan as soon as control returns to the event loop (the tab is
    # visible the moment it is created).
    QTimer.singleShot(0, _scan)
    return root
