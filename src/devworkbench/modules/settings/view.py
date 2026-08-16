"""Settings screen — nine-category navigation, stacked forms, professional
Apply / Save / Reset / Cancel flow with per-field validation.

Persistence goes through :class:`ConfigurationService` only: normal values
land in SQLite, secrets in the macOS Keychain. Applying also publishes
``settings.changed`` events so the shell (e.g. live theme switching) reacts.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from devworkbench.core.events import EventBus
from devworkbench.core.settings import SettingsManager
from devworkbench.modules.base import Module
from devworkbench.modules.settings.pages import AppearancePage, PAGES, SettingsPage
from devworkbench.services.configuration_service import (
    TOPIC_NAVIGATION_REQUEST,
    ConfigurationService,
)

FOOTER_ORDER = ("Reset", "Cancel", "Apply", "Save")


class SettingsView(QWidget):
    """The full settings module: nav rail + stacked pages + action footer."""

    def __init__(self, icons, service, ctx=None) -> None:
        super().__init__()
        self._icons = icons
        self._service = service
        self._ctx = ctx
        self._events = ctx.resolve("core.events") if ctx is not None and ctx.has("core.events") else None

        self._pages: list[SettingsPage] = []
        self._dirty_pages: set[SettingsPage] = set()
        self._return_module = "compare"
        self._build()
        self._refresh_footer()

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        # -- header ---------------------------------------------------------
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(1)
        title = QLabel("Settings")
        title.setObjectName("sectionTitle")
        header_layout.addWidget(title)
        self._subtitle = QLabel("")
        self._subtitle.setObjectName("pageSubtitle")
        header_layout.addWidget(self._subtitle)
        root.addWidget(header)

        # -- body -----------------------------------------------------------
        body = QHBoxLayout()
        body.setSpacing(8)

        self._nav = QListWidget()
        self._nav.setFixedWidth(196)
        self._nav.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for page_type in PAGES:
            item = QListWidgetItem(self._icons.get(page_type.icon, 16), page_type.title)
            item.setSizeHint(QSize(0, 36))
            item.setData(Qt.ItemDataRole.UserRole, page_type)
            self._nav.addItem(item)
        body.addWidget(self._nav)

        self._stack = QStackedWidget()
        self._page_of: dict[type[SettingsPage], SettingsPage] = {}
        for page_type in PAGES:
            page = page_type(self._service, self._icons, self._ctx)
            page.changed.connect(lambda _page=page: self._on_page_changed(_page))
            page.reset_all_requested.connect(self._reload_all)
            self._pages.append(page)
            self._page_of[page_type] = page
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self._stack.addWidget(scroll)
        body.addWidget(self._stack, 1)
        root.addLayout(body, 1)

        # -- footer ----------------------------------------------------------
        self._error_banner = QFrame()
        self._error_banner.setObjectName("errorBanner")
        banner_layout = QHBoxLayout(self._error_banner)
        banner_layout.setContentsMargins(10, 6, 10, 6)
        banner_layout.setSpacing(8)
        self._banner_title = QLabel("")
        self._banner_title.setObjectName("errorBannerText")
        banner_layout.addWidget(self._banner_title)
        self._banner_detail = QLabel("")
        self._banner_detail.setObjectName("errorBannerDetail")
        banner_layout.addWidget(self._banner_detail, 1)
        self._error_banner.hide()
        root.addWidget(self._error_banner)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)
        self._dirty_label = QLabel("Unsaved changes")
        self._dirty_label.setObjectName("statusPill")
        self._dirty_label.setProperty("state", "warn")
        self._dirty_label.hide()
        footer_layout.addWidget(self._dirty_label)
        footer_layout.addStretch(1)

        self._buttons: dict[str, QPushButton] = {}
        for key in FOOTER_ORDER:
            button = QPushButton(key)
            button.setProperty("class", "primary" if key in ("Apply", "Save") else "ghost")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if key == "Save":
                button.setDefault(True)
            button.clicked.connect(getattr(self, f"_on_{key.lower()}"))
            self._buttons[key] = button
            footer_layout.addWidget(button)
        root.addWidget(footer)

        self._nav.currentRowChanged.connect(self._on_page_selected)
        self._nav.setCurrentRow(0)

    # ---------------------------------------------------------------- wiring

    def set_return_module(self, module_id: str) -> None:
        """Remember the module to return to after Save (set by the shell)."""
        self._return_module = module_id

    def _on_page_selected(self, row: int) -> None:
        self._stack.setCurrentIndex(row)
        page = self._pages[row]
        self._subtitle.setText(page.subtitle)

    def _on_page_changed(self, page: SettingsPage) -> None:
        self._dirty_pages.add(page)
        self._refresh_nav_dots()
        self._refresh_footer()

    # ---------------------------------------------------------------- actions

    def _on_reset(self) -> None:
        page = self._current_page()
        if page is None:
            return
        page.reset()
        self._dirty_pages.discard(page)
        self._refresh_nav_dots()
        self._refresh_footer()

    def _on_cancel(self) -> None:
        self._reload_all()

    def _on_apply(self) -> None:
        if not self._dirty_pages:
            self._flash_banner("No unsaved changes", "Change a setting first, then Apply.", error=False)
            return
        # Only persist dirty pages — avoids unrelated validators (e.g. git path)
        # blocking Appearance saves.
        values: dict = {}
        for page in self._dirty_pages:
            values.update(page.collect())
        if self._service is not None:
            errors = self._service.apply(values)
        else:
            errors = {}
        if errors:
            self._show_errors(errors)
            return
        self._reload_all()
        self._flash_banner("Applied", "Settings saved.", error=False)

    def _on_save(self) -> None:
        if not self._dirty_pages:
            self._flash_banner("No unsaved changes", "Nothing to save — leaving Settings.", error=False)
            if self._events is not None:
                self._events.publish(TOPIC_NAVIGATION_REQUEST, module_id=self._return_module)
            return
        self._on_apply()
        if self._dirty_pages:
            return  # validation failed; stay and let the user fix it
        if self._events is not None:
            self._events.publish(TOPIC_NAVIGATION_REQUEST, module_id=self._return_module)

    # ---------------------------------------------------------------- helpers

    def recolor(self) -> None:
        """Re-fetch nav icons for the active theme (called on theme switch)."""
        for index, page_type in enumerate(PAGES):
            self._nav.item(index).setIcon(self._icons.get(page_type.icon, 16))
        # Keep Appearance theme combo / swatches in sync after a live toggle.
        appearance = self._page_of.get(AppearancePage)
        if appearance is not None and hasattr(appearance, "sync_theme_controls"):
            appearance.sync_theme_controls()

    def _flash_banner(self, title: str, detail: str, *, error: bool = True) -> None:
        self._banner_title.setText(title)
        self._banner_detail.setText(detail)
        self._error_banner.setProperty("tone", "error" if error else "ok")
        self._error_banner.style().unpolish(self._error_banner)
        self._error_banner.style().polish(self._error_banner)
        self._error_banner.show()
        QTimer.singleShot(2200, self._error_banner.hide)

    def _current_page(self) -> SettingsPage | None:
        row = self._nav.currentRow()
        return self._pages[row] if 0 <= row < len(self._pages) else None

    def _collect_all(self) -> dict:
        values: dict = {}
        for page in self._pages:
            values.update(page.collect())
        return values

    def _reload_all(self) -> None:
        for page in self._pages:
            page.load()
        self._dirty_pages.clear()
        self._error_banner.hide()
        self._refresh_nav_dots()
        self._refresh_footer()

    def _show_errors(self, errors: dict[str, str]) -> None:
        for page in self._pages:
            page.show_errors(errors)
        first_key = next(iter(errors))
        for index, page in enumerate(self._pages):
            if first_key in page.field_keys:
                self._nav.setCurrentRow(index)
                break
        self._banner_title.setText(f"Fix {len(errors)} issue{'s' if len(errors) != 1 else ''} before saving")
        self._banner_detail.setText(next(iter(errors.values())))
        self._error_banner.setProperty("tone", "error")
        self._error_banner.style().unpolish(self._error_banner)
        self._error_banner.style().polish(self._error_banner)
        self._error_banner.show()

    def _refresh_nav_dots(self) -> None:
        for index, page in enumerate(self._pages):
            item = self._nav.item(index)
            suffix = "  •" if page in self._dirty_pages else ""
            if item.text() != page.title + suffix:
                item.setText(page.title + suffix)

    def _refresh_footer(self) -> None:
        dirty = bool(self._dirty_pages)
        self._dirty_label.setVisible(dirty)
        # Always clickable — disabled buttons feel dead; empty Apply shows feedback.
        self._buttons["Apply"].setEnabled(True)
        self._buttons["Save"].setEnabled(True)
        self._buttons["Cancel"].setEnabled(dirty)
        self._buttons["Apply"].setToolTip(
            "Apply unsaved changes" if dirty else "No unsaved changes"
        )
        self._buttons["Save"].setToolTip(
            "Apply and return" if dirty else "Return (nothing to save)"
        )


# ---------------------------------------------------------------------------
# Module wiring
# ---------------------------------------------------------------------------


def build_view(icons, ctx=None) -> QWidget:
    if ctx is not None and ctx.has("services.configuration"):
        service = ctx.resolve("services.configuration")
    else:
        # Memory-only fallback so the screen never hard-crashes without a
        # container (tests, headless, misconfigured plugin contexts).
        settings = SettingsManager()
        service = ConfigurationService(settings=settings, repository=None, keychain=None, events=EventBus())
    return SettingsView(icons, service, ctx)


settings_module = Module(
    id="settings",
    title="Settings",
    icon="settings",
    build=build_view,
    navigator=(
        ("Sections", tuple(page.title for page in PAGES)),
    ),
    details=(
        ("Categories", f"{len(PAGES)}"),
        ("Secrets", "macOS Keychain"),
        ("Storage", "SQLite"),
    ),
    status=f"Settings · {len(PAGES)} categories",
)
