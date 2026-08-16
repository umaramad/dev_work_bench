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
    "surface2": "#e8eaef",
    "raise": "#dce0e8",
    "border": "#b8bfcc",
    "border2": "#8f99ab",
    "text": "#1a1f2a",
    "text2": "#3d4556",
    "text3": "#5c6577",
    "accent": "#2f6fdd",
    "accentHover": "#1f63d6",
    "accentPress": "#2459b4",
    "accentSoft": "rgba(47, 111, 221, 0.16)",
    "accentText": "#ffffff",
    "green": "#248a5a",
    "red": "#c44545",
    "redSoft": "rgba(196, 69, 69, 0.12)",
    "redBorder": "rgba(196, 69, 69, 0.55)",
    "amber": "#a87420",
    "cyan": "#1a8799",
    "purple": "#7a52c0",
    "selection": "rgba(47, 111, 221, 0.28)",
    "scroll": "#a9b0c2",
    "scrollHover": "#8f99ab",
    "inputBg": "#ffffff",
    "toolbarBg": "#f7f8fa",
    "menuBg": "#ffffff",
    "tabBarBg": "#f7f8fa",
    "panel": "#ffffff",
    "chatUser": "#2f6fdd",
    "rowHover": "#e8eaef",
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
/* cardRemoteStatus rules live in the git-landing section below */
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

/* ---------- git landing: repo card grid ----------
   Two-column IconMode cards: avatar + branch top-right, action chips,
   sync footer. Theme tokens only (no separate dashboard palette).
   Semantic status: amber = ahead, cyan = behind, green = clean, red = diverged. */
QScrollArea#favoritesList, QListWidget#groupList {
    background: transparent; border: none; border-radius: 0; }
QListWidget#groupList::item { padding: 0; }
QWidget#favoritesHost { background: transparent; }
QLabel#groupsHeading { color: __text3; font-weight: 700; letter-spacing: 1.5px; }
QLabel#groupTitle { color: __text; font-weight: 700;
    font-family: "SF Mono", Menlo, monospace; }
QPushButton#groupRow { background: transparent; border: 1px solid __border;
    border-radius: 7px; }
QPushButton#groupRow:hover { background: __surface2; border-color: __border2; }
QPushButton#groupRow:checked { background: __surface;
    border: 1px solid __border2; border-left: 3px solid __amber; }
QWidget#commandDeck { background: __surface; border: 1px solid __border;
    border-radius: 8px; }
QWidget#repoCard { background: __surface; border: 1px solid __border;
    border-radius: 14px; }
QWidget#repoCard:hover { border-color: __border2; }
QLabel#repoAvatar {
    border-radius: 20px; color: #ffffff; font-weight: 700;
    background: __accent; }
QLabel#repoAvatar[tone="0"] { background: __accent; }
QLabel#repoAvatar[tone="1"] { background: __green; }
QLabel#repoAvatar[tone="2"] { background: __purple; }
QLabel#repoAvatar[tone="3"] { background: __amber; }
QLabel#repoAvatar[tone="4"] { background: __cyan; }
QLabel#repoName { font-family: "SF Mono", Menlo, monospace; font-weight: 600;
    color: __text; }
QLabel#repoPath { font-family: "SF Mono", Menlo, monospace; color: __text3; }
QLabel#cardBranch {
    font-family: "SF Mono", Menlo, monospace; font-size: 11px; color: __text2;
    background: __accentSoft; border: 1px solid __border2;
    border-radius: 10px; padding: 3px 8px; }
QPushButton#cardChip {
    border-radius: 999px; padding: 5px 10px; font-size: 11px; font-weight: 600;
    border: 1px solid __border2; background: __surface2; color: __text; }
QPushButton#cardChip:hover { background: __raise; border-color: __border2; }
QPushButton#cardChip[kind="primary"] {
    background: rgba(76, 195, 138, 0.14); border-color: __green; color: __green; }
QPushButton#cardChip[kind="accent"] {
    background: __accentSoft; border-color: __accent; color: __accent; }
QPushButton#cardChip[kind="ghost"] {
    background: transparent; border-color: __border; color: __text2; }
QPushButton#cardChip[kind="danger"] {
    background: __redSoft; border-color: __redBorder; color: __red; }
QLabel#cardRemoteStatus { font-family: "SF Mono", Menlo, monospace;
    font-size: 12px; color: __text2; }
QLabel#cardRemoteStatus[state="ok"] { color: __green; }
QLabel#cardRemoteStatus[state="ahead"] { color: __amber; }
QLabel#cardRemoteStatus[state="behind"] { color: __cyan; }
QLabel#cardRemoteStatus[state="diverged"] { color: __red; }
QLabel#cardRemoteStatus[state="none"] { color: __text3; }
QLabel#cardRemoteStatus[state="warn"] { color: __amber; }
QLabel#cardRemoteStatus[state="err"] { color: __red; }
QLabel#cardUpdated { font-family: "SF Mono", Menlo, monospace;
    font-size: 11px; color: __text3; }
QLabel[role="cardStatus"] { font-family: "SF Mono", Menlo, monospace; }
QWidget#gitConsole { border-top: 1px solid __border; }
QPushButton#consoleToggle { font-family: "SF Mono", Menlo, monospace;
    color: __text2; }
QLabel#consoleStatus { font-family: "SF Mono", Menlo, monospace;
    color: __text3; }

/* ---------- home landing ---------- */
QWidget#homePage { background: transparent; }
QLabel#homeBrand { color: __text; letter-spacing: -0.5px; }
QLabel#homeTagline { color: __text2; font-size: 14px; }
QLabel#homeSectionTitle { color: __text; font-size: 15px; font-weight: 700; }
QFrame#homeChip {
    background: __surface; border: 1px solid __border2; border-radius: 12px;
    min-width: 110px; }
QLabel#homeChipLabel {
    color: __text3; font-size: 10px; font-weight: 700; letter-spacing: 0.08em; }
QLabel#homeChipValue {
    color: __text; font-size: 20px; font-weight: 700;
    font-family: "SF Mono", Menlo, monospace; }
QPushButton#homeLink {
    text-align: left; padding: 8px 12px; border-radius: 8px;
    background: __surface; border: 1px solid __border; color: __text; }
QPushButton#homeLink:hover {
    background: __surface2; border-color: __border2; }
QPushButton#homeTile {
    background: __surface; border: 1px solid __border; border-radius: 12px; }
QPushButton#homeTile:hover {
    background: __surface2; border-color: __accent; }
QLabel#homeTileTitle { font-weight: 600; color: __text; }
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
