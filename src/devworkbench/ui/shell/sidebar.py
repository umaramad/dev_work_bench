"""Sidebar — the left navigation rail.

Icon + label items for every module, a collapsible rail mode, and an accent
highlight for the active module. Emits ``module_activated`` for the window to
switch the workspace tab.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from devworkbench.ui.theme import current_colors


class Sidebar(QWidget):
    module_activated = Signal(str)
    collapse_toggled = Signal(bool)

    def __init__(self, modules, icons) -> None:
        super().__init__()
        self._icons = icons
        self._modules = {m.id: m for m in modules}
        self._buttons: dict[str, QToolButton] = {}
        self._collapsed = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(4)

        # App header
        self._header = QWidget()
        header_row = QHBoxLayout(self._header)
        header_row.setContentsMargins(6, 4, 6, 10)
        header_row.setSpacing(8)
        app_icon = QLabel()
        app_icon.setPixmap(icons.get("app", 20).pixmap(QSize(20, 20)))
        self._title = QLabel("DevWorkbench")
        self._title.setObjectName("sectionTitle")
        header_row.addWidget(app_icon)
        header_row.addWidget(self._title)
        header_row.addStretch(1)
        self._layout.addWidget(self._header)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for module in modules:
            btn = QToolButton(self)
            btn.setObjectName("navItem")
            btn.setText(module.title)
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setMinimumHeight(34)
            btn.setIcon(icons.get(module.icon, 18))
            btn.setToolTip(module.title)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(btn)
            btn.clicked.connect(lambda checked=False, m=module: self.module_activated.emit(m.id))
            self._buttons[module.id] = btn
            self._layout.addWidget(btn)

        self._layout.addStretch(1)

        # Collapse toggle at the bottom
        self._collapse_btn = QToolButton(self)
        self._collapse_btn.setObjectName("navItem")
        self._collapse_btn.setText("Collapse")
        self._collapse_btn.setIcon(icons.get("chevron_left", 16))
        self._collapse_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._collapse_btn.setMinimumHeight(34)
        self._collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_btn.clicked.connect(self._on_collapse_clicked)
        self._layout.addWidget(self._collapse_btn)

        self.setMinimumWidth(208)

    # -- public -------------------------------------------------------------

    def set_active(self, module_id: str) -> None:
        btn = self._buttons.get(module_id)
        if btn is not None:
            btn.setChecked(True)
            self._recolor()

    def set_module_visible(self, module_id: str, visible: bool) -> None:
        """Show or hide the nav button for ``module_id`` (menu manager)."""
        button = self._buttons.get(module_id)
        if button is not None:
            button.setVisible(visible)

    def set_collapsed(self, collapsed: bool) -> None:
        """Switch the rail between a full nav list and a narrow icon rail.

        The widget's minimum width wins over the dock's, so it must follow
        the collapsed state — otherwise the dock cannot compress below the
        full width and the user is left with a wide rail of empty icon-only
        rows (the icons disappear into a sea of empty buttons).
        """
        self._collapsed = collapsed
        self.setMinimumWidth(56 if collapsed else 208)
        for module in self._modules.values():
            btn = self._buttons[module.id]
            if collapsed:
                btn.setText("")
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            else:
                btn.setText(module.title)
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._title.setVisible(not collapsed)
        if collapsed:
            self._collapse_btn.setIcon(self._icons.get("chevron_right", 16))
            self._collapse_btn.setText("")
            self._collapse_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        else:
            self._collapse_btn.setIcon(self._icons.get("chevron_left", 16))
            self._collapse_btn.setText("Collapse")
            self._collapse_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        # Re-tint every icon against the current theme so the icon rail always
        # shows crisp glyphs (icons are cached per resolved color).
        self._recolor()
        self.collapse_toggled.emit(collapsed)

    # -- internals ------------------------------------------------------------

    def _on_collapse_clicked(self) -> None:
        self.set_collapsed(not self._collapsed)

    def _recolor(self) -> None:
        colors = current_colors()
        checked = self._group.checkedButton()
        for module in self._modules.values():
            btn = self._buttons[module.id]
            color = colors["accent"] if btn is checked else colors["text2"]
            btn.setIcon(self._icons.get(module.icon, 18, color=color))
