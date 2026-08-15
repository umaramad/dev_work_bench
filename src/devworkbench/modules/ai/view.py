"""AI screen — provider chat with session list and message bubbles.

The panel talks only to ``AIProvider`` via the container's
``services.ai.factory``: every send re-creates the provider from current
settings, so switching providers (or models/keys) in Settings requires zero
UI changes. When no factory is wired (headless previews), the panel stays
presentational with a friendly hint.
"""

from __future__ import annotations

from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from devworkbench.modules.base import Module
from devworkbench.services.ai import ChatMessage
from devworkbench.ui.samples import AI_MODELS, AI_SESSIONS
from devworkbench.ui.widgets.common import button, combo, icon_button, styled_label
from devworkbench.workers.ai_worker import AiChatWorker

_MAX_BUBBLE = 640


def _bubble(kind: str, text: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName({"user": "chatUser", "assistant": "chatAi", "error": "chatError"}.get(kind, "chatAi"))
    frame.setMaximumWidth(_MAX_BUBBLE)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 9, 12, 9)
    label = QLabel()
    # Flags before text — see styled_label: avoids the expensive re-layout.
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setWordWrap(True)
    label.setText(text)
    layout.addWidget(label)
    return frame


def build_view(icons, ctx=None) -> QWidget:
    factory = None
    if ctx is not None and ctx.has("services.ai.factory"):
        factory = ctx.resolve("services.ai.factory")

    root = QWidget()
    layout = QVBoxLayout(root)
    layout.setContentsMargins(10, 10, 10, 8)
    layout.setSpacing(8)

    split = QSplitter(Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)
    split.setHandleWidth(2)

    # ---- sessions rail -------------------------------------------------------------
    rail = QWidget()
    rail_layout = QVBoxLayout(rail)
    rail_layout.setContentsMargins(0, 0, 0, 0)
    rail_layout.setSpacing(8)
    new_chat_button = icon_button(icons, "plus", "New chat")
    rail_layout.addWidget(new_chat_button)
    sessions = QListWidget()
    for title, when in AI_SESSIONS:
        item = QListWidgetItem(f"{title}   ·   {when}")
        item.setToolTip(title)
        sessions.addItem(item)
    sessions.setCurrentRow(0)
    rail_layout.addWidget(sessions, 1)
    rail.setMinimumWidth(230)
    rail.setMaximumWidth(300)
    split.addWidget(rail)

    # ---- chat -----------------------------------------------------------------------
    chat = QWidget()
    chat_layout = QVBoxLayout(chat)
    chat_layout.setContentsMargins(0, 0, 0, 0)
    chat_layout.setSpacing(8)

    header = QWidget()
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.addWidget(styled_label("Chat", "muted"))
    header_layout.addStretch(1)
    provider_label = styled_label("Provider: …", "tiny")
    header_layout.addWidget(provider_label)
    model_combo = combo(AI_MODELS, 0)
    header_layout.addWidget(model_combo)
    header_layout.addWidget(icon_button(icons, "dots", "Session options"))
    chat_layout.addWidget(header)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll_content = QWidget()
    scroll_layout = QVBoxLayout(scroll_content)
    scroll_layout.setContentsMargins(4, 4, 12, 4)
    scroll_layout.setSpacing(12)
    scroll.setWidget(scroll_content)
    chat_layout.addWidget(scroll, 1)

    input_bar = QFrame()
    input_bar.setObjectName("panel")
    input_layout = QHBoxLayout(input_bar)
    input_layout.setContentsMargins(8, 8, 8, 8)
    input_layout.setSpacing(8)
    input_edit = QPlainTextEdit()
    input_edit.setPlaceholderText("Ask anything…  (⏎ to send)")
    input_edit.setFixedHeight(64)
    input_layout.addWidget(input_edit, 1)
    input_buttons = QVBoxLayout()
    input_buttons.setSpacing(6)
    send_button = button("Send", "primary")
    stop_button = button("Stop", "ghost")
    stop_button.setEnabled(False)
    input_buttons.addWidget(send_button)
    input_buttons.addWidget(stop_button)
    input_layout.addLayout(input_buttons)
    chat_layout.addWidget(input_bar)

    split.addWidget(chat)
    split.setSizes([260, 700])
    layout.addWidget(split, 1)

    # ---- live provider wiring ---------------------------------------------------------
    conversation: list[ChatMessage] = []
    bubble_widgets: list[QWidget] = []
    welcome_added = False
    # Workers must be retained until their signals deliver: a QRunnable held
    # only by the thread pool is destroyed right after run(), and the queued
    # finished/error event would target a dead signals object.
    pending_workers: list = []

    def scroll_to_bottom() -> None:
        bar = scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def add_bubble(kind: str, text: str) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        bubble = _bubble(kind, text)
        if kind == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch(1)
        scroll_layout.addWidget(row)
        bubble_widgets.append(row)
        scroll_to_bottom()

    def show_welcome() -> None:
        nonlocal welcome_added
        if welcome_added or conversation or bubble_widgets:
            return
        welcome_added = True
        add_bubble(
            "assistant",
            "Hi — I'm wired to the configured AI provider. Ask me anything — this panel "
            "follows the provider selected in Settings → AI.",
        )

    def new_chat() -> None:
        conversation.clear()
        for widget in bubble_widgets:
            widget.deleteLater()
        bubble_widgets.clear()
        show_welcome()

    def refresh_provider_status() -> None:
        if factory is None:
            provider_label.setText("Provider: not configured — open Settings → AI")
            return
        try:
            provider = factory.create()
            provider_label.setText(f"Provider: {provider.display_name}")
        except Exception as exc:  # noqa: BLE001 — surface configuration problems inline
            provider_label.setText(f"Provider: {exc}")

    def send() -> None:
        if not send_button.isEnabled():
            return  # a request is already in flight (Send is disabled)
        text = input_edit.toPlainText().strip()
        if not text:
            return
        input_edit.clear()
        conversation.append(ChatMessage("user", text))
        add_bubble("user", text)

        if factory is None:
            add_bubble("error", "No AI provider is configured. Open Settings → AI, pick a provider, add its key, then try again.")
            return
        try:
            provider = factory.create()
        except Exception as exc:  # noqa: BLE001
            add_bubble("error", f"Provider unavailable: {exc}")
            return

        send_button.setEnabled(False)
        stop_button.setEnabled(True)
        provider_label.setText(f"{provider.display_name} · generating…")
        worker = AiChatWorker(provider, list(conversation), model=model_combo.currentText())

        def attach(current: AiChatWorker) -> None:
            pending_workers.append(current)

            def done(result, _worker=current) -> None:
                _on_finished(result)
                if _worker in pending_workers:
                    pending_workers.remove(_worker)

            def failed(exc, _worker=current) -> None:
                _on_error(exc)
                if _worker in pending_workers:
                    pending_workers.remove(_worker)

            current.signals.finished.connect(done)
            current.signals.error.connect(failed)

        attach(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_finished(result) -> None:
        text = result.text or "*(empty response)*"
        conversation.append(ChatMessage("assistant", text))
        add_bubble("assistant", text)
        provider_label.setText(
            f"{provider_label.text().replace(' · generating…', '')} · {result.model or model_combo.currentText()}"
            f" · {result.usage.total_tokens} tokens"
        )
        send_button.setEnabled(True)
        stop_button.setEnabled(False)

    def _on_error(exc) -> None:
        add_bubble("error", f"{type(exc).__name__}: {exc}")
        provider_label.setText("Request failed")
        send_button.setEnabled(True)
        stop_button.setEnabled(False)

    def stop() -> None:
        for worker in pending_workers:
            worker.cancel()
        pending_workers.clear()
        send_button.setEnabled(True)
        stop_button.setEnabled(False)
        provider_label.setText("Stopped")

    send_button.clicked.connect(send)
    stop_button.clicked.connect(stop)
    new_chat_button.clicked.connect(new_chat)
    QShortcut(QKeySequence(Qt.Key.Key_Return), input_edit, send)
    QShortcut(QKeySequence(Qt.Key.Key_Enter), input_edit, send)
    refresh_provider_status()
    show_welcome()
    return root


ai_module = Module(
    id="ai",
    title="AI",
    icon="ai",
    build=build_view,
    navigator=(
        ("Sessions", ("Refactor config loader", "Explain WAL pragmas", "Review migration 0004")),
        ("Models", ("gpt-4.1", "claude-sonnet-4", "ollama/qwen3")),
    ),
    details=(
        ("Session", "Refactor config loader"),
        ("Model", "gpt-4.1"),
        ("Messages", "4"),
        ("Tokens", "2,411"),
    ),
    status="AI · provider from Settings · session: Refactor config loader",
)
