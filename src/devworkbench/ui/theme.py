"""Theme engine — dark & light QSS themes built for Qt Widgets.

The QSS is a single template with named color tokens (``__token__``), so a
theme is just a token dict. ``ThemeManager`` installs the palette + stylesheet
and can switch live (used by View → Toggle Theme).
"""

from __future__ import annotations

import re

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# --------------------------------------------------------------------------
# Color tokens per theme
# --------------------------------------------------------------------------

DARK: dict[str, str] = {
    "bg": "#16171c",
    "surface": "#1f2126",
    "surface2": "#262a32",
    "raise": "#2c313b",
    "border": "#2c303a",
    "border2": "#3b4150",
    "text": "#e3e7ef",
    "text2": "#a4adbd",
    "text3": "#6d7686",
    "accent": "#5b8def",
    "accentHover": "#709df6",
    "accentPress": "#4a7cdb",
    "accentSoft": "rgba(91, 141, 239, 0.16)",
    "accentText": "#ffffff",
    "green": "#4cc38a",
    "red": "#e06c6c",
    "redSoft": "rgba(224, 108, 108, 0.13)",
    "redBorder": "rgba(224, 108, 108, 0.55)",
    "amber": "#e2a94f",
    "cyan": "#5cc8d6",
    "purple": "#b98ce8",
    "selection": "rgba(91, 141, 239, 0.35)",
    "scroll": "#3a4050",
    "scrollHover": "#525b6f",
    "inputBg": "#14151a",
    "toolbarBg": "#1b1d23",
    "menuBg": "#23262e",
    "tabBarBg": "#1b1d23",
    "panel": "#1f2126",
    "chatUser": "#3a5fae",
    "rowHover": "#262a32",
}

LIGHT: dict[str, str] = {
    "bg": "#f2f3f6",
    "surface": "#ffffff",
    "surface2": "#eceef3",
    "raise": "#e3e6ee",
    "border": "#dfe2ea",
    "border2": "#c9cede",
    "text": "#232833",
    "text2": "#5c6577",
    "text3": "#9aa2b4",
    "accent": "#2f6fdd",
    "accentHover": "#1f63d6",
    "accentPress": "#2459b4",
    "accentSoft": "rgba(47, 111, 221, 0.14)",
    "accentText": "#ffffff",
    "green": "#2c9d68",
    "red": "#d05050",
    "redSoft": "rgba(208, 80, 80, 0.11)",
    "redBorder": "rgba(208, 80, 80, 0.5)",
    "amber": "#c08a2d",
    "cyan": "#1f9bb0",
    "purple": "#8a5fd0",
    "selection": "rgba(47, 111, 221, 0.28)",
    "scroll": "#c2c8d6",
    "scrollHover": "#a9b0c2",
    "inputBg": "#ffffff",
    "toolbarBg": "#f7f8fa",
    "menuBg": "#ffffff",
    "tabBarBg": "#f7f8fa",
    "panel": "#ffffff",
    "chatUser": "#2f6fdd",
    "rowHover": "#eceef3",
}

THEMES: dict[str, dict[str, str]] = {"dark": DARK, "light": LIGHT}

# --------------------------------------------------------------------------
# QSS template — tokens replaced at apply time
# --------------------------------------------------------------------------

