"""ServerDialog — add/edit an SSH server profile (TechTia-style).

Captures name, host, user, port and an auth method (SSH key / password /
SSH agent). Validation is local and immediate: the dialog never touches
SQLite or the Keychain — the view persists the returned
:class:`SshServerRecord` and the optional password secret (Keychain).

``accept()`` is only reachable when the form validates; on accept, callers
read ``record()`` and ``secret()`` (password is returned separately and never
stored on the record).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from devworkbench.models.persistence import SshServerRecord
from devworkbench.ui.widgets.common import form_row

_AUTH_CHOICES = (("key", "SSH key"), ("password", "Password"), ("agent", "SSH agent"))


def valid_host(host: str) -> bool:
    """Syntactic host check: non-empty, no whitespace, no path/port separators."""
    host = host.strip()
    if not host or any(char.isspace() for char in host):
        return False
    return all(char.isalnum() or char in ".-_" for char in host)


class ServerDialog(QDialog):
    """Modal add/edit form for an SSH server profile."""

    def __init__(
        self,
        parent: QWidget | None = None,
        record: SshServerRecord | None = None,
        default_user: str = "dev",
        default_port: int = 22,
    ) -> None:
        super().__init__(parent)
        self._record = record
        self.setWindowTitle("Edit server" if record is not None else "Add server")
        self.setMinimumWidth(420)
        self._build(default_user, default_port)
        if record is not None:
            self._load(record)

    # -- construction -------------------------------------------------------

    def _build(self, default_user: str, default_port: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        intro = QLabel(
            "SSH server profile — saved to your local database. "
            "Passwords go to the macOS Keychain, never the database."
            if self._record is None
            else "Edit the server profile — saved to your local database."
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Home server")
        self.name_row = form_row("Name *", self.name_edit, "A short label for this server.")
        layout.addWidget(self.name_row)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("e.g. 10.0.1.14 or api.example.com")
        self.host_row = form_row("Host *", self.host_edit, "Hostname or IP address.")
        layout.addWidget(self.host_row)

        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText(default_user)
        layout.addWidget(form_row("User", self.user_edit, "Login username."))

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(default_port)
        layout.addWidget(form_row("Port", self.port_spin))

        self.auth_combo = QComboBox()
        for value, label in _AUTH_CHOICES:
            self.auth_combo.addItem(label, value)
        layout.addWidget(form_row("Authentication", self.auth_combo))

        # Key path row (visible for "SSH key").
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("~/.ssh/id_ed25519")
        browse = QPushButton("Browse…")
        browse.setProperty("class", "ghost")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._browse_key)
        key_controls = QWidget()
        key_controls_layout = QHBoxLayout(key_controls)
        key_controls_layout.setContentsMargins(0, 0, 0, 0)
        key_controls_layout.setSpacing(6)
        key_controls_layout.addWidget(self.key_edit, 1)
        key_controls_layout.addWidget(browse)
        self.key_form_row = form_row(
            "Private key", key_controls, "Path to the SSH private key."
        )
        layout.addWidget(self.key_form_row)

        # Password row (visible for "Password" auth).
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("Enter password")
        self.password_form_row = form_row(
            "Password",
            self.password_edit,
            "Stored in the macOS Keychain — never in the database.",
        )
        layout.addWidget(self.password_form_row)

        self.auth_combo.currentIndexChanged.connect(self._sync_auth_fields)
        self._sync_auth_fields()

        # Per-field error labels (styled like the Settings pages). The widget
        # map is explicit — attribute names don't line up with field keys
        # (key_path_edit does not exist; the control is key_edit).
        self._errors: dict[str, QLabel] = {}
        self._error_widgets: dict[str, QLineEdit] = {
            "name": self.name_edit,
            "host": self.host_edit,
            "key_path": self.key_edit,
        }
        for key, row in (("name", self.name_row), ("host", self.host_row), ("key_path", self.key_form_row)):
            error = QLabel("")
            error.setObjectName("fieldError")
            error.setWordWrap(True)
            error.hide()
            row.layout().addWidget(error)
            self._errors[key] = error

        layout.addStretch(1)

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

    def _load(self, record: SshServerRecord) -> None:
        self.name_edit.setText(record.name)
        self.host_edit.setText(record.host)
        self.user_edit.setText(record.user)
        self.port_spin.setValue(record.port or 22)
        index = self.auth_combo.findData(record.auth_method or "key")
        self.auth_combo.setCurrentIndex(index if index >= 0 else 0)
        self.key_edit.setText(record.key_path or "")
        self.password_edit.clear()
        self.password_edit.setPlaceholderText("Leave blank to keep the saved password")

    def record(self) -> SshServerRecord:
        """The validated profile (auth_method normalized; no secret)."""
        return SshServerRecord(
            name=self.name_edit.text().strip(),
            host=self.host_edit.text().strip(),
            user=self.user_edit.text().strip() or "dev",
            port=self.port_spin.value(),
            key_path=self.key_edit.text().strip() if self.auth_combo.currentData() == "key" else "",
            auth_method=self.auth_combo.currentData(),
        )

    def secret(self) -> str | None:
        """The password to store in the Keychain, or None for key/agent auth."""
        if self.auth_combo.currentData() != "password":
            return None
        password = self.password_edit.text()
        return password if password else None

    # -- validation ---------------------------------------------------------------

    def validate(self) -> dict[str, str]:
        """Validate the form; returns {field: message} for failing fields."""
        errors: dict[str, str] = {}
        name = self.name_edit.text().strip()
        host = self.host_edit.text().strip()
        if not name:
            errors["name"] = "A name is required."
        elif len(name) > 80:
            errors["name"] = "Name is too long (max 80 characters)."
        if not host:
            errors["host"] = "A host is required."
        elif not valid_host(host):
            errors["host"] = "Host may only contain letters, digits, '.', '-' and '_'."
        if self.auth_combo.currentData() == "key":
            key_path = self.key_edit.text().strip()
            if not key_path:
                errors["key_path"] = "A private key path is required for key authentication."
            elif not Path(key_path).expanduser().exists():
                errors["key_path"] = f"Key not found: {key_path}"
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

    def _browse_key(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Choose private key", self.key_edit.text().strip() or str(Path.home() / ".ssh")
        )
        if chosen:
            self.key_edit.setText(chosen)

    def _sync_auth_fields(self) -> None:
        auth = self.auth_combo.currentData()
        self.key_form_row.setVisible(auth == "key")
        self.password_form_row.setVisible(auth == "password")
