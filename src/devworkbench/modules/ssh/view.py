"""SSH screen — connection profiles, remote browser, transfers.

The profile list is persisted in SQLite (``ssh_servers`` via
``SshServerRepository``); passwords/passphrases live only in the macOS
Keychain. The remote browser and transfer table remain mock display data.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devworkbench.models.persistence import SshServerRecord
from devworkbench.modules.base import Module
from devworkbench.modules.ssh.dialog import ServerDialog
from devworkbench.ui.samples import SSH_BROWSER, SSH_HOSTS, SSH_TRANSFERS
from devworkbench.ui.theme import current_colors
from devworkbench.ui.widgets.common import button, clear_list_widget, icon_button, splitter, styled_label
from devworkbench.workers.ssh_worker import SshWorker

# Demo profiles seeded once into an empty database so the screen is alive.
_SAMPLE_SERVERS: tuple[tuple[str, str, str, str], ...] = (
    ("dev", "dev", "10.0.1.14", "agent"),
    ("pi", "pi", "raspberrypi.local", "agent"),
    ("staging", "staging", "tools.example.com", "agent"),
    ("prod", "prod", "db01.internal", "agent"),
)


# ---------------------------------------------------------------------------
# Infrastructure access (never reaches for globals)
# ---------------------------------------------------------------------------


def _repository(ctx):
    if ctx is None or not ctx.has("database.repositories.ssh"):
        return None
    return ctx.resolve("database.repositories.ssh")


def _keychain(ctx):
    if ctx is None or not ctx.has("services.keychain"):
        return None
    return ctx.resolve("services.keychain")


def _secret_account(name: str) -> str:
    return f"ssh:{name}"


def _probe_timeout(ctx) -> float:
    try:
        value = ctx.resolve("services.configuration").get("ssh.timeout")
        return float(value) if value else 5.0
    except Exception:  # noqa: BLE001 — never block the UI on a bad setting
        return 5.0


def _status(ctx) -> str | None:
    try:
        return str(ctx.resolve("services.configuration").get("ssh.default_user")) or None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Persistence helpers (module-level so tests can drive them directly)
# ---------------------------------------------------------------------------


def ensure_seeded(ctx) -> None:
    """Insert the demo profiles once when the table is empty (demo scaffold)."""
    repository = _repository(ctx)
    if repository is None or repository.count() > 0:
        return
    for name, user, host, auth in _SAMPLE_SERVERS:
        repository.insert(
            SshServerRecord(name=name, user=user, host=host, port=22, auth_method=auth)
        )


def load_servers(ctx) -> list[SshServerRecord]:
    """All saved profiles, ordered by name (empty list when no database)."""
    repository = _repository(ctx)
    if repository is None:
        return []
    return repository.list(order_by="name")


def persist_new(ctx, record: SshServerRecord, secret: str | None) -> int | None:
    """Insert a profile (+ Keychain secret). Returns the new id, or None."""
    repository = _repository(ctx)
    if repository is None:
        return None
    server_id = repository.insert(record)
    keychain = _keychain(ctx)
    if secret and keychain is not None:
        keychain.set(account=_secret_account(record.name), secret=secret)
    return server_id


def persist_update(ctx, record: SshServerRecord, secret: str | None, old_name: str, old_auth: str) -> bool:
    """Update a profile; move/clear the Keychain secret when needed.

    Secret bookkeeping, in order: a new secret overwrites under the current
    name; a rename with no new secret carries the old secret across when the
    auth method still uses one; the old account is always cleaned up on
    rename, and password secrets are cleared when password auth is turned
    off. Combined rename + auth-switch therefore never orphans a secret.
    """
    repository = _repository(ctx)
    if repository is None:
        return False
    ok = repository.update(record)
    keychain = _keychain(ctx)
    if keychain is None:
        return ok
    if secret:
        keychain.set(account=_secret_account(record.name), secret=secret)
    elif old_auth == "password" and record.auth_method == "password" and old_name != record.name:
        carried = keychain.get(account=_secret_account(old_name))
        if carried is not None:
            keychain.set(account=_secret_account(record.name), secret=carried)
    if old_name != record.name:
        keychain.delete(account=_secret_account(old_name))
    elif old_auth == "password" and record.auth_method != "password":
        keychain.delete(account=_secret_account(record.name))
    return ok


def remove_server(ctx, server_id: int, name: str) -> bool:
    """Delete a profile row and its Keychain secret."""
    repository = _repository(ctx)
    if repository is None:
        return False
    keychain = _keychain(ctx)
    if keychain is not None:
        keychain.delete(account=_secret_account(name))
    return repository.delete(server_id)


# ---------------------------------------------------------------------------
# Profile list UI
# ---------------------------------------------------------------------------


def _profile_row(record: SshServerRecord, pill: QLabel) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(8, 4, 8, 4)
    layout.setSpacing(8)
    labels = QVBoxLayout()
    labels.setSpacing(0)
    main = QLabel(record.name or f"{record.user}@{record.host}")
    labels.addWidget(main)
    if record.name:
        sub = QLabel(f"{record.user}@{record.host}")
        sub.setObjectName("hint")
        labels.addWidget(sub)
    layout.addLayout(labels, 1)
    layout.addWidget(pill)
    return row


def populate_list(profiles: QListWidget, servers: list[SshServerRecord], pills: dict[int, QLabel]) -> None:
    """Rebuild the profile list; returns nothing (pills dict updated in place)."""
    # clear() alone would leave the old row widgets in the viewport (one leak
    # per rebuild) — destroy them explicitly.
    clear_list_widget(profiles)
    pills.clear()
    for record in servers:
        pill = QLabel("○ offline")
        pill.setObjectName("statusPill")
        pill.setProperty("state", "err")
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, record.id)
        row = _profile_row(record, pill)
        item.setSizeHint(row.sizeHint())
        # addItem must come before setItemWidget or the row never renders.
        profiles.addItem(item)
        profiles.setItemWidget(item, row)
        pills[record.id] = pill


def _selected_record(state) -> SshServerRecord | None:
    profiles = state["list"]
    item = profiles.currentItem()
    if item is None:
        return None
    server_id = item.data(Qt.ItemDataRole.UserRole)
    return next((s for s in state["servers"] if s.id == server_id), None)


def _set_pill(state, server_id: int, online: bool) -> None:
    pill = state["pills"].get(server_id)
    if pill is None:
        return
    pill.setText("● online" if online else "○ offline")
    pill.setProperty("state", "ok" if online else "err")
    pill.style().unpolish(pill)
    pill.style().polish(pill)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def add_server_flow(parent: QWidget, state, ctx) -> None:
    """Open the Add-server dialog; persist on Save and refresh the list."""
    repository = _repository(ctx)
    if repository is None:
        QMessageBox.warning(parent, "Add server", "The database is not connected in this session.")
        return
    dialog = ServerDialog(
        parent,
        default_user=_status(ctx) or "dev",
        default_port=_default_port(ctx),
    )
    if dialog.exec() != ServerDialog.DialogCode.Accepted:
        return
    record = dialog.record()
    server_id = persist_new(ctx, record, dialog.secret())
    if server_id is None:
        QMessageBox.warning(parent, "Add server", "Could not save the server.")
        return
    state["servers"] = load_servers(ctx)
    populate_list(state["list"], state["servers"], state["pills"])
    _select_id(state, server_id)
    state["status_label"].setText(f"Server “{record.name}” added")


def edit_server_flow(parent: QWidget, state, ctx) -> None:
    record = _selected_record(state)
    if record is None:
        return
    repository = _repository(ctx)
    if repository is None:
        QMessageBox.warning(parent, "Edit server", "The database is not connected in this session.")
        return
    dialog = ServerDialog(parent, record=record, default_user=record.user, default_port=record.port or 22)
    if dialog.exec() != ServerDialog.DialogCode.Accepted:
        return
    updated = dialog.record()
    updated.id = record.id
    old_name, old_auth = record.name, record.auth_method
    persist_update(ctx, updated, dialog.secret(), old_name, old_auth)
    state["servers"] = load_servers(ctx)
    populate_list(state["list"], state["servers"], state["pills"])
    _select_id(state, record.id)
    state["status_label"].setText(f"Server “{updated.name}” updated")


def delete_server_flow(parent: QWidget, state, ctx) -> None:
    record = _selected_record(state)
    if record is None:
        return
    answer = QMessageBox.question(
        parent,
        "Delete server",
        f"Delete “{record.name}” ({record.user}@{record.host})? Its saved password "
        "will also be removed from the Keychain.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    remove_server(ctx, record.id, record.name)
    if state.get("connected_id") == record.id:
        _disconnect(state)
    state["servers"] = load_servers(ctx)
    populate_list(state["list"], state["servers"], state["pills"])
    state["status_label"].setText(f"Server “{record.name}” deleted")


def connect_server(state, ctx) -> None:
    """TCP-reachability probe for the selected server (real check, no auth)."""
    record = _selected_record(state)
    if record is None or state.get("_probe_worker") is not None:
        return
    worker = SshWorker("probe", host=record.host, port=record.port or 22, timeout=_probe_timeout(ctx))
    state["_probe_worker"] = worker
    state["_probe_id"] = record.id
    state["strip_pill"].setText("○ Connecting…")
    state["strip_pill"].setProperty("state", "err")
    state["disconnect_btn"].setEnabled(False)
    state["status_label"].setText(f"Connecting to {record.user}@{record.host}…")

    def _alive(_record: SshServerRecord) -> bool:
        """The probed server still exists in the list (not deleted mid-probe)."""
        return _record.id in state["pills"]

    def _on_result(result, _record=record) -> None:
        if state.get("_probe_id") != _record.id or not _alive(_record):
            state["_probe_worker"] = None
            return  # stale — server deleted/replaced while probing
        state["_probe_worker"] = None
        state["connected_id"] = _record.id
        _set_pill(state, _record.id, True)
        state["strip_pill"].setText(f"● Connected to {_record.user}@{_record.host} · SFTP")
        state["strip_pill"].setProperty("state", "ok")
        state["disconnect_btn"].setEnabled(True)
        state["status_label"].setText(
            f"Connected to {_record.host}:{_record.port or 22} (TCP reachable)"
        )

    def _on_error(exc, _record=record) -> None:
        if state.get("_probe_id") != _record.id or not _alive(_record):
            state["_probe_worker"] = None
            return
        state["_probe_worker"] = None
        state["connected_id"] = None
        _set_pill(state, _record.id, False)
        state["strip_pill"].setText("○ Not connected")
        state["strip_pill"].setProperty("state", "err")
        state["disconnect_btn"].setEnabled(False)
        state["status_label"].setText(f"Unreachable: {_record.host}:{_record.port or 22} ({exc})")

    worker.signals.finished.connect(_on_result)
    worker.signals.error.connect(_on_error)
    QThreadPool.globalInstance().start(worker)


def _disconnect(state) -> None:
    previous = state.get("connected_id")
    state["connected_id"] = None
    if state.get("_probe_worker") is not None:
        state["_probe_worker"].cancel()
        state["_probe_worker"] = None
    if previous is not None:
        _set_pill(state, previous, False)
    state["strip_pill"].setText("○ Not connected")
    state["strip_pill"].setProperty("state", "err")
    state["disconnect_btn"].setEnabled(False)
    state["status_label"].setText("Disconnected")


def _select_id(state, server_id: int) -> None:
    profiles = state["list"]
    for index in range(profiles.count()):
        item = profiles.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == server_id:
            profiles.setCurrentRow(index)
            return


def _default_port(ctx) -> int:
    try:
        value = ctx.resolve("services.configuration").get("ssh.default_port")
        return int(value) if value else 22
    except Exception:  # noqa: BLE001
        return 22


# ---------------------------------------------------------------------------
# Mock panels (unchanged from the scaffold)
# ---------------------------------------------------------------------------


def _remote_browser() -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderLabels(["Name", "Size"])
    tree.setColumnWidth(0, 260)
    for kind, name, children in SSH_BROWSER:
        top = QTreeWidgetItem([name, ""])
        for child in children:
            size = "4.2 KB" if child != "access.log" else "48 MB"
            item = QTreeWidgetItem([child, size])
            top.addChild(item)
        tree.addTopLevelItem(top)
        top.setExpanded(True)
    return tree


def _transfers_table() -> QTableWidget:
    table = QTableWidget(len(SSH_TRANSFERS), 4)
    table.setHorizontalHeaderLabels(["File", "Direction", "Progress", "Size"])
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.setColumnWidth(0, 220)
    table.setColumnWidth(1, 90)
    table.setColumnWidth(3, 80)
    for row, (path, direction, status, value, size) in enumerate(SSH_TRANSFERS):
        table.setItem(row, 0, QTableWidgetItem(path))
        table.setItem(row, 1, QTableWidgetItem(direction))
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(value)
        bar.setTextVisible(True)
        bar.setFixedHeight(14)
        table.setCellWidget(row, 2, bar)
        table.setItem(row, 3, QTableWidgetItem(size))
    return table


# ---------------------------------------------------------------------------
# View assembly
# ---------------------------------------------------------------------------


def build_view(icons, ctx=None) -> QWidget:
    root = QWidget()
    layout = QVBoxLayout(root)
    layout.setContentsMargins(10, 10, 10, 8)
    layout.setSpacing(8)

    # ---- connection strip ----------------------------------------------------
    strip = QWidget()
    strip_layout = QHBoxLayout(strip)
    strip_layout.setContentsMargins(0, 0, 0, 0)
    strip_pill = QLabel("○ Not connected")
    strip_pill.setObjectName("statusPill")
    strip_pill.setProperty("state", "err")
    strip_layout.addWidget(strip_pill)
    status_label = QLabel("")
    status_label.setObjectName("hint")
    strip_layout.addWidget(status_label, 1)
    disconnect_btn = button("Disconnect", "ghost")
    disconnect_btn.setEnabled(False)
    strip_layout.addWidget(disconnect_btn)
    layout.addWidget(strip)

    main_split = splitter(Qt.Orientation.Horizontal)

    # ---- left: profiles --------------------------------------------------------
    rail = QWidget()
    rail_layout = QVBoxLayout(rail)
    rail_layout.setContentsMargins(0, 0, 0, 0)
    rail_layout.setSpacing(6)
    rail_layout.addWidget(styled_label("Connections", "muted"))

    ensure_seeded(ctx)
    servers = load_servers(ctx)
    if not servers:
        # No database in this session — fall back to the static demo list.
        demo: list[SshServerRecord] = []
        for index, (label, _state) in enumerate(SSH_HOSTS):
            user, sep, host = label.partition("@")
            demo.append(
                SshServerRecord(
                    id=index,
                    name=label if sep else host,
                    user=user or "dev",
                    host=host or label,
                )
            )
        servers = demo
    profiles = QListWidget()
    pills: dict[int, QLabel] = {}
    populate_list(profiles, servers, pills)
    rail_layout.addWidget(profiles, 1)

    state = {
        "list": profiles,
        "servers": servers,
        "pills": pills,
        "strip_pill": strip_pill,
        "status_label": status_label,
        "disconnect_btn": disconnect_btn,
        "connected_id": None,
        "_probe_worker": None,
        "_probe_id": None,
    }

    rail_actions = QWidget()
    rail_actions_layout = QHBoxLayout(rail_actions)
    rail_actions_layout.setContentsMargins(0, 0, 0, 0)
    rail_actions_layout.setSpacing(6)
    add_btn = button("Add profile", "ghost")
    edit_btn = button("Edit", "ghost")
    delete_btn = button("Delete", "ghost")
    connect_btn = button("Connect", "primary")
    add_btn.clicked.connect(lambda: add_server_flow(root, state, ctx))
    edit_btn.clicked.connect(lambda: edit_server_flow(root, state, ctx))
    delete_btn.clicked.connect(lambda: delete_server_flow(root, state, ctx))
    connect_btn.clicked.connect(lambda: connect_server(state, ctx))
    disconnect_btn.clicked.connect(lambda: _disconnect(state))
    for widget in (add_btn, edit_btn, delete_btn, connect_btn):
        rail_actions_layout.addWidget(widget)
    rail_actions_layout.addStretch(1)
    rail_layout.addWidget(rail_actions)
    rail.setMinimumWidth(240)
    rail.setMaximumWidth(320)
    main_split.addWidget(rail)

    # ---- right: browser + transfers ---------------------------------------------
    right = QWidget()
    right_layout = QVBoxLayout(right)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(8)

    path_bar = QWidget()
    path_bar_layout = QHBoxLayout(path_bar)
    path_bar_layout.setContentsMargins(0, 0, 0, 0)
    path_bar_layout.addWidget(icon_button(icons, "chevron_left", "Up"))
    path_bar_layout.addWidget(QLineEdit("~/projects/api"), 1)
    path_bar_layout.addWidget(icon_button(icons, "refresh", "Refresh"))
    right_layout.addWidget(path_bar)

    right_layout.addWidget(_remote_browser(), 3)

    transfers_label_row = QWidget()
    transfers_label_row_layout = QHBoxLayout(transfers_label_row)
    transfers_label_row_layout.setContentsMargins(0, 0, 0, 0)
    transfers_label_row_layout.addWidget(styled_label("Transfers", "muted"))
    transfers_label_row_layout.addStretch(1)
    right_layout.addWidget(transfers_label_row)
    right_layout.addWidget(_transfers_table(), 2)

    main_split.addWidget(right)
    main_split.setSizes([280, 720])
    layout.addWidget(main_split, 1)
    return root


ssh_module = Module(
    id="ssh",
    title="SSH",
    icon="ssh",
    build=build_view,
    navigator=(
        ("Hosts", ("dev@10.0.1.14", "pi@raspberrypi.local", "staging@tools.example.com")),
        ("Groups", ("production", "homelab")),
    ),
    details=(
        ("Host", "dev@10.0.1.14"),
        ("State", "connected"),
        ("CWD", "~/projects/api"),
        ("SFTP", "enabled"),
    ),
    status="SSH · dev@10.0.1.14 · connected",
)