_QSS = """
* { outline: 0; }

QMainWindow, QDialog { background: __bg; }
QWidget { color: __text; font-size: 13px; }
QToolTip { background: __menuBg; color: __text; border: 1px solid __border2;
           padding: 4px 8px; border-radius: 4px; }

/* ---------- menu bar & menus ---------- */
QMenuBar { background: __toolbarBg; color: __text2;
           border-bottom: 1px solid __border; padding: 2px 6px; }
QMenuBar::item { padding: 4px 10px; border-radius: 5px; background: transparent; }
QMenuBar::item:selected { background: __accentSoft; color: __text; }
QMenu { background: __menuBg; color: __text; border: 1px solid __border2;
        padding: 6px; border-radius: 6px; }
QMenu::item { padding: 5px 24px 5px 12px; border-radius: 4px; }
QMenu::item:selected { background: __accentSoft; }
QMenu::item:disabled { color: __text3; }
QMenu::separator { height: 1px; background: __border; margin: 5px 8px; }

/* ---------- toolbar ---------- */
QToolBar { background: __toolbarBg; border: none;
           border-bottom: 1px solid __border; padding: 3px 8px; spacing: 2px; }
QToolBar::separator { width: 1px; background: __border; margin: 5px 8px; }
QToolButton { background: transparent; border: none; border-radius: 6px;
              padding: 4px; color: __text2; }
QToolButton:hover { background: __surface2; color: __text; }
QToolButton:pressed { background: __accentSoft; }
QToolButton:checked { background: __accentSoft; color: __text; }
QToolButton:disabled { color: __text3; }
QToolButton#navItem { text-align: left; padding: 7px 9px; border-radius: 7px;
                      color: __text2; font-size: 13px; }
QToolButton#navItem:hover { background: __surface2; color: __text; }
QToolButton#navItem:checked { background: __accentSoft; color: __text; }
QToolButton#chip { border: 1px solid __border2; border-radius: 12px;
                   padding: 3px 11px; color: __text2; }
QToolButton#chip:hover { background: __surface2; color: __text; }
QToolButton#chip:checked { background: __accentSoft; color: __text;
                           border-color: __accent; }

/* ---------- status bar ---------- */
QStatusBar { background: __toolbarBg; border-top: 1px solid __border;
             color: __text2; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: __text2; padding: 0 6px; font-size: 12px; }

/* ---------- docks ---------- */
QDockWidget { color: __text2; font-size: 12px; }
QDockWidget::title { background: __surface; color: __text2;
                     border-bottom: 1px solid __border; padding: 5px 10px;
                     text-align: left; font-weight: 600; }
QDockWidget::close-button, QDockWidget::float-button { border-radius: 4px;
                     padding: 2px; background: transparent; }
QDockWidget::close-button:hover, QDockWidget::float-button:hover {
                     background: __surface2; }

/* ---------- tabs ---------- */
QTabWidget::pane { border: none; background: __bg; }
QTabBar { background: __tabBarBg; }
QTabBar::tab { background: transparent; color: __text2; padding: 7px 14px;
               border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: __text; }
QTabBar::tab:selected { color: __text; border-bottom: 2px solid __accent; }
QTabBar::tab:disabled { color: __text3; }

/* ---------- splitters ---------- */
QSplitter::handle { background: __bg; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QSplitter::handle:hover { background: __accent; }
QSplitter::handle:pressed { background: __accent; }

/* ---------- trees / lists / tables ---------- */
QTreeWidget, QListWidget, QTableWidget {
    background: __surface; alternate-background-color: __bg;
    border: 1px solid __border; border-radius: 6px; }
QTreeWidget::item, QListWidget::item, QTableWidget::item { padding: 3px 6px; }
QTreeWidget::item:hover, QListWidget::item:hover { background: __rowHover; }
QTreeWidget::item:selected, QListWidget::item:selected,
QTableWidget::item:selected { background: __accentSoft; color: __text; }
/* NOTE: do NOT add a QTreeWidget::branch rule without an `image:` property —
   once a branch subcontrol rule exists, Qt stops drawing the default
   disclosure arrow, so expandable tree nodes lose their +/- indicator. */
QHeaderView::section { background: __surface; color: __text2; border: none;
    border-bottom: 1px solid __border; padding: 5px 8px; font-weight: 600; }
QTableWidget { gridline-color: __border; }
QTableCornerButton::section { background: __surface; border: none; }
QAbstractItemView { outline: 0; selection-background-color: __selection; }

/* ---------- inputs ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {
    background: __inputBg; border: 1px solid __border2; border-radius: 6px;
    padding: 5px 8px; selection-background-color: __accent; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus { border-color: __accent; }
QLineEdit[class="search"] { padding-left: 26px; }
QComboBox { background: __inputBg; border: 1px solid __border2;
            border-radius: 6px; padding: 5px 10px; }
QComboBox:focus { border-color: __accent; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QAbstractItemView { background: __menuBg; border: 1px solid __border2;
    selection-background-color: __accentSoft; selection-color: __text; }

/* ---------- buttons ---------- */
QPushButton { background: __surface2; color: __text; border: 1px solid __border2;
              border-radius: 6px; padding: 5px 14px; }
QPushButton:hover { background: __raise; border-color: __text3; }
QPushButton:pressed { background: __accentPress; color: __accentText; }
QPushButton:disabled { color: __text3; background: __surface; border-color: __border; }
QPushButton[class="primary"] { background: __accent; color: __accentText;
              border: 1px solid __accent; font-weight: 600; }
QPushButton[class="primary"]:hover { background: __accentHover; border-color: __accentHover; }
QPushButton[class="primary"]:pressed { background: __accentPress; }
QPushButton[class="ghost"] { background: transparent; border: 1px solid transparent;
              color: __text2; }
QPushButton[class="ghost"]:hover { background: __surface2; color: __text; }
QPushButton[class="danger"] { background: transparent; border: 1px solid transparent;
              color: __red; }
QPushButton[class="danger"]:hover { background: rgba(224, 108, 108, 0.12); }

/* ---------- scrollbars ---------- */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: __scroll; border-radius: 4px;
                             min-height: 24px; }
QScrollBar::handle:vertical:hover { background: __scrollHover; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: __scroll; border-radius: 4px;
                              min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: __scrollHover; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---------- progress / sliders ---------- */
QProgressBar { background: __surface2; border: 1px solid __border;
               border-radius: 4px; text-align: center; color: __text2;
               font-size: 11px; }
QProgressBar::chunk { background: __accent; border-radius: 3px; }
QSlider::groove:horizontal { height: 4px; background: __surface2;
                             border-radius: 2px; }
QSlider::handle:horizontal { background: __accent; width: 14px; height: 14px;
                             margin: -5px 0; border-radius: 7px; }

/* ---------- checkboxes / radios / groups ---------- */
QCheckBox, QRadioButton { spacing: 7px; color: __text; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; }
QCheckBox::indicator { border: 1px solid __border2; border-radius: 4px;
                       background: __inputBg; }
QCheckBox::indicator:hover { border-color: __accent; }
QCheckBox::indicator:checked { background: __accent; border-color: __accent; }
QRadioButton::indicator { border: 1px solid __border2; border-radius: 8px;
                          background: __inputBg; }
QRadioButton::indicator:checked { border: 5px solid __accent;
                                  background: __inputBg; }
QGroupBox { border: 1px solid __border; border-radius: 8px;
            margin-top: 14px; padding-top: 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px;
                   color: __text2; font-weight: 500; }

/* ---------- custom frames & bubbles ---------- */
QFrame#panel { background: __panel; border: 1px solid __border;
               border-radius: 8px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QFrame#panelFlat { background: __surface; }
QFrame#chatUser { background: __chatUser; border-radius: 10px;
                  border-bottom-right-radius: 2px; }
QFrame#chatAi { background: __surface2; border: 1px solid __border2;
                border-radius: 10px; border-bottom-left-radius: 2px; }
QFrame#chatError { background: __redSoft; border: 1px solid __redBorder;
                   border-radius: 10px; border-bottom-left-radius: 2px; }
QFrame#chatError QLabel { color: __red; }
QLabel#sectionTitle { font-size: 15px; font-weight: 700; color: __text; }
QLabel#muted { color: __text2; }
QLabel#tiny { color: __text3; font-size: 11px; }
QLabel#hint { color: __text3; font-size: 11px; }
QLabel#statusPill { background: __surface2; border: 1px solid __border2;
                    border-radius: 10px; padding: 2px 9px; font-size: 11px; }
QLabel#statusPill[state="ok"] { color: __green; }
QLabel#statusPill[state="warn"] { color: __amber; }
QLabel#statusPill[state="err"] { color: __red; }
QLabel#cardRemoteStatus { color: __text2; font-size: 11px; }
QLabel#cardRemoteStatus[state="ok"] { color: __green; }
QLabel#cardRemoteStatus[state="warn"] { color: __amber; }
QLabel#cardRemoteStatus[state="err"] { color: __red; }
QLabel#levelTrace { color: __text3; }
QLabel#levelDebug { color: __cyan; }
QLabel#levelInfo { color: __text2; }
QLabel#levelWarn { color: __amber; }
QLabel#levelError { color: __red; }

/* ---------- forms & validation ---------- */
QLabel#pageSubtitle { color: __text3; font-size: 12px; }
QLineEdit[invalid="true"], QSpinBox[invalid="true"],
QDoubleSpinBox[invalid="true"], QComboBox[invalid="true"] {
    border-color: __red; }
QLabel#fieldError { color: __red; font-size: 11px; }
QLabel#keychainHint { color: __text3; font-size: 11px; }
QFrame#errorBanner { background: __redSoft;
    border: 1px solid __redBorder; border-radius: 8px; }
QLabel#errorBannerText { color: __red; font-weight: 600; }
QLabel#errorBannerDetail { color: __text2; font-size: 12px; }
"""


