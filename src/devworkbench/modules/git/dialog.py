"""RepoDialog — add/edit a favorite git repository folder.

Captures name, path and an optional group. Validation is local and
immediate (the path must exist and not already be a favorite); the view
persists the returned :class:`Favorite` after the dialog accepts.

``record()`` returns the ``Favorite`` the view should insert (add) or use
to update the existing row (edit). Paths are normalized to absolute form so
favorites stay comparable with ``os.path.abspath`` elsewhere.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, QThreadPool, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devworkbench.models.persistence import Favorite
from devworkbench.ui.widgets.common import button, form_row, styled_label
from devworkbench.workers.git_worker import GitWorker


class RepoDialog(QDialog):
    """Modal add/edit form for a favorite git repository folder."""

    def __init__(
        self,
        parent: QWidget | None = None,
        record: Favorite | None = None,
        existing_groups: tuple[str, ...] = (),
        existing_paths: tuple[str, ...] = (),
    ) -> None:
        super().__init__(parent)
        self._record = record
        self._existing_paths = frozenset(existing_paths)
        self.setWindowTitle("Edit repository" if record is not None else "Add repository")
        self.setMinimumWidth(440)
        self._build(existing_groups)
        if record is not None:
            self._load(record)

    # -- construction -------------------------------------------------------

    def _build(self, existing_groups: tuple[str, ...]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        intro = QLabel(
            "Pin a git repository folder for quick operations. Repositories "
            "are organized into groups on the Git home page."
            if self._record is None
            else "Update the repository details below."
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. dev_work_bench")
        self.name_row = form_row("Name *", self.name_edit, "A short label for this repository.")
        layout.addWidget(self.name_row)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("/Users/me/Projects/my-repo")
        browse = QPushButton("Browse…")
        browse.setProperty("class", "ghost")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._browse_path)
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)
        controls_layout.addWidget(self.path_edit, 1)
        controls_layout.addWidget(browse)
        self.path_row = form_row("Path *", controls, "Full path to the repository folder.")
        layout.addWidget(self.path_row)

        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        self.group_combo.addItem("")  # empty selection = ungrouped
        for group in existing_groups:
            self.group_combo.addItem(group)
        self.group_combo.lineEdit().setPlaceholderText("Ungrouped — or type a new group")
        self.group_row = form_row(
            "Group",
            self.group_combo,
            "e.g. Work, Personal, Open Source. Leave empty for no group.",
        )
        layout.addWidget(self.group_row)

        layout.addStretch(1)

        # Per-field error labels (styled like the Settings/SSH pages).
        self._errors: dict[str, QLabel] = {}
        self._error_widgets: dict[str, QLineEdit] = {
            "name": self.name_edit,
            "path": self.path_edit,
        }
        for key, row in (("name", self.name_row), ("path", self.path_row)):
            error = QLabel("")
            error.setObjectName("fieldError")
            error.setWordWrap(True)
            error.hide()
            row.layout().addWidget(error)
            self._errors[key] = error

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setProperty("class", "ghost")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setProperty("class", "primary")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    # -- data ------------------------------------------------------------------

    def _load(self, record: Favorite) -> None:
        self.name_edit.setText(record.label)
        self.path_edit.setText(record.ref)
        index = self.group_combo.findText(
            record.group_name or "", Qt.MatchFlag.MatchFixedString
        )
        if record.group_name and index < 0:
            self.group_combo.addItem(record.group_name)
            index = self.group_combo.count() - 1
        self.group_combo.setCurrentIndex(index if index >= 0 else 0)

    def record(self) -> Favorite:
        """The validated favorite (path normalized to absolute form)."""
        return Favorite(
            kind="folder",
            ref=os.path.abspath(os.path.expanduser(self.path_edit.text().strip())),
            label=self.name_edit.text().strip(),
            group_name=self.group_combo.currentText().strip(),
        )

    # -- validation ---------------------------------------------------------------

    def validate(self) -> dict[str, str]:
        """Validate the form; returns {field: message} for failing fields."""
        errors: dict[str, str] = {}
        name = self.name_edit.text().strip()
        if not name:
            errors["name"] = "A name is required."
        elif len(name) > 80:
            errors["name"] = "Name is too long (max 80 characters)."

        path = self.path_edit.text().strip()
        if not path:
            errors["path"] = "A path is required."
        else:
            expanded = os.path.abspath(os.path.expanduser(path))
            if not os.path.isdir(expanded):
                errors["path"] = f"Folder not found: {expanded}"
            elif expanded in self._existing_paths:
                errors["path"] = "This folder is already in your favorites."
        return errors

    def show_errors(self, errors: dict[str, str]) -> None:
        for key, label in self._errors.items():
            message = errors.get(key)
            if message:
                label.setText(message)
                label.show()
            else:
                label.hide()
            widget = self._error_widgets.get(key)
            if widget is None:
                continue
            widget.setProperty("invalid", bool(message))
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _on_save(self) -> None:
        errors = self.validate()
        self.show_errors(errors)
        if errors:
            return
        self.accept()

    # -- helpers ---------------------------------------------------------------

    def _browse_path(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose repository folder",
            self.path_edit.text().strip() or os.path.expanduser("~"),
        )
        if chosen:
            self.path_edit.setText(chosen)


def rename_favorite_group(favorites_repo, old_name: str, new_name: str) -> str | None:
    """Rename a group across all folder favorites.

    Returns an error message, or ``None`` on success.
    """
    old = (old_name or "").strip()
    new = (new_name or "").strip()
    if not old:
        return "Ungrouped cannot be renamed."
    if not new:
        return "A group name is required."
    if new == old:
        return "That is the current name — nothing to rename."
    counts: dict[str, int] = {}
    for favorite in favorites_repo.by_kind("folder", limit=None):
        group = (favorite.group_name or "").strip()
        if group:
            counts[group] = counts.get(group, 0) + 1
    other_names = {g.casefold() for g in counts if g != old}
    if new.casefold() in other_names:
        return f"A group named “{new}” already exists — use Merge instead."
    for favorite in favorites_repo.by_kind("folder", limit=None):
        if (favorite.group_name or "").strip() == old:
            favorite.group_name = new
            favorites_repo.update(favorite)
    return None


class GroupManagerDialog(QDialog):
    """Organize repository groups on the Git home page.

    Rename a group (all its repositories at once), merge two groups into
    one, or delete a group (its repositories move to Ungrouped). Changes are
    applied inline through the favorites repository — no nested modals — so
    every operation is unambiguous and testable. The Git view refreshes its
    landing list after ``exec()`` returns.
    """

    def __init__(self, parent: QWidget | None, favorites_repo) -> None:
        super().__init__(parent)
        self._repo = favorites_repo
        self.setWindowTitle("Manage groups")
        self.setMinimumWidth(520)
        self.setMinimumHeight(440)
        self._build()
        self._reload_groups()

    # -- construction -------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Manage groups")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        intro = QLabel(
            "Rename a group, merge two groups, or delete a group — "
            "its repositories move to Ungrouped."
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        list_frame = QFrame()
        list_frame.setObjectName("groupManagePanel")
        list_layout = QVBoxLayout(list_frame)
        list_layout.setContentsMargins(10, 10, 10, 10)
        list_layout.setSpacing(8)
        list_heading = QLabel("GROUPS")
        list_heading.setObjectName("groupsHeading")
        list_layout.addWidget(list_heading)

        self.group_list = QListWidget()
        self.group_list.setObjectName("groupManageList")
        self.group_list.setSpacing(4)
        self.group_list.setUniformItemSizes(False)
        self.group_list.currentRowChanged.connect(lambda _row: self._sync_actions())
        list_layout.addWidget(self.group_list, 1)
        layout.addWidget(list_frame, 1)

        # Action row.
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.rename_button = button("Rename…", "ghost")
        self.rename_button.setObjectName("renameGroupButton")
        self.merge_button = button("Merge…", "ghost")
        self.merge_button.setObjectName("mergeGroupButton")
        self.delete_button = button("Delete", "danger")
        self.delete_button.setObjectName("deleteGroupButton")
        actions.addWidget(self.rename_button)
        actions.addWidget(self.merge_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        close_button = button("Close", "ghost")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        # Inline editor panels (one at a time, non-modal).
        editor_frame = QFrame()
        editor_frame.setObjectName("groupManageEditor")
        editor_layout = QVBoxLayout(editor_frame)
        editor_layout.setContentsMargins(12, 12, 12, 12)
        editor_layout.setSpacing(8)
        self._editor = QStackedWidget()
        self._editor.addWidget(self._empty_panel())
        self._editor.addWidget(self._rename_panel())
        self._editor.addWidget(self._merge_panel())
        self._editor.addWidget(self._delete_panel())
        editor_layout.addWidget(self._editor)
        layout.addWidget(editor_frame)

        # Status lines: success hint + inline error (styled like field errors).
        self._hint = styled_label("", "hint")
        self._hint.setWordWrap(True)
        self._hint.hide()  # revealed by _set_hint on the first action
        layout.addWidget(self._hint)
        self._error_label = QLabel("")
        self._error_label.setObjectName("fieldError")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        layout.addWidget(self._error_label)

        self.rename_button.clicked.connect(self._show_rename)
        self.merge_button.clicked.connect(self._show_merge)
        self.delete_button.clicked.connect(self._show_delete)

    def _empty_panel(self) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(styled_label("Select a group to modify it.", "muted"))
        return panel

    def _rename_panel(self) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(6)
        panel_layout.addWidget(styled_label("Rename group to", "muted"))
        self.rename_edit = QLineEdit()
        self.rename_edit.setPlaceholderText("New group name")
        panel_layout.addWidget(self.rename_edit)
        row = QHBoxLayout()
        row.setSpacing(6)
        apply_button = button("Rename", "primary")
        apply_button.clicked.connect(self._apply_rename)
        cancel_button = button("Cancel", "ghost")
        cancel_button.clicked.connect(self._hide_editor)
        row.addStretch(1)
        row.addWidget(cancel_button)
        row.addWidget(apply_button)
        panel_layout.addLayout(row)
        return panel

    def _merge_panel(self) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(6)
        panel_layout.addWidget(styled_label("Merge the selected group into", "muted"))
        self.merge_combo = QComboBox()
        self.merge_combo.setObjectName("mergeTargetCombo")
        panel_layout.addWidget(self.merge_combo)
        row = QHBoxLayout()
        row.setSpacing(6)
        apply_button = button("Merge", "primary")
        apply_button.clicked.connect(self._apply_merge)
        cancel_button = button("Cancel", "ghost")
        cancel_button.clicked.connect(self._hide_editor)
        row.addStretch(1)
        row.addWidget(cancel_button)
        row.addWidget(apply_button)
        panel_layout.addLayout(row)
        return panel

    def _delete_panel(self) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(6)
        self.delete_label = styled_label("", "muted")
        self.delete_label.setWordWrap(True)
        panel_layout.addWidget(self.delete_label)
        row = QHBoxLayout()
        row.setSpacing(6)
        apply_button = button("Move to Ungrouped", "danger")
        apply_button.clicked.connect(self._apply_delete)
        cancel_button = button("Cancel", "ghost")
        cancel_button.clicked.connect(self._hide_editor)
        row.addStretch(1)
        row.addWidget(cancel_button)
        row.addWidget(apply_button)
        panel_layout.addLayout(row)
        return panel

    # -- data ------------------------------------------------------------------

    def _group_counts(self) -> dict[str, int]:
        """group name -> number of repositories in it (non-empty groups).

        Fetches *all* favorites (no cap) so groups on older repositories are
        still manageable even when the landing page limits its list.
        """
        counts: dict[str, int] = {}
        for favorite in self._repo.by_kind("folder", limit=None):
            group = (favorite.group_name or "").strip()
            if group:
                counts[group] = counts.get(group, 0) + 1
        return counts

    def _current_group(self) -> str | None:
        item = self.group_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _reload_groups(self) -> None:
        """Re-query groups and counts; restore the selection when it survives."""
        selected = self._current_group()
        counts = self._group_counts()
        self.group_list.clear()
        for group in sorted(counts, key=str.casefold):
            count = counts[group]
            noun = "repo" if count == 1 else "repos"
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, group)
            item.setSizeHint(QSize(0, 52))
            self.group_list.addItem(item)

            row = QWidget()
            row.setObjectName("groupManageRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 8, 12, 8)
            row_layout.setSpacing(10)
            name_label = QLabel(group)
            name_label.setObjectName("groupManageName")
            name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            row_layout.addWidget(name_label, 1)
            badge = QLabel(f"{count} {noun}")
            badge.setObjectName("groupManageBadge")
            badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            row_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignRight)
            self.group_list.setItemWidget(item, row)
        index = -1
        for i in range(self.group_list.count()):
            if self.group_list.item(i).data(Qt.ItemDataRole.UserRole) == selected:
                index = i
                break
        if index >= 0:
            self.group_list.setCurrentRow(index)
        elif self.group_list.count():
            self.group_list.setCurrentRow(0)
        self._sync_actions()

    # -- panels / actions --------------------------------------------------------

    def _sync_actions(self) -> None:
        has_selection = self._current_group() is not None
        self.rename_button.setEnabled(has_selection)
        self.merge_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)
        if not has_selection:
            self._hide_editor()
            self._set_hint("")

    def _hide_editor(self) -> None:
        self._editor.setCurrentIndex(0)
        self._error_label.hide()

    def _set_hint(self, text: str) -> None:
        self._hint.setText(text)
        self._hint.setVisible(bool(text))
        self._error_label.hide()

    def _error(self, text: str) -> None:
        self._hint.hide()
        self._error_label.setText(text)
        self._error_label.show()

    def _show_rename(self) -> None:
        group = self._current_group()
        if group is None:
            return
        self.rename_edit.setText(group)
        self.rename_edit.selectAll()
        self.rename_edit.setFocus()
        self._set_hint("")
        self._editor.setCurrentIndex(1)

    def _show_merge(self) -> None:
        group = self._current_group()
        if group is None:
            return
        self.merge_combo.clear()
        for other in sorted((g for g in self._group_counts() if g != group), key=str.casefold):
            self.merge_combo.addItem(other, other)
        if self.merge_combo.count() == 0:
            self._error("There is no other group to merge into.")
            return
        self._set_hint("")
        self._editor.setCurrentIndex(2)

    def _show_delete(self) -> None:
        group = self._current_group()
        if group is None:
            return
        count = self._group_counts().get(group, 0)
        noun = "repository" if count == 1 else "repositories"
        self.delete_label.setText(f"Move {count} {noun} from “{group}” to Ungrouped?")
        self._set_hint("")
        self._editor.setCurrentIndex(3)

    def _apply_rename(self) -> None:
        group = self._current_group()
        new_name = self.rename_edit.text().strip()
        if group is None:
            return
        error = rename_favorite_group(self._repo, group, new_name)
        if error:
            self._error(error)
            return
        self._hide_editor()
        self._reload_groups()
        # Select the renamed group if it still exists.
        for i in range(self.group_list.count()):
            if self.group_list.item(i).data(Qt.ItemDataRole.UserRole) == new_name:
                self.group_list.setCurrentRow(i)
                break
        self._set_hint(f"Renamed “{group}” to “{new_name}”.")

    def _apply_merge(self) -> None:
        source = self._current_group()
        target = self.merge_combo.currentData()
        if not source or not target:
            return
        for favorite in self._repo.by_kind("folder"):
            if (favorite.group_name or "").strip() == source:
                favorite.group_name = target
                self._repo.update(favorite)
        self._hide_editor()
        self._reload_groups()
        self._set_hint(f"Merged “{source}” into “{target}”.")

    def _apply_delete(self) -> None:
        group = self._current_group()
        if group is None:
            return
        for favorite in self._repo.by_kind("folder"):
            if (favorite.group_name or "").strip() == group:
                favorite.group_name = ""
                self._repo.update(favorite)
        self._hide_editor()
        self._reload_groups()
        self._set_hint(f"Moved “{group}” to Ungrouped.")


class ScanReposDialog(QDialog):
    """Scan a folder for nested git repositories and add the chosen ones.

    Picks a root folder, runs the ``find_repos`` git operation off the UI
    thread, then lists every discovered repository with a checkbox. Select
    one or several, pick a group (an existing one or a freshly typed name)
    and click "Add selected" — already-favorited paths are marked and
    skipped. ``added`` holds the favorites created by the last "Add" so the
    caller can refresh its lists and history.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        favorites_repo=None,
        existing_groups: tuple[str, ...] = (),
        executable: str = "git",
    ) -> None:
        super().__init__(parent)
        self._repo = favorites_repo
        self._executable = executable
        self._existing_paths = {
            favorite.ref for favorite in (favorites_repo.by_kind("folder") if favorites_repo is not None else ())
        }
        # Retained until their signals deliver (Worker retention contract).
        self._workers: list = []
        self.added: list[Favorite] = []
        self.setWindowTitle("Scan for repositories")
        self.setMinimumWidth(560)
        self.setMinimumHeight(460)
        self._build(existing_groups)

    # -- construction -------------------------------------------------------

    def _build(self, existing_groups: tuple[str, ...]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        intro = QLabel(
            "Scan a folder for git repositories in its subdirectories, then "
            "add the ones you want to a group."
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Root folder row.
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("/Users/me/Projects/workspace")
        browse = QPushButton("Browse…")
        browse.setProperty("class", "ghost")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._browse)
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)
        controls_layout.addWidget(self.path_edit, 1)
        controls_layout.addWidget(browse)
        layout.addWidget(form_row("Folder to scan", controls, "Git repositories are searched in its subdirectories."))

        scan_row = QHBoxLayout()
        scan_row.setSpacing(6)
        self.scan_button = button("Scan", "primary")
        self.scan_button.setObjectName("scanButton")
        self.scan_button.clicked.connect(self._scan)
        self.scan_status = styled_label("", "hint")
        self.scan_status.setObjectName("scanStatus")
        scan_row.addWidget(self.scan_button)
        scan_row.addWidget(self.scan_status, 1)
        layout.addLayout(scan_row)

        self.results_list = QListWidget()
        self.results_list.setObjectName("scanResults")
        self.results_list.setFrameStyle(0)
        layout.addWidget(self.results_list, 1)

        select_row = QHBoxLayout()
        select_row.setSpacing(6)
        select_all = button("Select all", "ghost")
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        select_none = button("Select none", "ghost")
        select_none.clicked.connect(lambda: self._set_all_checked(False))
        select_row.addWidget(select_all)
        select_row.addWidget(select_none)
        select_row.addStretch(1)
        layout.addLayout(select_row)

        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        self.group_combo.addItem("")  # empty selection = ungrouped
        for group in existing_groups:
            self.group_combo.addItem(group)
        self.group_combo.lineEdit().setPlaceholderText("Ungrouped — or type a new group")
        layout.addWidget(form_row(
            "Add to group",
            self.group_combo,
            "e.g. Work, Personal, Open Source. Leave empty for no group.",
        ))

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setProperty("class", "ghost")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        self.add_button = button("Add selected", "primary")
        self.add_button.setObjectName("addSelectedButton")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._add_selected)
        buttons.addWidget(cancel)
        buttons.addWidget(self.add_button)
        layout.addLayout(buttons)

    # -- scanning ------------------------------------------------------------------

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose folder to scan",
            self.path_edit.text().strip() or os.path.expanduser("~"),
        )
        if chosen:
            self.path_edit.setText(chosen)

    def _scan(self) -> None:
        """Kick off the (async) find_repos scan for the entered folder."""
        path = self.path_edit.text().strip()
        if not path or not os.path.isdir(os.path.abspath(os.path.expanduser(path))):
            self.scan_status.setText("Choose a folder that exists first.")
            return
        self.results_list.clear()
        self.add_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.scan_status.setText("Scanning subdirectories…")

        worker = GitWorker("find_repos", os.path.abspath(os.path.expanduser(path)), executable=self._executable)
        self._workers.append(worker)

        def done(result, current=worker) -> None:
            if current in self._workers:
                self._workers.remove(current)
            self.scan_button.setEnabled(True)
            repos = result.get("repos") or []
            self.show_results(repos)

        def failed(exc, current=worker) -> None:
            if current in self._workers:
                self._workers.remove(current)
            self.scan_button.setEnabled(True)
            self.scan_status.setText(f"Scan failed: {exc}")

        worker.signals.finished.connect(done)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def show_results(self, repos: list[str]) -> None:
        """Populate the results list from a scan (also used by tests)."""
        self.results_list.clear()
        if not repos:
            self.scan_status.setText("No git repositories found under this folder.")
            return
        for path in sorted(repos):
            name = os.path.basename(path.rstrip("/")) or path
            existing = path in self._existing_paths
            item = QListWidgetItem(f"{name}  ·  {path}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if existing:
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)  # greyed out
                item.setToolTip("Already in your favorites")
            else:
                item.setCheckState(Qt.CheckState.Checked)  # pre-selected by default
            self.results_list.addItem(item)
        self.scan_status.setText(f"Found {len(repos)} git repositories — check the ones to add.")
        self.add_button.setEnabled(True)

    # -- adding ----------------------------------------------------------------------

    def selected_paths(self) -> list[str]:
        """Full paths of the checked, enabled results."""
        selected: list[str] = []
        for i in range(self.results_list.count()):
            item = self.results_list.item(i)
            if not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
                continue
            if item.checkState() != Qt.CheckState.Checked:
                continue
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                selected.append(path)
        return selected

    def _set_all_checked(self, checked: bool) -> None:
        for i in range(self.results_list.count()):
            item = self.results_list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def _add_selected(self) -> None:
        """Insert the checked repositories as favorites; closes on success."""
        group = self.group_combo.currentText().strip()
        added: list[Favorite] = []
        for path in self.selected_paths():
            if path in self._existing_paths:
                continue
            favorite = Favorite(
                kind="folder",
                ref=os.path.abspath(path),
                label=os.path.basename(path.rstrip("/")) or path,
                group_name=group,
            )
            if self._repo is not None:
                self._repo.insert(favorite)
            self._existing_paths.add(path)
            added.append(favorite)
        self.added = added
        if added or self._repo is None:
            self.accept()


