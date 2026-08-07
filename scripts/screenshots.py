#!/usr/bin/env python3
"""Render every DevWorkbench screen offscreen, dump the widget hierarchy,
and emit an HTML gallery with Retina (2x) screenshots embedded as data URIs.

Usage:  .venv/bin/python scripts/screenshots.py
Output: docs/ui/screens/*.png, docs/ui/ui-hierarchy.txt, docs/ui/gallery.html
"""

from __future__ import annotations

import base64
import os
import pathlib
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QBuffer, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from devworkbench.core.container import DependencyContainer  # noqa: E402
from devworkbench.core.events import EventBus  # noqa: E402
from devworkbench.core.paths import Paths  # noqa: E402
from devworkbench.core.settings import SettingsManager  # noqa: E402
from devworkbench.modules import MODULES  # noqa: E402
from devworkbench.modules.base import ModuleContext  # noqa: E402
from devworkbench.services.configuration_service import ConfigurationService  # noqa: E402
from devworkbench.ui.icons import IconProvider  # noqa: E402
from devworkbench.ui.main_window import MainWindow  # noqa: E402
from devworkbench.ui.theme import ThemeManager  # noqa: E402

OUT = ROOT / "docs" / "ui"
SHOTS = OUT / "screens"
DPR = 2  # retina render


def grab(window: QWidget) -> QPixmap:
    pm = QPixmap(window.size() * DPR)
    pm.setDevicePixelRatio(DPR)
    window.render(pm)
    return pm


def dump_hierarchy(window: QWidget, path: pathlib.Path) -> None:
    lines: list[str] = []

    def walk(widget: QWidget, depth: int, seen: set[int]) -> None:
        key = id(widget)
        if key in seen:
            return
        seen.add(key)
        name = widget.objectName()
        meta = type(widget).__name__
        if name:
            meta += f'  [objectName="{name}"]'
        layout = widget.layout()
        if layout is not None:
            meta += f"  ({type(layout).__name__})"
        lines.append("    " * depth + meta)
        for child in widget.children():
            if isinstance(child, QWidget) and child.parentWidget() is widget:
                walk(child, depth + 1, seen)

    lines.append(f"# DevWorkbench widget hierarchy — {window.windowTitle()} {window.size().width()}x{window.size().height()}")
    lines.append("")
    walk(window, 0, set())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def to_data_uri(pm: QPixmap) -> str:
    image = pm.toImage()
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    data = bytes(buffer.data())
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def build_gallery(shots: list[tuple[str, str, str]]) -> None:
    cards = []
    for title, caption, uri in shots:
        cards.append(
            f"""
            <section>
              <h2>{title}</h2>
              <p>{caption}</p>
              <img src="{uri}" alt="{title}">
            </section>"""
        )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DevWorkbench — UI gallery</title>
