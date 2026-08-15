"""Small presentational helpers shared by the module views."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def panel(parent: QWidget | None = None) -> QFrame:
    """A rounded, bordered surface used to group content (see QSS #panel)."""
    frame = QFrame(parent)
    frame.setObjectName("panel")
    return frame


def title_label(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("sectionTitle")
    return label


def styled_label(text: str, kind: str = "muted", parent: QWidget | None = None) -> QLabel:
    label = QLabel(parent)
    label.setObjectName(kind)
    # Set the interaction flags BEFORE the text: on a QLabel with content this
    # forces a full re-layout (~0.8 ms/label — the #1 cost in the Git landing
    # build), but on an empty label it is a no-op (~0.01 ms).
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setText(text)
    return label


def splitter(orientation: Qt.Orientation, parent: QWidget | None = None) -> QSplitter:
    """A themed QSplitter with a sane handle width."""
    s = QSplitter(orientation, parent)
    s.setChildrenCollapsible(False)
    s.setHandleWidth(2)
    return s


def icon_button(
    icons,
    icon: str,
    tip: str = "",
    checkable: bool = False,
    checked: bool = False,
    parent: QWidget | None = None,
) -> QToolButton:
    """A flat toolbar-style icon button (QSS-styled QToolButton)."""
    btn = QToolButton(parent)
    btn.setIcon(icons.get(icon, 16))
    btn.setIconSize(QSize(16, 16))
    if tip:
        btn.setToolTip(tip)
    btn.setCheckable(checkable)
    btn.setChecked(checked)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def search_field(placeholder: str, parent: QWidget | None = None) -> QLineEdit:
    edit = QLineEdit(parent)
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    edit.setProperty("class", "search")
    return edit


def make_chips(icons, labels: Iterable[str]) -> tuple[list[QToolButton], QWidget]:
    """A row of checkable filter chips (QSS #chip). Returns (buttons, container)."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    buttons: list[QToolButton] = []
    for label in labels:
        btn = QToolButton(container)
        btn.setObjectName("chip")
        btn.setText(label)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        buttons.append(btn)
        layout.addWidget(btn)
    layout.addStretch(1)
    return buttons, container


def form_row(label: str, widget: QWidget, hint: str | None = None) -> QWidget:
    """A labeled row: caption above the control, optional hint below."""
    row = QWidget()
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    caption = QLabel(label)
    caption.setObjectName("muted")
    layout.addWidget(caption)
    layout.addWidget(widget)
    if hint:
        hint_label = QLabel(hint)
        hint_label.setObjectName("hint")
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)
    return row


def hrow(spacing: int = 8) -> tuple[QWidget, QHBoxLayout]:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return row, layout


def vrow(spacing: int = 8) -> tuple[QWidget, QVBoxLayout]:
    row = QWidget()
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return row, layout


def combo(items: list[str], index: int = 0, parent: QWidget | None = None) -> QComboBox:
    box = QComboBox(parent)
    box.addItems(items)
    box.setCurrentIndex(index)
    return box


def checkbox(text: str, checked: bool = False, parent: QWidget | None = None) -> QCheckBox:
    box = QCheckBox(text, parent)
    box.setChecked(checked)
    return box


def button(
    text: str,
    kind: str = "default",
    parent: QWidget | None = None,
) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setProperty("class", kind)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def clear_list_widget(list_widget: QListWidget) -> None:
    """Clear a QListWidget, explicitly destroying its item widgets.

    ``QListWidget.clear()`` removes the items but leaves rows attached with
    ``setItemWidget`` behind in the viewport — a view that rebuilds its list
    (search-as-you-type, pin/unpin, …) would otherwise leak one card widget
    per rebuild. Ownership of each widget is handed back and it is freed on
    the next event-loop pass.
    """
    for index in range(list_widget.count()):
        item = list_widget.item(index)
        widget = list_widget.itemWidget(item)
        if widget is not None:
            list_widget.removeItemWidget(item)  # ownership returns to us
            # removeItemWidget alone leaves the row parented to the viewport
            # (still findable) — hide it first (a visible parentless widget
            # would briefly flash as a top-level window), force it out of the
            # tree, then free it on the next event-loop pass.
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
    list_widget.clear()
