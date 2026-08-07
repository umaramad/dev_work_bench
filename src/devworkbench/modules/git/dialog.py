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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from devworkbench.models.persistence import Favorite
from devworkbench.ui.widgets.common import button, form_row, styled_label


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
        self.setMinimumWidth(480)
        self.setMinimumHeight(380)
        self._build()
        self._reload_groups()

    # -- construction -------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        intro = QLabel(
            "Repositories are organized into groups. Rename a group, merge two "
            "groups, or delete a group — its repositories move to Ungrouped."
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.group_list = QListWidget()
        self.group_list.setObjectName("groupList")
        self.group_list.setFrameStyle(0)
        self.group_list.currentRowChanged.connect(lambda _row: self._sync_actions())
        layout.addWidget(self.group_list, 1)

        # Action row.
        actions = QHBoxLayout()
        actions.setSpacing(6)
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
        self._editor = QStackedWidget()
        self._editor.addWidget(self._empty_panel())
        self._editor.addWidget(self._rename_panel())
        self._editor.addWidget(self._merge_panel())
        self._editor.addWidget(self._delete_panel())
        layout.addWidget(self._editor)

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
            item = QListWidgetItem(f"{group}  ·  {counts[group]}")
            item.setData(Qt.ItemDataRole.UserRole, group)
            self.group_list.addItem(item)
        index = -1
        for i in range(self.group_list.count()):
            if self.group_list.item(i).data(Qt.ItemDataRole.UserRole) == selected:
                index = i
                break
        if index >= 0:
            self.group_list.setCurrentRow(index)
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
        if not new_name:
            self._error("A group name is required.")
            return
        if new_name == group:
            self._error("That is the current name — nothing to rename.")
            return
        # Block case-insensitive collisions with *other* groups, while still
        # allowing a pure case change of the selected group ("Work" -> "work").
        other_names = {g.casefold() for g in self._group_counts() if g != group}
        if new_name.casefold() in other_names:
            self._error(f"A group named “{new_name}” already exists — use Merge instead.")
            return
        for favorite in self._repo.by_kind("folder"):
            if (favorite.group_name or "").strip() == group:
                favorite.group_name = new_name
                self._repo.update(favorite)
        self._hide_editor()
        self._reload_groups()
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