<style>
  body {{ background:#0f1014; color:#dfe3ea; font:13px/1.6 -apple-system,
        "Helvetica Neue", sans-serif; margin:0; padding:32px 40px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#7f8899; margin:0 0 28px; }}
  section {{ margin:0 0 40px; }}
  h2 {{ font-size:14px; color:#9db2dd; margin:0 0 4px; letter-spacing:.3px; }}
  p {{ color:#7f8899; margin:0 0 10px; font-size:12px; }}
  img {{ display:block; width:100%; max-width:1280px; border:1px solid #23262e;
        border-radius:10px; box-shadow:0 14px 40px rgba(0,0,0,.55);
        background:#16171c; }}
</style></head><body>
<h1>DevWorkbench — UI gallery</h1>
<p class="sub">Rendered offscreen at 2&times; (Retina). Dark theme, all seven modules.</p>
{''.join(cards)}
</body></html>"""
    (OUT / "gallery.html").write_text(html, encoding="utf-8")


def _pump_until(predicate, timeout_ms: int = 2000) -> None:
    """Process events until ``predicate`` is true or the timeout elapses.

    Needed because the Compare view runs its engine in a thread pool worker;
    a screenshot taken too early would catch the "Comparing…" state.
    """
    import time

    deadline = time.monotonic() + timeout_ms / 1000
    app = QApplication.instance()
    while time.monotonic() < deadline:
        app.processEvents()
        try:
            if predicate():
                return
        except Exception:  # noqa: BLE001 — widget lookups during teardown
            return
        time.sleep(0.02)


def main() -> int:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(["devworkbench-screenshots"])
    ThemeManager.install(app)
    icons = IconProvider()

    # Minimal DI context so the Settings screen binds real (memory-only)
    # ConfigurationService defaults — no database, no Keychain writes.
    container = DependencyContainer()
    container.register_singleton("core.paths", Paths())
    container.register_singleton("core.events", EventBus())
    settings = SettingsManager()
    container.register_singleton("core.settings", settings)
    container.register_singleton(
        "services.configuration",
        ConfigurationService(
            settings=settings,
            repository=None,
            keychain=None,
            events=container.resolve("core.events"),
        ),
    )
    config_service = container.resolve("services.configuration")
    ctx = ModuleContext(container)
    win = MainWindow(modules=MODULES, icons=icons, ctx=ctx)
    win.resize(1440, 900)
    win.show()
    app.processEvents()

    # The menu-manager default is compare-only; the gallery is meant to show
    # every screen, so enable all modules and menu-bar menus for the capture
    # (in-memory only — no database in this session).
    config_service.apply(
        {
            "ui.menu_file": True, "ui.menu_edit": True, "ui.menu_view": True,
            "ui.menu_module": True, "ui.menu_help": True,
            "ui.show_compare": True, "ui.show_git": True, "ui.show_ai": True,
            "ui.show_ssh": True, "ui.show_loganalyzer": True, "ui.show_plugins": True,
        }
    )
    app.processEvents()

    SHOTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    shots: list[tuple[str, str, str]] = []

    def capture(name: str, title: str, caption: str) -> None:
        app.processEvents()
        pm = grab(win)
        pm.save(str(SHOTS / f"{name}.png"))
        shots.append((title, caption, to_data_uri(pm)))

    # 1) every module, dark theme, default shell layout
    for i, module in enumerate(MODULES):
        win.workspace.setCurrentIndex(i)
        capture(
            module.id,
            module.title,
            f"{module.id} · default shell (sidebar + navigator + output docks)",
        )

    # 2) light theme on Compare
    win.workspace.setCurrentIndex(0)
    win.theme.toggle()
    capture("compare-light", "Compare — light theme", "Same screen, light theme via View → Toggle Theme (⌘D)")
    win.theme.toggle()

    # 3) Git home (repository manager) + collapsed sidebar
    win.workspace.setCurrentIndex(1)
    capture(
        "git-home",
        "Git — repository home",
        "Home page: demo repositories grouped under headers, search + group filter toolbar, Add repository",
    )
    win.sidebar.set_collapsed(True)
    capture("git-collapsed", "Git — collapsed sidebar", "Sidebar collapsed to an icon rail")
    win.sidebar.set_collapsed(False)

    # 4) docks hidden on Log Analyzer
    win.workspace.setCurrentIndex(4)
    win._navigator_dock.hide()
    win._output_dock.hide()
    capture("loganalyzer-focus", "Log Analyzer — focus mode", "Navigator & output docks hidden (View menu toggles)")
    win._navigator_dock.show()
    win._output_dock.show()

    # 5) Compare deep-dives: live diff panes + folder table
    from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton, QTreeWidget  # noqa: E402
    import time  # noqa: E402

    compare_index = next(i for i, m in enumerate(MODULES) if m.id == "compare")
    win.workspace.setCurrentIndex(compare_index)
    compare_view = win.workspace.widget(compare_index)
    demo_button = next(b for b in compare_view.findChildren(QPushButton) if b.property("class") == "primary")
    _pump_until(lambda: demo_button.text() == "Compare", timeout_ms=4000)  # demo worker done
    app.processEvents()
    capture("compare-diff", "Compare — file diff", "Live engine output: +/− stats, hunks, intra-line highlights, encoding")

    # Search bar open with live matches (highlights + count in the status row).
    search_input = next(e for e in compare_view.findChildren(QLineEdit) if e.property("class") == "search")
    search_input.parentWidget().setVisible(True)
    search_input.setText("import")
    time.sleep(0.35)  # let the debounced live search fire
    app.processEvents()
    capture("compare-search", "Compare — search", "Find bar with live match highlighting (⌘F), counts, case toggle, replace")
    search_input.setText("")
    search_input.parentWidget().setVisible(False)

    # Inline (unified) compare mode.
    view_combo = next(c for c in compare_view.findChildren(QComboBox) if c.itemText(0) == "Side by side")
    view_combo.setCurrentIndex(view_combo.findData("inline"))
    app.processEvents()
    capture("compare-inline", "Compare — inline", "Unified view: removed lines immediately followed by their additions")
    view_combo.setCurrentIndex(view_combo.findData("sbs"))

    # Folder compare: build two small temp trees and run it.
    import pathlib, tempfile

    root = pathlib.Path(tempfile.mkdtemp(prefix="dwb-shot-"))
    left_dir, right_dir = root / "left", root / "right"
    left_dir.mkdir(); right_dir.mkdir()
    (left_dir / "README.md").write_text("# Demo\n\nCompare me.\n")
    (right_dir / "README.md").write_text("# Demo\n\nCompare me too.\n")
    (left_dir / "config.json").write_text('{"debug": true}')
    (right_dir / "config.json").write_text('{"debug": false}')
    (left_dir / "only-left.txt").write_text("left only")
    (right_dir / "only-right.txt").write_text("right only")
    (left_dir / "sub").mkdir()
    (right_dir / "sub").mkdir()
    (left_dir / "sub" / "same.txt").write_text("same")
    (right_dir / "sub" / "same.txt").write_text("same")

    edits = compare_view.findChildren(QLineEdit)
    mode_combo = next(c for c in compare_view.findChildren(QComboBox) if c.count() == 2)
    mode_combo.setCurrentIndex(mode_combo.findData("folders"))
    edits[0].setText(str(left_dir))
    edits[1].setText(str(right_dir))
    compare_button = next(b for b in compare_view.findChildren(QPushButton) if b.property("class") == "primary")
    compare_button.click()
    _pump_until(lambda: compare_button.text() == "Compare", timeout_ms=4000)
    app.processEvents()
    capture("compare-folder", "Compare — folder tree", "Recursive walk as a collapsible tree: directories summarize their subtree (2 mod · 1 add), files carry sizes and timestamps")

    # Hide-identical filter: identical files and identical-only subtrees vanish.
    hide_identical = next(b for b in compare_view.findChildren(QPushButton) if b.objectName() == "hideIdenticalButton")
    hide_identical.click()
    app.processEvents()
    capture("compare-folder-filtered", "Compare — hide identical", "Hide-identical filter: only differing files and folders remain")
    hide_identical.click()
    app.processEvents()

    # Select the modified row so the sync toolbar (copy/delete/refresh) is live.
    tree = next(w for w in compare_view.findChildren(QTreeWidget) if w.objectName() == "folderTree")
    selected_item = next(
        item
        for item in tree.findItems("modified", Qt.MatchFlag.MatchStartsWith | Qt.MatchFlag.MatchRecursive, 0)
        if item.data(0, Qt.ItemDataRole.UserRole) is not None
    )
    tree.setCurrentItem(selected_item)
    app.processEvents()
    capture("compare-folder-sync", "Compare — folder sync", "Selected row + sync toolbar: Copy → right, Copy ← left, Delete, refresh")

    # File diff in its own tab: activate a row — the folder tree stays on
    # tab 0 and the selected file opens side-by-side in a new tab.
    from PySide6.QtWidgets import QSplitter, QTabWidget  # noqa: E402

    tree.itemActivated.emit(selected_item, 0)
    tabs = next(w for w in compare_view.findChildren(QTabWidget) if w.objectName() == "compareTabs")
    _pump_until(lambda: tabs.count() == 2, timeout_ms=4000)
    _pump_until(
        lambda: next(
            w for w in tabs.widget(1).findChildren(QSplitter) if w.count() == 2
        ).widget(0).status_info()["total_lines"] >= 1,
        timeout_ms=4000,
    )
    app.processEvents()
    capture(
        "compare-folder-diff-tab",
        "Compare — file diff in a tab",
        "Folder results stay visible on tab 0; the selected file opens its own diff tab",
    )
    tree.clearSelection()
    tabs.setCurrentIndex(0)
    mode_combo.setCurrentIndex(mode_combo.findData("files"))

    # 6) Settings deep-dives: Menus (menu manager), AI credentials, validation,
    #    Advanced. Page rows are resolved by id — never hardcoded indices.
    def page_row(settings_view, page_id: str) -> int:
        for i, page in enumerate(settings_view._pages):
            if page.page_id == page_id:
                return i
        raise AssertionError(f"no settings page {page_id!r}")

    settings_index = next(i for i, m in enumerate(MODULES) if m.id == "settings")
    win.workspace.setCurrentIndex(settings_index)
    settings_view = win.workspace.widget(settings_index)

    settings_view._nav.setCurrentRow(page_row(settings_view, "menus"))
    capture("settings-menus", "Settings — Menus", "Menu manager: show/hide menu-bar menus and module screens, applied live")

    settings_view._nav.setCurrentRow(page_row(settings_view, "ai"))
    capture("settings-ai", "Settings — AI", "Provider, model and Keychain-stored API credentials")

    ai_page = settings_view._pages[page_row(settings_view, "ai")]
    url_edit = next(f.widget for f in ai_page._fields if f.key == "ai.openai_base_url")
    url_edit.setText("not-a-url")
    settings_view._on_apply()
    capture("settings-validation", "Settings — validation", "Invalid Base URL: inline field error + summary banner, nothing persisted")
    url_edit.setText("https://api.openai.com/v1")
    settings_view._on_apply()

    settings_view._nav.setCurrentRow(page_row(settings_view, "advanced"))
    capture("settings-advanced", "Settings — Advanced", "Paths, diagnostics and maintenance actions")
    settings_view._nav.setCurrentRow(0)

    dump_hierarchy(win, OUT / "ui-hierarchy.txt")
    build_gallery(shots)
    print(f"Wrote {len(shots)} screenshots → docs/ui/screens/, hierarchy → ui-hierarchy.txt, gallery → gallery.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