class EditGroupActionsDialog(QDialog):
    """Edit the custom Actions list for one repository group."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        group_name: str,
        actions: list[dict],
    ) -> None:
        super().__init__(parent)
        self._group_name = group_name
        self.setWindowTitle(f"Edit actions — {group_name or 'Ungrouped'}")
        self.setMinimumSize(560, 360)
        self._actions = [dict(item) for item in actions]
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        hint = QLabel(
            "Each action appears in the Actions menu for this group. "
            "Use {{name}} placeholders for prompts (same value for every repo). "
            'Example: git commit -m "{{message}}"'
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Label", "Command"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        add_btn = button("Add", "ghost")
        remove_btn = button("Remove", "ghost")
        up_btn = button("Move up", "ghost")
        down_btn = button("Move down", "ghost")
        add_btn.clicked.connect(self._add_row)
        remove_btn.clicked.connect(self._remove_row)
        up_btn.clicked.connect(lambda: self._move_row(-1))
        down_btn.clicked.connect(lambda: self._move_row(1))
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addWidget(up_btn)
        row.addWidget(down_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.error = styled_label("", "hint")
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for action in self._actions:
            self._append_row(
                str(action.get("label") or ""),
                str(action.get("command") or ""),
                str(action.get("id") or ""),
            )

    def _append_row(self, label: str, command: str, action_id: str = "") -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        label_item = QTableWidgetItem(label)
        label_item.setData(Qt.ItemDataRole.UserRole, action_id)
        self.table.setItem(row, 0, label_item)
        self.table.setItem(row, 1, QTableWidgetItem(command))

    def _add_row(self) -> None:
        self._append_row("New action", "git status", "")
        self.table.selectRow(self.table.rowCount() - 1)

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _move_row(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.table.rowCount():
            return
        label_item = self.table.item(row, 0)
        cmd_item = self.table.item(row, 1)
        label = label_item.text() if label_item else ""
        action_id = str(label_item.data(Qt.ItemDataRole.UserRole) or "") if label_item else ""
        command = cmd_item.text() if cmd_item else ""
        self.table.removeRow(row)
        self.table.insertRow(target)
        new_label = QTableWidgetItem(label)
        new_label.setData(Qt.ItemDataRole.UserRole, action_id)
        self.table.setItem(target, 0, new_label)
        self.table.setItem(target, 1, QTableWidgetItem(command))
        self.table.selectRow(target)

    def _save(self) -> None:
        actions: list[dict] = []
        for row in range(self.table.rowCount()):
            label_item = self.table.item(row, 0)
            cmd_item = self.table.item(row, 1)
            label = (label_item.text() if label_item else "").strip()
            command = (cmd_item.text() if cmd_item else "").strip()
            if not label or not command:
                self.error.setText("Every row needs a non-empty label and command.")
                self.error.show()
                return
            action_id = str(label_item.data(Qt.ItemDataRole.UserRole) or "") if label_item else ""
            actions.append({"id": action_id, "label": label, "command": command})
        self._result = actions
        self.accept()

    def result_actions(self) -> list[dict]:
        return list(getattr(self, "_result", []))


class CopyGroupActionsDialog(QDialog):
    """Copy selected actions from the current group into other groups."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        source_group: str,
        actions: list[dict],
        target_groups: tuple[str, ...],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Copy actions to groups")
        self.setMinimumSize(480, 400)
        self._actions = list(actions)
        self._build(source_group, target_groups)

    def _build(self, source_group: str, target_groups: tuple[str, ...]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(
            styled_label(
                f"Copy from “{source_group or 'Ungrouped'}”. "
                "Duplicate labels on a target are skipped.",
                "hint",
            )
        )

        layout.addWidget(QLabel("Actions to copy"))
        self.action_list = QListWidget()
        self.action_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for action in self._actions:
            item = QListWidgetItem(str(action.get("label") or "(unnamed)"))
            item.setData(Qt.ItemDataRole.UserRole, action)
            self.action_list.addItem(item)
        for i in range(self.action_list.count()):
            self.action_list.item(i).setSelected(True)
        layout.addWidget(self.action_list, 1)

        layout.addWidget(QLabel("Target groups"))
        self.group_list = QListWidget()
        self.group_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for group in target_groups:
            item = QListWidgetItem(group or "Ungrouped")
            item.setData(Qt.ItemDataRole.UserRole, group)
            self.group_list.addItem(item)
        layout.addWidget(self.group_list, 1)

        self.error = styled_label("", "hint")
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        selected_actions = [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.action_list.selectedItems()
        ]
        selected_groups = [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.group_list.selectedItems()
        ]
        if not selected_actions:
            self.error.setText("Select at least one action.")
            self.error.show()
            return
        if not selected_groups:
            self.error.setText("Select at least one target group.")
            self.error.show()
            return
        self._selected_actions = selected_actions
        self._selected_groups = selected_groups
        self.accept()

    def selected_actions(self) -> list[dict]:
        return list(getattr(self, "_selected_actions", []))

    def selected_groups(self) -> list[str]:
        return list(getattr(self, "_selected_groups", []))


class ActionPlaceholdersDialog(QDialog):
    """Collect values for {{placeholder}} tokens before a group action run."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        action_label: str,
        placeholders: list[str],
    ) -> None:
        super().__init__(parent)
        title = "Commit message" if placeholders == ["message"] else f"Values for {action_label}"
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self._placeholders = list(placeholders)
        self._fields: dict[str, QWidget] = {}
        self._build(action_label)

    def _build(self, action_label: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        intro = QLabel(
            f"Enter values for “{action_label}”. "
            "The same values are used for every repository in the group."
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)
        for name in self._placeholders:
            if name == "message":
                field = QPlainTextEdit()
                field.setPlaceholderText("Commit message")
                field.setFixedHeight(100)
            else:
                field = QLineEdit()
                field.setPlaceholderText(name)
            self._fields[name] = field
            form.addRow(name, field)
        layout.addLayout(form)

        self.error = styled_label("", "hint")
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        values: dict[str, str] = {}
        for name, field in self._fields.items():
            if isinstance(field, QPlainTextEdit):
                text = field.toPlainText().strip()
            else:
                text = field.text().strip()
            if not text:
                self.error.setText(f"“{name}” is required.")
                self.error.show()
                return
            values[name] = text
        self._values = values
        self.accept()

    def values(self) -> dict[str, str]:
        return dict(getattr(self, "_values", {}))


class RepoFilesDialog(QDialog):
    """Modal list of files for Local changes or Diff vs remote on one repo."""

    MODE_LOCAL = "local"
    MODE_REMOTE = "remote"

    def __init__(
        self,
        parent: QWidget | None,
        *,
        path: str,
        repo_name: str,
        mode: str,
        git_executable: str = "git",
        pending_workers: list | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self._mode = mode if mode in (self.MODE_LOCAL, self.MODE_REMOTE) else self.MODE_LOCAL
        self._exe = git_executable or "git"
        self._pending = pending_workers if pending_workers is not None else []
        self._busy = False
        title_mode = "Local changes" if self._mode == self.MODE_LOCAL else "Diff vs remote"
        self.setWindowTitle(f"{title_mode} — {repo_name or os.path.basename(path)}")
        self.setMinimumSize(560, 420)
        self._build()
        self._load()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self._subtitle = styled_label("", "hint")
        self._subtitle.setWordWrap(True)
        layout.addWidget(self._subtitle)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._refresh = button("Refresh", "ghost")
        self._refresh.clicked.connect(self._load)
        toolbar.addWidget(self._refresh)
        toolbar.addStretch(1)
        self._count = styled_label("", "hint")
        toolbar.addWidget(self._count)
        layout.addLayout(toolbar)

        self._error = styled_label("", "hint")
        self._error.setObjectName("fieldError")
        self._error.hide()
        layout.addWidget(self._error)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Status", "Path"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _load(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._refresh.setEnabled(False)
        self._error.hide()
        self._subtitle.setText("Loading…")
        self._count.setText("")
        op = (
            "working_tree_status"
            if self._mode == self.MODE_LOCAL
            else "diff_vs_upstream"
        )
        worker = GitWorker(op, self._path, executable=self._exe)
        self._pending.append(worker)

        def _done(result) -> None:
            self._busy = False
            self._refresh.setEnabled(True)
            if worker in self._pending:
                self._pending.remove(worker)
            self._apply(result if isinstance(result, dict) else {})

        def _err(exc) -> None:
            self._busy = False
            self._refresh.setEnabled(True)
            if worker in self._pending:
                self._pending.remove(worker)
            self._subtitle.setText("")
            self._error.setText(str(exc) if exc else "Failed")
            self._error.show()
            self._table.setRowCount(0)
            self._count.setText("0 files")

        worker.signals.finished.connect(_done)
        worker.signals.error.connect(_err)
        QThreadPool.globalInstance().start(worker)

    def _apply(self, result: dict) -> None:
        if self._mode == self.MODE_LOCAL:
            if not result.get("ok"):
                self._subtitle.setText("")
                self._error.setText(str(result.get("error") or "Could not read status"))
                self._error.show()
                self._fill([])
                return
            self._subtitle.setText(f"Working tree · {self._path}")
            self._fill(list(result.get("files") or []))
            return

        upstream = result.get("upstream") or "—"
        dirty_hint = int(result.get("dirty_hint_count") or 0)
        if not result.get("ok"):
            self._subtitle.setText(f"Comparing to {upstream}" if result.get("upstream") else "")
            self._error.setText(str(result.get("error") or "Could not diff vs remote"))
            self._error.show()
            self._fill([])
            return
        hint = ""
        if dirty_hint:
            hint = f" · Also {dirty_hint} local uncommitted change(s) — use Local changes…"
        self._subtitle.setText(f"Comparing to {upstream}{hint}")
        self._fill(list(result.get("files") or []))

    def _fill(self, files: list[dict]) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._table.setRowCount(len(files))
        for index, row in enumerate(files):
            status = str(row.get("status") or row.get("code") or "")
            path = str(row.get("path") or "")
            status_item = QTableWidgetItem(status)
            path_item = QTableWidgetItem(path)
            status_item.setToolTip(str(row.get("code") or status))
            path_item.setToolTip(path)
            self._table.setItem(index, 0, status_item)
            self._table.setItem(index, 1, path_item)
        self._table.setSortingEnabled(True)
        if not files and not self._error.isVisible():
            self._count.setText("Working tree clean" if self._mode == self.MODE_LOCAL else "No differences vs remote")
        else:
            self._count.setText(f"{len(files)} file(s)")
