"""Home module — app landing: overview, continue, module shortcuts."""

from __future__ import annotations

import os
from collections import defaultdict

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from devworkbench import APP_NAME, __version__
from devworkbench.modules.base import Module, ModuleContext
from devworkbench.services.configuration_service import (
    TOPIC_GIT_OPEN_GROUP,
    TOPIC_NAVIGATION_REQUEST,
)
from devworkbench.ui.theme import current_colors
from devworkbench.ui.widgets.common import styled_label


def _shortcut_modules() -> list:
    """Lazy import avoids a circular import with ``modules.__init__``."""
    from devworkbench.modules.ai import ai_module
    from devworkbench.modules.compare import compare_module
    from devworkbench.modules.git import git_module
    from devworkbench.modules.loganalyzer import loganalyzer_module
    from devworkbench.modules.plugins import plugins_module
    from devworkbench.modules.settings import settings_module
    from devworkbench.modules.ssh import ssh_module

    return [
        compare_module,
        git_module,
        ai_module,
        ssh_module,
        loganalyzer_module,
        settings_module,
        plugins_module,
    ]


def build_view(icons, ctx: ModuleContext | None = None) -> QWidget:
    favorites_repo = (
        ctx.resolve("database.repositories.favorites")
        if ctx is not None and ctx.has("database.repositories.favorites")
        else None
    )
    history_repo = (
        ctx.resolve("database.repositories.history")
        if ctx is not None and ctx.has("database.repositories.history")
        else None
    )
    config = (
        ctx.resolve("services.configuration")
        if ctx is not None and ctx.has("services.configuration")
        else None
    )
    events = (
        ctx.resolve("core.events")
        if ctx is not None and ctx.has("core.events")
        else None
    )

    root = QWidget()
    root.setObjectName("homeRoot")
    outer = QVBoxLayout(root)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    scroll = QScrollArea()
    scroll.setObjectName("homeScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameStyle(0)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    outer.addWidget(scroll, 1)

    page = QWidget()
    page.setObjectName("homePage")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(28, 24, 28, 32)
    layout.setSpacing(22)

    hero = QWidget()
    hero.setObjectName("homeHero")
    hero_layout = QVBoxLayout(hero)
    hero_layout.setContentsMargins(4, 4, 4, 8)
    hero_layout.setSpacing(6)
    brand = QLabel(APP_NAME)
    brand.setObjectName("homeBrand")
    brand_font = QFont()
    brand_font.setPointSize(28)
    brand_font.setBold(True)
    brand.setFont(brand_font)
    hero_layout.addWidget(brand)
    tagline = QLabel("Your local workspace hub — compare, git, and tools in one place.")
    tagline.setObjectName("homeTagline")
    tagline.setWordWrap(True)
    hero_layout.addWidget(tagline)
    layout.addWidget(hero)

    chips_row = QHBoxLayout()
    chips_row.setSpacing(10)
    chips_row.setContentsMargins(0, 0, 0, 0)
    chip_groups = _stat_chip("Groups", "—")
    chip_repos = _stat_chip("Repos", "—")
    chip_recent = _stat_chip("Recent", "—")
    chip_version = _stat_chip("Version", f"v{__version__}")
    for chip in (chip_groups, chip_repos, chip_recent, chip_version):
        chips_row.addWidget(chip)
    chips_row.addStretch(1)
    layout.addLayout(chips_row)

    body = QHBoxLayout()
    body.setSpacing(20)
    body.setContentsMargins(0, 0, 0, 0)

    continue_col = QWidget()
    continue_col.setObjectName("homeContinue")
    continue_layout = QVBoxLayout(continue_col)
    continue_layout.setContentsMargins(0, 0, 0, 0)
    continue_layout.setSpacing(10)
    continue_title = QLabel("Continue")
    continue_title.setObjectName("homeSectionTitle")
    continue_layout.addWidget(continue_title)

    continue_layout.addWidget(styled_label("Recent groups", "hint"))
    groups_box = QVBoxLayout()
    groups_box.setSpacing(6)
    groups_box.setContentsMargins(0, 0, 0, 0)
    continue_layout.addLayout(groups_box)

    continue_layout.addWidget(styled_label("Recent folders", "hint"))
    folders_box = QVBoxLayout()
    folders_box.setSpacing(6)
    folders_box.setContentsMargins(0, 0, 0, 0)
    continue_layout.addLayout(folders_box)
    continue_layout.addStretch(1)

    modules_col = QWidget()
    modules_col.setObjectName("homeModules")
    modules_layout = QVBoxLayout(modules_col)
    modules_layout.setContentsMargins(0, 0, 0, 0)
    modules_layout.setSpacing(10)
    modules_title = QLabel("Modules")
    modules_title.setObjectName("homeSectionTitle")
    modules_layout.addWidget(modules_title)
    modules_grid = QGridLayout()
    modules_grid.setHorizontalSpacing(10)
    modules_grid.setVerticalSpacing(10)
    modules_layout.addLayout(modules_grid)
    modules_layout.addStretch(1)

    body.addWidget(continue_col, 3)
    body.addWidget(modules_col, 2)
    layout.addLayout(body, 1)
    layout.addStretch(1)
    scroll.setWidget(page)

    def _module_visible(module_id: str) -> bool:
        if module_id in ("home", "settings"):
            return True
        if config is None:
            return True
        try:
            return bool(config.get(f"ui.show_{module_id}"))
        except Exception:  # noqa: BLE001
            return True

    def _navigate(module_id: str) -> None:
        if events is None:
            return
        events.publish(TOPIC_NAVIGATION_REQUEST, module_id=module_id)

    def _open_git_group(group: str) -> None:
        if config is not None:
            try:
                config.set("git.home.group", group)
            except Exception:  # noqa: BLE001
                pass
        if events is not None:
            events.publish(TOPIC_NAVIGATION_REQUEST, module_id="git")
            events.publish(TOPIC_GIT_OPEN_GROUP, group=group)

    def _open_recent_folder(path: str) -> None:
        group = ""
        if favorites_repo is not None:
            favorite = favorites_repo.find("folder", path)
            if favorite is not None:
                group = (favorite.group_name or "").strip()
        if config is not None:
            try:
                config.set("git.home.group", group)
            except Exception:  # noqa: BLE001
                pass
        if events is not None:
            events.publish(TOPIC_NAVIGATION_REQUEST, module_id="git")
            events.publish(TOPIC_GIT_OPEN_GROUP, group=group)

    def _clear_box(box: QVBoxLayout) -> None:
        while box.count():
            item = box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _link_button(text: str, tip: str = "") -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("homeLink")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setToolTip(tip or text)
        return btn

    def refresh() -> None:
        favorites = (
            favorites_repo.by_kind("folder") if favorites_repo is not None else []
        )
        groups: dict[str, int] = defaultdict(int)
        for favorite in favorites:
            key = (favorite.group_name or "").strip()
            groups[key] += 1
        named_groups = {k: v for k, v in groups.items() if k}

        recent = (
            history_repo.recent_folders(limit=8) if history_repo is not None else []
        )

        chip_groups.findChild(QLabel, "homeChipValue").setText(str(len(named_groups)))
        chip_repos.findChild(QLabel, "homeChipValue").setText(str(len(favorites)))
        chip_recent.findChild(QLabel, "homeChipValue").setText(str(len(recent)))

        ordered_groups: list[str] = []
        seen: set[str] = set()
        path_to_group = {f.ref: (f.group_name or "").strip() for f in favorites}
        for entry in recent:
            g = path_to_group.get(entry.path, "")
            if g and g not in seen:
                seen.add(g)
                ordered_groups.append(g)
        for g in sorted(named_groups, key=str.casefold):
            if g not in seen:
                ordered_groups.append(g)
        ordered_groups = ordered_groups[:6]

        _clear_box(groups_box)
        if not ordered_groups:
            empty = styled_label("No git groups yet — add repositories in Git.", "hint")
            empty.setWordWrap(True)
            groups_box.addWidget(empty)
        else:
            for name in ordered_groups:
                count = named_groups.get(name, 0)
                noun = "repo" if count == 1 else "repos"
                btn = _link_button(f"{name}  ·  {count} {noun}", f"Open Git → {name}")
                btn.clicked.connect(lambda _c=False, g=name: _open_git_group(g))
                groups_box.addWidget(btn)

        _clear_box(folders_box)
        if not recent:
            empty = styled_label("Open a repository folder to see it here.", "hint")
            empty.setWordWrap(True)
            folders_box.addWidget(empty)
        else:
            for entry in recent:
                path = entry.path
                label = os.path.basename(path.rstrip("/")) or path
                btn = _link_button(label, path)
                btn.clicked.connect(lambda _c=False, p=path: _open_recent_folder(p))
                folders_box.addWidget(btn)

        while modules_grid.count():
            item = modules_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        tiles = [m for m in _shortcut_modules() if _module_visible(m.id)]
        for index, module in enumerate(tiles):
            tile = QPushButton()
            tile.setObjectName("homeTile")
            tile.setCursor(Qt.CursorShape.PointingHandCursor)
            tile.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            tile.setMinimumHeight(72)
            tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tile_layout = QHBoxLayout(tile)
            tile_layout.setContentsMargins(12, 10, 12, 10)
            tile_layout.setSpacing(10)
            icon_label = QLabel()
            colors = current_colors()
            icon_label.setPixmap(
                icons.get(module.icon, 22, color=colors.get("accent")).pixmap(22, 22)
            )
            # Child labels steal clicks from QPushButton unless they ignore mouse.
            icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            tile_layout.addWidget(icon_label)
            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            title = QLabel(module.title)
            title.setObjectName("homeTileTitle")
            title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            text_col.addWidget(title)
            hint = styled_label("Open module", "hint")
            hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            text_col.addWidget(hint)
            tile_layout.addLayout(text_col, 1)
            tile.clicked.connect(lambda _c=False, mid=module.id: _navigate(mid))
            modules_grid.addWidget(tile, index // 2, index % 2)

    class _RootShowFilter(QObject):
        def eventFilter(self, obj, event):  # noqa: N802
            if event.type() == QEvent.Type.Show and obj is root:
                refresh()
            return False

    filt = _RootShowFilter(root)
    root.installEventFilter(filt)
    root._home_show_filter = filt  # noqa: SLF001 — retain filter
    refresh()
    return root


def _stat_chip(label: str, value: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName("homeChip")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(2)
    title = QLabel(label.upper())
    title.setObjectName("homeChipLabel")
    layout.addWidget(title)
    val = QLabel(value)
    val.setObjectName("homeChipValue")
    layout.addWidget(val)
    return frame


home_module = Module(
    id="home",
    title="Home",
    icon="home",
    build=build_view,
    navigator=(("Overview", ("Continue", "Modules")),),
    details=(
        ("Screen", "Home"),
        ("Role", "Landing"),
    ),
    status="Home · workspace overview",
)
