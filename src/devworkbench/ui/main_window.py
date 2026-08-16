"""MainWindow — the application shell.

Layout
    menubar (top)
    ├─ Sidebar dock     (left, pinned)    — module rail (Compare / Git / …)
    ├─ Navigator dock   (left, hidden)    — optional contextual tree
    ├─ Output dock      (bottom, hidden)  — Terminal / Command Log / Tasks
    ├─ Details dock     (right, hidden)   — inspector
    └─ workspace        (center)          — module stack (tab bar hidden)

Shell chrome formerly on the top toolbar (theme, docks, command palette)
lives under Settings → Appearance → Workspace.

Output / Details remain toggleable from the View menu / command palette.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabWidget,
    QWidget,
)

from devworkbench import APP_NAME, __version__
from devworkbench.services.configuration_service import (
    TOPIC_NAVIGATION_REQUEST,
    TOPIC_SETTINGS_APPLIED,
    TOPIC_SETTINGS_CHANGED,
)
from devworkbench.ui.shell.command_palette import CommandPalette
from devworkbench.ui.shell.details_dock import DetailsDock
from devworkbench.ui.shell.navigator_dock import NavigatorDock
from devworkbench.ui.shell.output_dock import OutputDock
from devworkbench.ui.shell.sidebar import Sidebar
from devworkbench.ui.shell.status_bar import StatusBar
from devworkbench.ui.theme import THEMES, ThemeManager


class MainWindow(QMainWindow):
    def __init__(self, modules, icons, theme_manager: ThemeManager | None = None, ctx=None) -> None:
        super().__init__()
        self._icons = icons
        self._modules = modules
        self._module_by_id = {m.id: m for m in modules}
        self.theme = theme_manager or ThemeManager(QApplication.instance())
        self._ctx = ctx
        # Menu manager: the configuration service drives which menu-bar menus
        # and module screens are visible. None (tests/headless) = all visible.
        self._config = (
            ctx.resolve("services.configuration")
            if ctx is not None and ctx.has("services.configuration")
            else None
        )

        # Subscribe to the settings bus so persisted changes reach the shell
        # (live theme switching, save confirmation, cross-module navigation).
        self._events = None
        if ctx is not None and ctx.has("core.events"):
            self._events = ctx.resolve("core.events")
            self._events.subscribe(TOPIC_SETTINGS_CHANGED, self._on_setting_changed)
            self._events.subscribe(TOPIC_SETTINGS_APPLIED, self._on_settings_applied)
            self._events.subscribe(TOPIC_NAVIGATION_REQUEST, self._on_navigation_request)

        self.setWindowTitle(APP_NAME)
        self.resize(1400, 860)
        self.setMinimumSize(1024, 640)
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks
            | QMainWindow.DockOption.AnimatedDocks
        )

        self.workspace = QTabWidget()
        self.workspace.setObjectName("workspaceTabs")
        self.workspace.setDocumentMode(True)
        self.workspace.setMovable(False)
        # Module switching lives in the left sidebar (and menus / shortcuts).
        # Hide the duplicate top tab strip so content gets the vertical space.
        self.workspace.tabBar().hide()
        self.setCentralWidget(self.workspace)

        self._build_sidebar()
        self._build_navigator()
        self._build_output()
        self._build_details()
        self._build_tabs()
        self._build_status_bar()
        self._build_menus()
        self._build_task_actions()
        self._register_shell_actions()

        # Lazily-built module views (see _build_tabs): index -> built widget.
        self._built_views: dict[int, QWidget] = {}

        self._commands: list[tuple[str, str, callable]] = []
        self._rebuild_commands()

        self.workspace.currentChanged.connect(self._on_module_changed)
        self.sidebar.module_activated.connect(self._activate_module_id)
        self.sidebar.collapse_toggled.connect(self._on_sidebar_collapsed)
        # Never let a background task outlive the window: cancel on quit so no
        # queued line/progress events touch destroyed Output dock widgets.
        QApplication.instance().aboutToQuit.connect(self._shutdown_run_worker)

        # Apply the persisted menu-manager state, then open the configured
        # startup module (default: Home). Fall back to the first visible tab.
        self._apply_ui_visibility()
        startup = "home"
        if self._config is not None:
            try:
                startup = str(self._config.get("startup.module") or "home")
            except Exception:  # noqa: BLE001
                startup = "home"
        if self._module_visible(startup) and startup in self._module_by_id:
            self._activate_module_id(startup)
        else:
            self._on_module_changed(0)

    # ------------------------------------------------------------------ shell

    def _build_sidebar(self) -> None:
        self.sidebar = Sidebar(self._modules, self._icons)
        dock = QDockWidget(self)
        dock.setObjectName("sidebarDock")
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setTitleBarWidget(QWidget())  # no title bar — it's the app nav rail
        dock.setWidget(self.sidebar)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        dock.setMinimumWidth(212)
        dock.setMaximumWidth(280)
        self._sidebar_dock = dock

    def _build_navigator(self) -> None:
        self.navigator = NavigatorDock(self._icons)
        dock = QDockWidget("Navigator", self)
        dock.setObjectName("navigatorDock")
        dock.setWidget(self.navigator)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        # Hidden by default — module switching is the sidebar; open via View menu
        # if needed. Leaving it visible stacks a second left panel under the rail.
        dock.hide()
        self._navigator_dock = dock

    def _build_output(self) -> None:
        self.output = OutputDock(self._icons)
        dock = QDockWidget("Output", self)
        dock.setObjectName("outputDock")
        dock.setWidget(self.output)
        dock.setMinimumHeight(150)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        # Console / command log / tasks stay minimized by default — the user
        # opens the Output panel on demand (View → Output / Settings / palette).
        dock.hide()
        self._output_dock = dock

    def _build_details(self) -> None:
        self.details = DetailsDock(self._icons)
        dock = QDockWidget("Details", self)
        dock.setObjectName("detailsDock")
        dock.setWidget(self.details)
        dock.setMinimumWidth(220)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.hide()
        self._details_dock = dock

    def _build_tabs(self) -> None:
        """Add a placeholder tab per module; the real view is built lazily on
        first activation (``_view_for``), so startup only pays for the shell
        and the first screen instead of all seven."""
        for module in self._modules:
            placeholder = QWidget()
            placeholder.setObjectName(f"{module.id}_placeholder")
            self.workspace.addTab(placeholder, self._icons.get(module.icon, 16), module.title)

    def _view_for(self, index: int) -> QWidget:
        """Return the built view for ``index``, building it on first use.

        The placeholder tab is replaced in place with the real widget, so
        ``workspace.currentIndex()`` / tab order never change.
        """
        view = self._built_views.get(index)
        if view is not None:
            return view
        module = self._modules[index]
        view = module.build(self._icons, self._ctx)
        view.setObjectName(module.id)
        # Cache *before* touching the tab widget: removeTab/insertTab fire
        # currentChanged re-entrantly, and without the cache that recursion
        # would rebuild the view (and swap tabs) on every frame.
        self._built_views[index] = view
        self.workspace.blockSignals(True)
        try:
            self.workspace.removeTab(index)
            self.workspace.insertTab(index, view, self._icons.get(module.icon, 16), module.title)
            # removeTab/insertTab can shift the current tab (inserting before
            # it bumps the index); restore it so the activated tab stays put.
            self.workspace.setCurrentIndex(index)
        finally:
            self.workspace.blockSignals(False)
        return view

    def _build_status_bar(self) -> None:
        self.status_bar = StatusBar(self._icons, f"v{__version__}")
        self.setStatusBar(self.status_bar)

    # ------------------------------------------------------------------ menus

    def _build_menus(self) -> None:
        self._menus: dict[str, QMenu] = {}
        self._module_actions: dict[str, QAction] = {}

        file_menu = self.menuBar().addMenu("&File")
        self._menus["file"] = file_menu
        file_menu.addAction(self._action("New Comparison…", "compare", lambda: self._activate_module_id("compare")))
        file_menu.addAction(self._action("Open…", "open", self._not_wired))
        file_menu.addSeparator()
        prefs = self._action("Preferences…", "settings", lambda: self._activate_module_id("settings"))
        prefs.setShortcut(QKeySequence("Ctrl+,"))
        file_menu.addAction(prefs)
        file_menu.addSeparator()
        quit_action = self._action("Quit " + APP_NAME, "close", QApplication.instance().quit)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        file_menu.addAction(quit_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        self._menus["edit"] = edit_menu
        palette_action = self._action("Command Palette…", "search", self._open_palette)
        palette_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        edit_menu.addAction(palette_action)
        edit_menu.addAction(self._action("Find…", "search", self._not_wired))

        # Dock toggles — use each dock's own toggleViewAction so the menu
        # checkmark always reflects the actual panel visibility.
        self._dock_actions = {
            "nav": self._navigator_dock.toggleViewAction(),
            "out": self._output_dock.toggleViewAction(),
            "det": self._details_dock.toggleViewAction(),
        }
        for key, icon in (("nav", "eye"), ("out", "terminal"), ("det", "info")):
            action = self._dock_actions[key]
            action.setIcon(self._icons.get(icon, 16))
            action.setData(icon)
        view_menu = self.menuBar().addMenu("&View")
        self._menus["view"] = view_menu
        view_menu.addAction(self._dock_actions["out"])
        view_menu.addAction(self._dock_actions["det"])
        view_menu.addSeparator()
        sidebar_action = self._action("Collapse Sidebar", "chevron_left", self._toggle_sidebar_collapse)
        sidebar_action.setShortcut(QKeySequence("Ctrl+\\"))
        view_menu.addAction(sidebar_action)
        theme_action = self._action("Toggle Dark / Light Theme", "moon", self._toggle_theme)
        theme_action.setShortcut(QKeySequence("Ctrl+D"))
        view_menu.addAction(theme_action)

        module_menu = self.menuBar().addMenu("&Module")
        self._menus["module"] = module_menu
        for i, module in enumerate(self._modules):
            action = self._action(module.title, module.icon, partial(self._activate_module_id, module.id))
            action.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            self._module_actions[module.id] = action
            module_menu.addAction(action)

        help_menu = self.menuBar().addMenu("&Help")
        self._menus["help"] = help_menu
        help_menu.addAction(self._action("About " + APP_NAME, "app", self._show_about))

    # ---------------------------------------------------------- task actions

    def _build_task_actions(self) -> None:
        """Keep Run/Stop actions for Output dock workers — no top toolbar."""
        self._run_action = self._action("Run", "play", self._run_task)
        self._stop_action = self._action("Stop", "stop", self._stop_task, enabled=False)
        self._run_worker = None
        self._task_row: int | None = None

    def _register_shell_actions(self) -> None:
        """Expose palette / docks / theme to Settings → Appearance → Workspace."""
        if self._ctx is None:
            return
        self._ctx.container.register_singleton(
            "shell.actions",
            {
                "open_palette": self._open_palette,
                "toggle_theme": self._toggle_theme,
                "toggle_navigator": self._dock_actions["nav"].trigger,
                "toggle_output": self._dock_actions["out"].trigger,
                "toggle_details": self._dock_actions["det"].trigger,
            },
        )

    # ---------------------------------------------------------------- actions

    def _action(
        self,
        text: str,
        icon: str,
        slot=None,
        shortcut: str | None = None,
        enabled: bool = True,
    ) -> QAction:
        action = QAction(self._icons.get(icon, 16), text, self)
        if slot is not None:
            action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.setData(icon)  # remembered so _toggle_theme can re-tint icons
        action.setEnabled(enabled)
        return action

    # -------------------------------------------------------------- commands

    def _rebuild_commands(self) -> None:
        """(Re)build the command palette entries — disabled modules are left out
        so they cannot be launched from the palette either."""
        commands: list[tuple[str, str, callable]] = []
        for module in self._modules:
            if self._module_visible(module.id):
                commands.append((f"Switch to {module.title}", module.icon, partial(self._activate_module_id, module.id)))
        commands.append(("Toggle Output", "terminal", self._dock_actions["out"].trigger))
        commands.append(("Toggle Details", "info", self._dock_actions["det"].trigger))
        commands.append(("Toggle Dark / Light Theme", "moon", self._toggle_theme))
        commands.append(("Collapse Sidebar", "chevron_left", self._toggle_sidebar_collapse))
        commands.append(("Quit " + APP_NAME, "close", QApplication.instance().quit))
        self._commands = commands

    # ------------------------------------------------------------ connectors

    def _on_module_changed(self, index: int) -> None:
        if not (0 <= index < len(self._modules)):
            return
        if not self._module_visible(self._modules[index].id):
            # A hidden tab became current (startup with a disabled first
            # module, or a mid-session hide) — hop to the first visible one.
            for i, module in enumerate(self._modules):
                if i != index and self._module_visible(module.id):
                    self.workspace.setCurrentIndex(i)
                    return
            return
        # Build the screen lazily the first time the user opens it. This is
        # also what makes _activate_module_id / screenshots reliable: by the
        # time the index settles, the view exists.
        self._view_for(index)
        module = self._modules[index]
        self.sidebar.set_active(module.id)
        self.navigator.set_module(index)
        self.status_bar.set_message(module.status)
        if module.details:
            self.details.show_entry(module.title, list(module.details))
        else:
            self.details.clear()

    def _module_index(self, module_id: str) -> int:
        for index, module in enumerate(self._modules):
            if module.id == module_id:
                return index
        return -1

    def _activate_module_id(self, module_id: str) -> None:
        if not self._module_visible(module_id):
            return  # disabled in the menu manager — ignore navigation requests
        current_index = self.workspace.currentIndex()
        current_module = self._modules[current_index] if 0 <= current_index < len(self._modules) else None
        for index, module in enumerate(self._modules):
            if module.id == module_id:
                if module_id == "settings" and current_module is not None and current_module.id != "settings":
                    # Let the Settings screen know where to return after Save.
                    view = self._view_for(index)
                    if hasattr(view, "set_return_module"):
                        view.set_return_module(current_module.id)
                # setCurrentIndex is a no-op when already on ``index`` (e.g. Home
                # at 0 on first launch) — force the change handler so the view builds.
                if self.workspace.currentIndex() == index:
                    self._on_module_changed(index)
                else:
                    self.workspace.setCurrentIndex(index)
                return

    def _on_sidebar_collapsed(self, collapsed: bool) -> None:
        if collapsed:
            self._sidebar_dock.setMinimumWidth(56)
            self._sidebar_dock.setMaximumWidth(64)
        else:
            self._sidebar_dock.setMinimumWidth(212)
            self._sidebar_dock.setMaximumWidth(280)

    def _toggle_sidebar_collapse(self) -> None:
        self.sidebar.set_collapsed(not self.sidebar._collapsed)

    def _toggle_theme(self) -> None:
        # Persist through the settings service so the change is durable and
        # every subscriber sees it; the resulting settings.changed event
        # drives the actual theme application.
        if self._ctx is not None and self._ctx.has("services.configuration"):
            service = self._ctx.resolve("services.configuration")
            target = "light" if self.theme.name == "dark" else "dark"
            service.set("appearance.theme", target)
            return
        self._apply_theme("light" if self.theme.name == "dark" else "dark")

    def _apply_theme(self, name: str) -> None:
        """Apply a theme by name (resolving 'system' to the OS appearance)."""
        if name == "system":
            try:
                scheme = QGuiApplication.styleHints().colorScheme()
                name = "dark" if scheme == Qt.ColorScheme.Dark else "light"
            except Exception:  # noqa: BLE001 — offscreen platforms may not report a scheme
                name = "dark"
        if name not in THEMES:
            name = "dark"
        self.theme.apply(name)
        # Re-tint every action and tab icon for the new theme (icons are
        # cached per resolved color, so fresh hex values yield fresh pixmaps).
        for action in self.findChildren(QAction):
            key = action.data()
            if isinstance(key, str) and key:
                action.setIcon(self._icons.get(key, 16))
        for index, module in enumerate(self._modules):
            self.workspace.setTabIcon(index, self._icons.get(module.icon, 16))
        self.sidebar._recolor()
        # Re-tint the Settings category rail too (icons cached per color) —
        # but only if the screen exists: a theme toggle must not force-build
        # the Settings view (that would defeat lazy loading).
        settings_view = self._built_views.get(self._module_index("settings"))
        if settings_view is not None and hasattr(settings_view, "recolor"):
            settings_view.recolor()
        self.status_bar.set_message(f"Theme: {name}")
        for i in range(self.workspace.count()):
            self.workspace.widget(i).update()

    def _on_setting_changed(self, key: str = None, value=None) -> None:
        if key == "appearance.theme":
            self._apply_theme(str(value))
        elif key is not None and key.startswith("ui.menu_"):
            self._apply_menu_visibility(key[len("ui.menu_"):], bool(value))
        elif key is not None and key.startswith("ui.show_"):
            self._apply_module_visibility(key[len("ui.show_"):], bool(value))

    def _on_settings_applied(self, keys=None) -> None:
        self.status_bar.set_message(f"Settings saved ({len(keys or [])} changes)")

    def _on_navigation_request(self, module_id: str = None) -> None:
        self._activate_module_id(module_id or "home")

    # ----------------------------------------------------------- menu manager

    def _module_visible(self, module_id: str) -> bool:
        """Whether a module screen is enabled in the menu manager.

        Settings is pinned: it must always stay reachable or the user could
        never re-enable the rest. With no configuration service (tests,
        headless) everything is visible.
        """
        if module_id in ("settings", "home"):
            return True
        if self._config is None:
            return True
        try:
            return bool(self._config.get(f"ui.show_{module_id}"))
        except KeyError:
            return True

    def _menu_visible(self, menu_id: str) -> bool:
        if self._config is None:
            return True
        try:
            return bool(self._config.get(f"ui.menu_{menu_id}"))
        except KeyError:
            return True

    def _apply_ui_visibility(self) -> None:
        """Sync the shell to the persisted menu-manager state (startup)."""
        for menu_id, menu in self._menus.items():
            menu.menuAction().setVisible(self._menu_visible(menu_id))
        for module in self._modules:
            self._apply_module_visibility(module.id, self._module_visible(module.id), rebuild=False)
        self._rebuild_commands()

    def _apply_module_visibility(self, module_id: str, visible: bool, rebuild: bool = True) -> None:
        """Show or hide a module screen everywhere at once (menu, sidebar, tab)."""
        action = self._module_actions.get(module_id)
        if action is not None:
            action.setVisible(visible)
            action.setEnabled(visible)  # also disables the Ctrl+N shortcut
        self.sidebar.set_module_visible(module_id, visible)
        index = self._module_index(module_id)
        if index >= 0 and index < self.workspace.count():
            if not visible and index == self.workspace.currentIndex():
                # Never leave the active tab hidden: hop to the first visible
                # module first (fires currentChanged → _on_module_changed).
                for i, module in enumerate(self._modules):
                    if i != index and self._module_visible(module.id):
                        self.workspace.setCurrentIndex(i)
                        break
            self.workspace.setTabVisible(index, visible)
        if rebuild:
            self._rebuild_commands()

    def _apply_menu_visibility(self, menu_id: str, visible: bool) -> None:
        menu = self._menus.get(menu_id)
        if menu is not None:
            menu.menuAction().setVisible(visible)

    def _open_palette(self) -> None:
        CommandPalette(self._icons, self._commands, self).exec()

    # ------------------------------------------------------------ run / stop

    def _run_task(self) -> None:
        """Run the (simulated) check pipeline in the Output dock.

        Run disables itself while the task streams; Stop cancels it. The
        worker is retained until finished/error fires (threading contract in
        ``workers/base.py``).
        """
        if self._run_worker is not None:
            return  # a task is already running
        from devworkbench.workers.task_stream_worker import TaskStreamWorker

        worker = TaskStreamWorker()
        self._run_worker = worker
        self._task_row = self.output.begin_task("Run checks")
        self._run_action.setEnabled(False)
        self._stop_action.setEnabled(True)
        self.status_bar.set_message("Running checks…")

        def _on_line(text: str) -> None:
            self.output.append_terminal(text)

        def _on_progress(percent: int, _payload=None) -> None:
            if self._task_row is not None:
                self.output.set_task_progress(self._task_row, percent)

        def _finish(message: str, ok: bool) -> None:
            if self._run_worker is None:
                return  # already cleaned up (cancelled)
            self._run_worker = None
            self._run_action.setEnabled(True)
            self._stop_action.setEnabled(False)
            if self._task_row is not None:
                self.output.finish_task(self._task_row, "Done" if ok else "Failed")
                self._task_row = None
            self.status_bar.set_message(message)

        worker.signals.line.connect(_on_line)
        worker.signals.progress.connect(_on_progress)
        worker.signals.finished.connect(lambda result: _finish(str(result), ok=True))
        worker.signals.error.connect(lambda exc: _finish(f"Task failed: {exc}", ok=False))
        QThreadPool.globalInstance().start(worker)

    def _shutdown_run_worker(self) -> None:
        """Cancel an in-flight task when the app quits (best effort)."""
        if self._run_worker is not None:
            self._run_worker.cancel()

    def _stop_task(self) -> None:
        """Cancel the running task (cooperative — checked between steps)."""
        worker = self._run_worker
        if worker is None:
            return
        worker.cancel()
        self._finish_run("Cancelled")

    def _finish_run(self, message: str) -> None:
        """Idempotent UI cleanup for a cancelled task."""
        if self._run_worker is None:
            return
        self._run_worker = None
        self._run_action.setEnabled(True)
        self._stop_action.setEnabled(False)
        if self._task_row is not None:
            self.output.finish_task(self._task_row, "Cancelled")
            self._task_row = None
        self.status_bar.set_message(message)

    def _not_wired(self) -> None:
        self.status_bar.set_message("Not wired yet — UI scaffold")

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br>"
            "A lightweight, native developer toolbox for macOS.<br><br>"
            "UI scaffold — business logic lands with the core framework.",
        )