def _render(tokens: dict[str, str]) -> str:
    """Substitute ``__token`` / ``__token__`` placeholders with theme colors.

    Token names are tried longest-first so ``__text2`` is never mistaken for
    ``__text``; trailing underscores are consumed greedily.
    """

    names = sorted(tokens, key=len, reverse=True)
    pattern = re.compile("__(" + "|".join(re.escape(n) for n in names) + ")_*")
    return pattern.sub(lambda m: tokens[m.group(1)], _QSS)


# --------------------------------------------------------------------------
# ThemeManager
# --------------------------------------------------------------------------

_current: dict[str, str] = DARK


def current_colors() -> dict[str, str]:
    """Access the active theme's tokens (for widgets that paint themselves)."""
    return _current


class ThemeManager:
    """Installs / switches the application theme (palette + stylesheet)."""

    def __init__(self, app: QApplication, name: str = "dark") -> None:
        self.app = app
        self._name = name

    @classmethod
    def install(cls, app: QApplication, name: str = "dark") -> "ThemeManager":
        manager = cls(app, name)
        manager.apply(name)
        return manager

    @property
    def name(self) -> str:
        return self._name

    def toggle(self) -> str:
        return self.apply("light" if self._name == "dark" else "dark")

    def apply(self, name: str) -> str:
        global _current
        tokens = THEMES[name]
        self._name = name
        _current = tokens
        self.app.setPalette(self._palette(tokens))
        self.app.setStyleSheet(_render(tokens))
        return name

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _palette(t: dict[str, str]) -> QPalette:
        p = QPalette()
        bg = QColor(t["bg"])
        surface = QColor(t["surface"])
        text = QColor(t["text"])
        text2 = QColor(t["text2"])
        accent = QColor(t["accent"])

        p.setColor(QPalette.ColorRole.Window, bg)
        p.setColor(QPalette.ColorRole.WindowText, text)
        p.setColor(QPalette.ColorRole.Base, surface)
        p.setColor(QPalette.ColorRole.AlternateBase, QColor(t["surface2"]))
        p.setColor(QPalette.ColorRole.Text, text)
        p.setColor(QPalette.ColorRole.Button, surface)
        p.setColor(QPalette.ColorRole.ButtonText, text)
        p.setColor(QPalette.ColorRole.ToolTipBase, QColor(t["menuBg"]))
        p.setColor(QPalette.ColorRole.ToolTipText, text)
        p.setColor(QPalette.ColorRole.Highlight, accent)
        p.setColor(QPalette.ColorRole.HighlightedText, QColor(t["accentText"]))
        p.setColor(QPalette.ColorRole.PlaceholderText, text2)
        p.setColor(QPalette.ColorRole.Link, accent)
        disabled = QPalette.ColorGroup.Disabled
        p.setColor(disabled, QPalette.ColorRole.Text, text2)
        p.setColor(disabled, QPalette.ColorRole.ButtonText, text2)
        return p
