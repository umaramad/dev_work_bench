"""Settings screen (Flet) — bound to ``ConfigurationService``.

Category nav + schema-driven forms + Apply / Save / Reset / Cancel.
Secrets go through the keychain; Advanced offers backup / open data /
reset-all. Menus toggles persist only (no Qt menu manager).
"""

from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path

import flet as ft

from devworkbench.flet_ui import theme
from devworkbench.services.configuration_service import (
    CATEGORIES,
    SettingKind,
    ConfigurationService,
)


class _SettingsView:
    def __init__(self, shell) -> None:
        self.page = shell.page
        self._shell = shell
        self._backend = getattr(shell, "backend", None) or {}
        self._config: ConfigurationService | None = self._backend.get("config")
        self._paths = self._backend.get("paths")
        self._database = self._backend.get("database")
        self._git = self._backend.get("git")

        self._category = CATEGORIES[0]
        self._widgets: dict[str, ft.Control] = {}
        self._baseline: dict[str, object] = {}
        self._dirty = False

        # FilePicker is a Service in Flet 0.86+ — do not add it to page.overlay.
        self._nav = ft.Column(spacing=4, tight=True)
        self._form = ft.Column(spacing=theme.GAP, scroll=ft.ScrollMode.AUTO, expand=True)
        self._status = ft.Text("", size=12, color=theme.TEXT_MUTED)
        self._apply_btn = ft.FilledButton("Apply", icon=ft.Icons.CHECK, disabled=True, on_click=lambda _e: self._apply(save=False))
        self._save_btn = ft.FilledButton("Save", icon=ft.Icons.SAVE, disabled=True, on_click=lambda _e: self._apply(save=True))
        self._reset_btn = ft.OutlinedButton("Reset page", icon=ft.Icons.RESTART_ALT, on_click=lambda _e: self._reset_page())
        self._cancel_btn = ft.TextButton("Cancel", on_click=lambda _e: self._cancel())

    def build(self) -> ft.Container:
        if self._config is None:
            return theme.card(
                ft.Column(
                    [
                        ft.Text("Settings", size=22, weight=ft.FontWeight.W_600, color=theme.TEXT),
                        ft.Text("Configuration is unavailable (database did not open).", color=theme.WARN),
                    ],
                    spacing=theme.GAP,
                ),
                expand=True,
            )

        self._build_nav()
        self._load_category()
        footer = ft.Row(
            [self._status, ft.Container(expand=True), self._cancel_btn, self._reset_btn, self._apply_btn, self._save_btn],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        body = ft.Row(
            [
                ft.Container(
                    content=self._nav,
                    width=180,
                    padding=theme.padding_all(8),
                    border=theme.border_all(),
                    border_radius=theme.radius_all(theme.RADIUS),
                    bgcolor=theme.SURFACE_HOVER,
                ),
                ft.Container(content=self._form, expand=True, padding=theme.padding_xy(16, 4)),
            ],
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
            spacing=theme.GAP,
        )
        return theme.card(
            ft.Column(
                [
                    ft.Text("Settings", size=22, weight=ft.FontWeight.W_600, color=theme.TEXT),
                    ft.Text("Schema-bound preferences shared with the desktop app.", size=13, color=theme.TEXT_MUTED),
                    ft.Divider(height=1, color=theme.BORDER),
                    body,
                    ft.Divider(height=1, color=theme.BORDER),
                    footer,
                ],
                spacing=theme.GAP,
                expand=True,
            ),
            expand=True,
        )

    def _build_nav(self) -> None:
        self._nav.controls.clear()
        for name in CATEGORIES:
            selected = name == self._category
            self._nav.controls.append(
                ft.Container(
                    content=ft.Text(
                        name,
                        size=13,
                        weight=ft.FontWeight.W_600 if selected else ft.FontWeight.W_400,
                        color=theme.TEXT if selected else theme.TEXT_MUTED,
                    ),
                    bgcolor=theme.ACCENT_SOFT if selected else None,
                    border_radius=theme.radius_all(8),
                    padding=theme.padding_xy(12, 8),
                    ink=True,
                    on_click=lambda _e, n=name: self._select_category(n),
                )
            )

    def _select_category(self, name: str) -> None:
        if name == self._category:
            return
        if self._dirty:
            # Keep edits in memory widgets; switching categories rebuilds from service.
            # Collect current page into a pending buffer would be nicer; for parity
            # with a simple port we warn and discard unsaved page edits.
            self._snack("Unsaved changes on this page were discarded", ok=False)
        self._category = name
        self._build_nav()
        self._load_category()
        self.page.update()

    def _load_category(self) -> None:
        assert self._config is not None
        self._widgets.clear()
        self._form.controls.clear()
        self._dirty = False
        self._set_dirty_ui(False)
        self._status.value = ""

        definitions = self._config.definitions(self._category)
        if self._category == "Menus":
            self._form.controls.append(
                ft.Text(
                    "Left-nav module toggles apply live after Apply/Save. "
                    "Settings is always visible so you can re-enable modules.",
                    size=12,
                    color=theme.TEXT_MUTED,
                )
            )

        for definition in definitions:
            control = self._make_control(definition)
            self._widgets[definition.key] = control
            row = ft.Column(
                [
                    ft.Text(definition.label, size=13, weight=ft.FontWeight.W_500, color=theme.TEXT),
                    control,
                    ft.Text(definition.hint, size=11, color=theme.TEXT_FAINT) if definition.hint else ft.Container(height=0),
                ],
                spacing=4,
            )
            self._form.controls.append(row)

        if self._category == "Advanced":
            self._form.controls.append(ft.Divider(height=1, color=theme.BORDER))
            self._form.controls.append(ft.Text("Maintenance", size=14, weight=ft.FontWeight.W_600, color=theme.TEXT))
            self._form.controls.append(
                ft.Row(
                    [
                        ft.FilledButton("Back up database…", icon=ft.Icons.BACKUP, on_click=lambda _e: self._backup_database()),
                        ft.OutlinedButton("Open data folder", icon=ft.Icons.FOLDER_OPEN, on_click=lambda _e: self._open_data_folder()),
                        ft.OutlinedButton("Reset all settings…", icon=ft.Icons.DELETE_FOREVER, on_click=lambda _e: self._confirm_reset_all()),
                    ],
                    wrap=True,
                )
            )

        self._baseline = self._collect()

    def _make_control(self, definition) -> ft.Control:
        value = self._config.get(definition.key) if self._config else definition.default
        kind = definition.kind

        if kind is SettingKind.BOOL:
            return ft.Switch(value=bool(value), active_color=theme.ACCENT, on_change=lambda _e: self._mark_dirty())

        if kind is SettingKind.ENUM:
            return ft.Dropdown(
                options=[ft.dropdown.Option(key=c, text=c) for c in definition.choices],
                value=str(value if value in definition.choices else definition.default),
                dense=True,
                border_radius=theme.RADIUS,
                focused_border_color=theme.ACCENT,
                on_select=lambda _e: self._mark_dirty(),
            )

        if kind is SettingKind.INT:
            return ft.TextField(
                value=str(value),
                dense=True,
                border_radius=theme.RADIUS,
                focused_border_color=theme.ACCENT,
                keyboard_type=ft.KeyboardType.NUMBER,
                on_change=lambda _e: self._mark_dirty(),
            )

        if kind is SettingKind.FLOAT:
            return ft.TextField(
                value=str(value),
                dense=True,
                border_radius=theme.RADIUS,
                focused_border_color=theme.ACCENT,
                keyboard_type=ft.KeyboardType.NUMBER,
                on_change=lambda _e: self._mark_dirty(),
            )

        if kind is SettingKind.SECRET:
            field = ft.TextField(
                value="" if value is None else str(value),
                password=True,
                can_reveal_password=True,
                dense=True,
                border_radius=theme.RADIUS,
                focused_border_color=theme.ACCENT,
                hint_text="••••••••" if self._config and self._config.has_secret(definition.key) else "",
                on_change=lambda _e: self._mark_dirty(),
            )
            return field

        # STRING / PATH
        field = ft.TextField(
            value="" if value is None else str(value),
            dense=True,
            border_radius=theme.RADIUS,
            focused_border_color=theme.ACCENT,
            on_change=lambda _e: self._mark_dirty(),
        )
        if definition.browse in ("file", "dir"):
            async def browse(_e, mode=definition.browse, target=field) -> None:
                if mode == "dir":
                    chosen = await ft.FilePicker().get_directory_path(dialog_title="Choose folder")
                else:
                    files = await ft.FilePicker().pick_files(allow_multiple=False)
                    chosen = files[0].path if files else None
                if chosen:
                    target.value = chosen
                    self._mark_dirty()
                    self.page.update()

            field.suffix = ft.IconButton(icon=ft.Icons.FOLDER_OPEN, on_click=browse)
        return field

    def _collect(self) -> dict:
        values: dict = {}
        assert self._config is not None
        for key, control in self._widgets.items():
            definition = self._config.definition(key)
            if definition is None:
                continue
            if isinstance(control, ft.Switch):
                values[key] = bool(control.value)
            elif isinstance(control, ft.Dropdown):
                values[key] = control.value
            elif isinstance(control, ft.TextField):
                raw = control.value
                if definition.kind is SettingKind.INT:
                    try:
                        values[key] = int(str(raw).strip())
                    except ValueError:
                        values[key] = raw
                elif definition.kind is SettingKind.FLOAT:
                    try:
                        values[key] = float(str(raw).strip())
                    except ValueError:
                        values[key] = raw
                elif definition.kind is SettingKind.SECRET:
                    text = "" if raw is None else str(raw)
                    # Empty secret field means "leave unchanged" when a secret already exists.
                    if not text.strip() and self._config.has_secret(key):
                        continue
                    values[key] = text
                else:
                    values[key] = "" if raw is None else str(raw)
        return values

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._set_dirty_ui(True)
        self.page.update()

    def _set_dirty_ui(self, dirty: bool) -> None:
        self._apply_btn.disabled = not dirty
        self._save_btn.disabled = not dirty

    def _clear_errors(self) -> None:
        for control in self._widgets.values():
            if isinstance(control, ft.TextField):
                control.error_text = None

    def _show_errors(self, errors: dict[str, str]) -> None:
        for key, message in errors.items():
            control = self._widgets.get(key)
            if isinstance(control, ft.TextField):
                control.error_text = message

    def _apply(self, save: bool) -> None:
        assert self._config is not None
        self._clear_errors()
        values = self._collect()
        errors = self._config.apply(values)
        if errors:
            self._show_errors(errors)
            self._status.value = "Fix validation errors before applying."
            self._status.color = theme.ERR
            self._snack("Settings validation failed", ok=False)
            self.page.update()
            return

        # Keep GitService executable in sync when git settings change.
        if "git.executable" in values and self._git is not None and hasattr(self._git, "set_executable"):
            self._git.set_executable(str(values["git.executable"]))

        # Menus toggles rebuild the left NavigationRail immediately.
        if any(key.startswith("ui.show_") for key in values) and hasattr(self._shell, "refresh_navigation"):
            self._shell.refresh_navigation()

        self._dirty = False
        self._set_dirty_ui(False)
        self._baseline = self._collect()
        self._status.value = "Saved." if save else "Applied."
        self._status.color = theme.OK
        self._snack(self._status.value, ok=True)
        self.page.update()

    def _reset_page(self) -> None:
        assert self._config is not None
        for key in list(self._widgets):
            self._config.reset(key)
        self._load_category()
        self._status.value = "Page reset to defaults."
        self._status.color = theme.TEXT_MUTED
        self._snack("Page reset", ok=True)
        self.page.update()

    def _cancel(self) -> None:
        self._load_category()
        self._status.value = "Changes discarded."
        self._status.color = theme.TEXT_MUTED
        self.page.update()

    def _backup_database(self) -> None:
        if self._database is None:
            self._snack("Database is not connected", ok=False)
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        if self._paths is not None:
            target = Path(self._paths.app_support) / f"devworkbench-backup-{stamp}.db"
        else:
            target = Path.home() / f"devworkbench-backup-{stamp}.db"
        try:
            self._database.backup(target)
            self._snack(f"Backed up to {target}", ok=True)
        except Exception as exc:  # noqa: BLE001
            self._snack(str(exc), ok=False)

    def _open_data_folder(self) -> None:
        folder = Path(self._paths.app_support) if self._paths is not None else Path.home()
        try:
            if os.name == "posix":
                subprocess.Popen(["open", str(folder)])  # noqa: S603,S607 — macOS Finder
            else:
                os.startfile(str(folder))  # type: ignore[attr-defined]
            self._snack(f"Opened {folder}", ok=True)
        except Exception as exc:  # noqa: BLE001
            self._snack(str(exc), ok=False)

    def _confirm_reset_all(self) -> None:
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Reset all settings"),
            content=ft.Text("Reset every setting to its default? This cannot be undone."),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _e: self._close_dialog(dialog)),
                ft.FilledButton(
                    "Reset all",
                    icon=ft.Icons.DELETE_FOREVER,
                    on_click=lambda _e: self._do_reset_all(dialog),
                ),
            ],
        )
        self.page.show_dialog(dialog)

    def _do_reset_all(self, dialog) -> None:
        assert self._config is not None
        self._config.reset_all()
        self._close_dialog(dialog)
        self._load_category()
        if hasattr(self._shell, "refresh_navigation"):
            self._shell.refresh_navigation()
        self._snack("All settings reset", ok=True)
        self.page.update()

    def _snack(self, message: str, ok: bool = True) -> None:
        snackbar = ft.SnackBar(
            content=ft.Text(message, color=theme.TEXT),
            bgcolor=theme.OK if ok else theme.ERR,
            open=True,
            duration=3500,
        )
        self.page.overlay.append(snackbar)
        self.page.update()

    def _close_dialog(self, dialog) -> None:
        dialog.open = False
        self.page.update()


def build_settings_screen(shell) -> ft.Container:
    return _SettingsView(shell).build()
