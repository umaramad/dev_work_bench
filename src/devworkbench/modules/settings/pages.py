"""Settings pages — nine category forms bound to the ConfigurationService.

Each page is declarative: it names the setting keys that belong to it and the
base class builds the controls from the schema (``SettingDef``), wires
validation error labels, secret handling and dirty tracking. Pages never
touch SQLite or the Keychain — every read/write goes through the service.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from devworkbench.services.configuration_service import SettingDef, SettingKind
from devworkbench.ui.theme import current_colors

ACCENT_CHOICES: tuple[tuple[str, str], ...] = (
    ("#5b8def", "Blue"),
    ("#4cc38a", "Green"),
    ("#e2a94f", "Amber"),
    ("#e06c6c", "Red"),
    ("#b98ce8", "Purple"),
)


# ---------------------------------------------------------------------------
# Field — binds a control to a setting key and knows how to read/write it
# ---------------------------------------------------------------------------


class Field:
    """A setting key + widget pair with value get/set (validation lives in the
    ConfigurationService — pages never duplicate the rules).

    Custom controls expose ``value()``/``set_value()`` and are dispatched by
    duck-typing after the built-in control types.
    """

    def __init__(self, key: str, widget: QWidget) -> None:
        self.key = key
        self.widget = widget

    def value(self):
        widget = self.widget
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, QSpinBox):
            return widget.value()
        if isinstance(widget, QDoubleSpinBox):
            return widget.value()
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        if hasattr(widget, "value"):
            return widget.value()
        return None

    def set_value(self, value) -> None:
        widget = self.widget
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QComboBox):
            index = widget.findData(value)
            widget.setCurrentIndex(index if index >= 0 else 0)
        elif isinstance(widget, QSpinBox):
            widget.setValue(int(value))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value))
        elif isinstance(widget, QLineEdit):
            widget.setText("" if value is None else str(value))
        elif hasattr(widget, "set_value"):
            widget.set_value(value)


# ---------------------------------------------------------------------------
# Custom controls
# ---------------------------------------------------------------------------


class _AccentSwatches(QWidget):
    """Exclusive row of accent color dots (radio-like, painted swatches)."""

    def __init__(self) -> None:
        super().__init__()
        self._colors = [hex_color for hex_color, _ in ACCENT_CHOICES]
        self._buttons: list[QPushButton] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for hex_color, name in ACCENT_CHOICES:
            button = QPushButton()
            button.setFixedSize(26, 26)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(name)
            self._group.addButton(button)
            self._buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(1)
        self.changed_signal = self._group.buttonClicked
        self.set_value(self._colors[0])

    def value(self) -> str:
        for button, color in zip(self._buttons, self._colors):
            if button.isChecked():
                return color
        return self._colors[0]

    def set_value(self, color: str) -> None:
        for button, candidate in zip(self._buttons, self._colors):
            selected = candidate == color
            button.setChecked(selected)
            button.setStyleSheet(self._swatch_style(candidate, selected))

    @staticmethod
    def _swatch_style(color: str, selected: bool) -> str:
        border = current_colors()["accent"] if selected else current_colors()["border2"]
        return f"background:{color}; border:2px solid {border}; border-radius:6px;"


class _FontSizeRow(QWidget):
    """Slider + live preview for the base font size."""

    def __init__(self) -> None:
        super().__init__()
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(11, 17)
        self._slider.setValue(13)
        self._preview = QLabel("13")
        self._preview.setObjectName("muted")
        self._preview.setFixedWidth(22)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._slider.valueChanged.connect(lambda v: self._preview.setText(str(v)))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._preview)
        self.changed_signal = self._slider.valueChanged

    def value(self) -> int:
        return self._slider.value()

    def set_value(self, value) -> None:
        self._slider.setValue(int(value))


# ---------------------------------------------------------------------------
# SettingsPage base
# ---------------------------------------------------------------------------


class SettingsPage(QWidget):
    """A single settings category: builds controls from the schema."""

    page_id = ""
    title = ""
    icon = ""
    subtitle = ""
    # (group title or None, (setting keys, ...))
    groups: tuple[tuple[str | None, tuple[str, ...]], ...] = ()

    changed = Signal()
    reset_all_requested = Signal()

    def __init__(self, service, icons, ctx=None) -> None:
        super().__init__()
        self._service = service
        self._icons = icons
        self._ctx = ctx
        self._fields: list[Field] = []
        self._secret_keys: set[str] = set()
        self._error_labels: dict[str, QLabel] = {}
        self._dirty = False
        self._loading = False
        self._build()
        self.load()

    # -- construction ---------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(12)
        for group_title, keys in self.groups:
            if group_title:
                box = QGroupBox(group_title)
                group_layout = QVBoxLayout(box)
                group_layout.setContentsMargins(12, 14, 12, 12)
                group_layout.setSpacing(8)
                layout.addWidget(box)
            else:
                box = QWidget()
                group_layout = QVBoxLayout(box)
                group_layout.setContentsMargins(0, 0, 0, 0)
                group_layout.setSpacing(8)
                layout.addWidget(box)
            for key in keys:
                spec = self._service.definition(key)
                if spec is None:
                    continue
                self._add_field(spec, group_layout)
        self._build_extra(layout)
        layout.addStretch(1)

    def _build_extra(self, layout: QVBoxLayout) -> None:
        """Hook for pages that add non-schema content (e.g. Advanced)."""

    def _make_control(self, spec: SettingDef) -> QWidget:
        kind = spec.kind
        if kind is SettingKind.BOOL:
            check = QCheckBox(spec.label)
            check.setCursor(Qt.CursorShape.PointingHandCursor)
            return check
        if kind is SettingKind.ENUM:
            box = QComboBox()
            for choice in spec.choices:
                box.addItem(choice.replace("-", " ").replace("_", " ").title(), choice)
            return box
        if kind is SettingKind.INT:
            spin = QSpinBox()
            spin.setRange(spec.min if spec.min is not None else 0, spec.max if spec.max is not None else 10000)
            if spec.step:
                spin.setSingleStep(spec.step)
            return spin
        if kind is SettingKind.FLOAT:
            spin = QDoubleSpinBox()
            spin.setRange(spec.min if spec.min is not None else 0, spec.max if spec.max is not None else 100)
            spin.setSingleStep((spec.step or 10) / 10)
            spin.setDecimals(1)
            return spin
        if kind is SettingKind.SECRET:
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setClearButtonEnabled(True)
            return edit
        if kind is SettingKind.PATH:
            edit = QLineEdit()
            edit.setPlaceholderText("Choose…")
            return edit
        edit = QLineEdit()
        return edit

    def _add_field(self, spec: SettingDef, group_layout: QVBoxLayout) -> None:
        if spec.kind is SettingKind.SECRET:
            self._secret_keys.add(spec.key)
        widget = self._make_control(spec)
        field = Field(spec.key, widget)
        row, error_label = self._field_row(spec, field)
        group_layout.addWidget(row)
        self._fields.append(field)
        self._error_labels[spec.key] = error_label
        self._connect_field(field)

    def _field_row(self, spec: SettingDef, field: Field) -> tuple[QWidget, QLabel]:
        row = QWidget()
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        widget = field.widget
        if spec.kind is SettingKind.BOOL:
            row_layout.addWidget(widget)
        else:
            caption = QLabel(spec.label)
            caption.setObjectName("muted")
            row_layout.addWidget(caption)
            if spec.kind is SettingKind.PATH and spec.browse:
                control_row = QWidget()
                control_layout = QHBoxLayout(control_row)
                control_layout.setContentsMargins(0, 0, 0, 0)
                control_layout.setSpacing(6)
                control_layout.addWidget(widget, 1)
                browse = QPushButton("Browse…")
                browse.setProperty("class", "ghost")
                browse.setCursor(Qt.CursorShape.PointingHandCursor)
                browse.clicked.connect(lambda: self._browse(spec, widget))
                control_layout.addWidget(browse)
                row_layout.addWidget(control_row)
            else:
                row_layout.addWidget(widget)

        if spec.kind is SettingKind.SECRET:
            hint = QLabel("Stored in the macOS Keychain — never in the database")
            hint.setObjectName("keychainHint")
            row_layout.addWidget(hint)

        error = QLabel("")
        error.setObjectName("fieldError")
        error.setWordWrap(True)
        error.hide()
        row_layout.addWidget(error)

        if spec.hint and spec.kind is not SettingKind.SECRET:
            hint = QLabel(spec.hint)
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            row_layout.addWidget(hint)
        return row, error

    def _connect_field(self, field: Field) -> None:
        widget = field.widget
        if isinstance(widget, QLineEdit):
            widget.textChanged.connect(self._on_edit)
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(self._on_edit)
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(self._on_edit)
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.valueChanged.connect(self._on_edit)
        elif hasattr(widget, "changed_signal"):
            widget.changed_signal.connect(self._on_edit)

    def _browse(self, spec: SettingDef, edit: QLineEdit) -> None:
        start = edit.text().strip() or str(Path.home())
        if spec.browse == "dir":
            chosen = QFileDialog.getExistingDirectory(self, f"Choose folder — {spec.label}", start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, f"Choose file — {spec.label}", start)
        if chosen:
            edit.setText(chosen)

    # -- lifecycle ---------------------------------------------------------------

    def load(self) -> None:
        """Repopulate controls from the service (used on init, apply, cancel)."""
        self._loading = True
        try:
            for field in self._fields:
                if field.key in self._secret_keys:
                    present = self._service.has_secret(field.key)
                    field.widget.clear()
                    field.widget.setPlaceholderText(
                        "•••••••• (saved in Keychain)" if present else "Not set — stored in macOS Keychain"
                    )
                else:
                    field.set_value(self._service.get(field.key))
        finally:
            self._loading = False
        self.clear_errors()
        self._dirty = False

    def collect(self) -> dict:
        """Read the form. Empty secret fields are omitted (keep existing)."""
        values: dict = {}
        for field in self._fields:
            if field.key in self._secret_keys:
                text = field.widget.text().strip()
                if text:
                    values[field.key] = text
            else:
                values[field.key] = field.value()
        return values

    def validate(self) -> dict[str, str]:
        """Validate the current form contents; shows and returns errors."""
        errors = self._service.validate(self.collect())
        self.show_errors(errors)
        return errors

    def show_errors(self, errors: dict[str, str]) -> None:
        for key, label in self._error_labels.items():
            message = errors.get(key)
            if message:
                label.setText(message)
                label.show()
            else:
                label.hide()
            self._set_invalid(key, bool(message))

    def clear_errors(self) -> None:
        for key, label in self._error_labels.items():
            label.hide()
            self._set_invalid(key, False)

    def _set_invalid(self, key: str, invalid: bool) -> None:
        for field in self._fields:
            if field.key != key:
                continue
            widget = field.widget
            widget.setProperty("invalid", invalid)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def reset(self) -> None:
        """Reset every key on this page to its default (persisted)."""
        for field in self._fields:
            self._service.reset(field.key)
        self.load()

    # -- dirty tracking -----------------------------------------------------------

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def field_keys(self) -> tuple[str, ...]:
        return tuple(field.key for field in self._fields)

    def _on_edit(self, *_args) -> None:
        if self._loading or self._dirty:
            return
        self._dirty = True
        self.changed.emit()


# ---------------------------------------------------------------------------
# The nine category pages
# ---------------------------------------------------------------------------


class GeneralPage(SettingsPage):
    page_id = "general"
    title = "General"
    icon = "settings"
    subtitle = "Startup, workspace and update behaviour"
    groups = (
        ("Startup", ("startup.module", "startup.restore_workspace", "startup.confirm_quit", "startup.single_instance")),
        ("Workspace", ("general.autosave_interval", "general.show_hidden_files", "general.open_files_in_tabs")),
        ("Updates", ("updates.check", "updates.channel")),
    )


class MenusPage(SettingsPage):
    page_id = "menus"
    title = "Menus"
    icon = "eye"
    subtitle = "Menu manager — show or hide top menus and left navigation, live"
    groups = (
        ("Top menu bar", ("ui.menu_file", "ui.menu_edit", "ui.menu_view", "ui.menu_module", "ui.menu_help")),
        ("Left navigation & tabs", ("ui.show_compare", "ui.show_git", "ui.show_ai", "ui.show_ssh", "ui.show_loganalyzer", "ui.show_plugins")),
    )

    def _build_extra(self, layout: QVBoxLayout) -> None:
        hint = QLabel("Changes apply immediately after you press Apply — no restart needed. "
                      "Each module below controls its sidebar icon, workspace tab, Module-menu entry "
                      "and command-palette command. Settings itself cannot be disabled, so you can "
                      "always re-enable things here.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)


class AppearancePage(SettingsPage):
    page_id = "appearance"
    title = "Appearance"
    icon = "sun"
    subtitle = "Theme, workspace panels, accent color and typography"
    groups = (
        ("Theme", ("appearance.theme", "appearance.accent", "appearance.reduce_transparency")),
        ("Typography", ("appearance.font_size", "appearance.mono_diffs", "appearance.antialias")),
    )

    def _make_control(self, spec: SettingDef) -> QWidget:
        if spec.key == "appearance.accent":
            return _AccentSwatches()
        if spec.key == "appearance.font_size":
            return _FontSizeRow()
        return super()._make_control(spec)

    def _build_extra(self, layout: QVBoxLayout) -> None:
        """Workspace chrome moved here from the old top toolbar."""
        box = QGroupBox("Workspace")
        group_layout = QVBoxLayout(box)
        group_layout.setContentsMargins(12, 14, 12, 12)
        group_layout.setSpacing(10)

        hint = QLabel(
            "Panels and tools that used to sit on the top toolbar. "
            "Shortcuts still work from the View / Edit menus and the command palette."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        group_layout.addWidget(hint)

        panels = QWidget()
        panels_layout = QHBoxLayout(panels)
        panels_layout.setContentsMargins(0, 0, 0, 0)
        panels_layout.setSpacing(8)
        for label, action_key, icon_key in (
            ("Command Palette", "open_palette", "search"),
            ("Output", "toggle_output", "terminal"),
            ("Details", "toggle_details", "info"),
            ("Toggle Theme", "toggle_theme", "moon"),
        ):
            btn = QPushButton(self._icons.get(icon_key, 16), label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("class", "ghost")
            btn.clicked.connect(lambda _checked=False, key=action_key: self._run_shell_action(key))
            panels_layout.addWidget(btn)
        panels_layout.addStretch(1)
        group_layout.addWidget(panels)
        layout.insertWidget(0, box)

    def _run_shell_action(self, key: str) -> None:
        if self._ctx is None or not self._ctx.has("shell.actions"):
            return
        actions = self._ctx.resolve("shell.actions")
        slot = actions.get(key) if isinstance(actions, dict) else None
        if callable(slot):
            slot()


class GitPage(SettingsPage):
    page_id = "git"
    title = "Git"
    icon = "git"
    subtitle = "Executable, defaults, tools and commit signing"
    groups = (
        ("Core", ("git.executable", "git.default_branch", "git.autofetch", "git.fetch_interval")),
        ("External tools", ("git.diff_tool", "git.merge_tool")),
        ("Signing", ("git.sign_commits", "git.signing_key")),
    )


class AIPage(SettingsPage):
    page_id = "ai"
    title = "AI"
    icon = "ai"
    subtitle = "Provider, model and per-provider credentials"
    groups = (
        ("Provider", ("ai.provider", "ai.model", "ai.temperature", "ai.timeout", "ai.max_tokens")),
        ("OpenAI", ("ai.openai_base_url", "ai.api_key")),
        ("Gemini", ("ai.gemini_base_url", "ai.gemini_api_key")),
        ("Anthropic", ("ai.anthropic_base_url", "ai.anthropic_api_key")),
        ("Ollama", ("ai.ollama_base_url",)),
        ("Azure OpenAI", ("ai.azure_endpoint", "ai.azure_deployment", "ai.azure_api_version", "ai.azure_api_key")),
    )


class SSHPage(SettingsPage):
    page_id = "ssh"
    title = "SSH"
    icon = "ssh"
    subtitle = "Connection defaults and identity"
    groups = (
        ("Connection", ("ssh.default_user", "ssh.default_port", "ssh.timeout", "ssh.keepalive")),
        ("Identity", ("ssh.default_key", "ssh.passphrase", "ssh.compression")),
    )


class ComparePage(SettingsPage):
    page_id = "compare"
    title = "Compare"
    icon = "compare"
    subtitle = "Diff engine and comparison rules"
    groups = (
        ("Engine", ("compare.engine", "compare.context_lines")),
        ("Rules", ("compare.ignore_whitespace", "compare.ignore_case", "compare.ignore_comments", "compare.ignore_blank_lines")),
        ("Folders", ("compare.detect_moves", "compare.ignore_dirs")),
        ("Display", ("compare.show_whitespace", "compare.follow_symlinks")),
    )


class LogsPage(SettingsPage):
    page_id = "logs"
    title = "Logs"
    icon = "log"
    subtitle = "Application logging and rotation"
    groups = (
        ("Output", ("logs.log_level", "logs.timestamps")),
        ("Rotation", ("logs.max_bytes", "logs.backup_count")),
    )


class PluginsPage(SettingsPage):
    page_id = "plugins"
    title = "Plugins"
    icon = "plugins"
    subtitle = "Discovery and trust policy"
    groups = (
        ("Discovery", ("plugins.auto_discover", "plugins.scan_on_start")),
        ("Trust", ("plugins.allow_community", "plugins.trusted_sources", "plugins.strict_validation")),
    )


class AdvancedPage(SettingsPage):
    page_id = "advanced"
    title = "Advanced"
    icon = "alert"
    subtitle = "Paths, diagnostics and maintenance"
    groups = (
        ("Paths", ("advanced.data_folder", "advanced.log_folder")),
        ("Diagnostics", ("advanced.telemetry", "advanced.crash_reports", "advanced.developer_mode")),
    )

    def _build_extra(self, layout: QVBoxLayout) -> None:
        box = QGroupBox("Maintenance")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(12, 14, 12, 12)
        box_layout.setSpacing(8)
        box_layout.addWidget(
            self._action_row("Back up database…", "Copies devworkbench.db to a timestamped file.", "primary", self._backup_database)
        )
        box_layout.addWidget(
            self._action_row("Open data folder", "Reveals the application support directory in Finder.", "ghost", self._open_data_folder)
        )
        box_layout.addWidget(
            self._action_row("Reset all settings…", "Restores defaults — this cannot be undone.", "danger", self._reset_all)
        )
        layout.addWidget(box)

    @staticmethod
    def _action_row(title: str, hint: str, kind: str, callback) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)
        labels = QVBoxLayout()
        labels.setSpacing(2)
        title_label = QLabel(title)
        labels.addWidget(title_label)
        hint_label = QLabel(hint)
        hint_label.setObjectName("hint")
        hint_label.setWordWrap(True)
        labels.addWidget(hint_label)
        row_layout.addLayout(labels, 1)
        action = QPushButton(title)
        action.setProperty("class", kind)
        action.setCursor(Qt.CursorShape.PointingHandCursor)
        action.clicked.connect(callback)
        row_layout.addWidget(action)
        return row

    def _backup_database(self) -> None:
        if self._ctx is None or not self._ctx.has("database.connection"):
            QMessageBox.warning(self, "Back up database", "The database is not connected in this session.")
            return
        database = self._ctx.resolve("database.connection")
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        if self._ctx.has("core.paths"):
            target = self._ctx.resolve("core.paths").app_support / f"devworkbench-backup-{stamp}.db"
        else:
            target = Path.home() / f"devworkbench-backup-{stamp}.db"
        database.backup(target)
        QMessageBox.information(self, "Back up database", f"Database backed up to:\n{target}")

    def _open_data_folder(self) -> None:
        if self._ctx is not None and self._ctx.has("core.paths"):
            folder = self._ctx.resolve("core.paths").app_support
        else:
            folder = Path.home()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _reset_all(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset all settings",
            "Reset every setting to its default? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._service.reset_all()
            self.reset_all_requested.emit()


PAGES: list[type[SettingsPage]] = [
    GeneralPage,
    MenusPage,
    AppearancePage,
    GitPage,
    AIPage,
    SSHPage,
    ComparePage,
    LogsPage,
    PluginsPage,
    AdvancedPage,
]
