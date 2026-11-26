"""Tray icon + always-on-top overlay (PySide6).

The overlay shows: status, mode badges, what you said, Claude's reply (code
blocks get a Copy button), approval cards with the diff, an activity log, a
text box for typing instead of talking, and a big Stop button.

Threading: the core runs on an asyncio thread and calls the UiSink methods
from there. QtSink turns each call into a Qt signal, which Qt delivers on the
GUI thread (queued connection), so widgets are only touched on that thread.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..config import EditMode
from .markdown import split_code_blocks
from .sink import ApprovalRequest

if TYPE_CHECKING:
    from ..orchestrator import Orchestrator

STATE_COLORS = {
    "idle": "#6b7280",
    "listening": "#16a34a",
    "transcribing": "#0891b2",
    "thinking": "#2563eb",
    "speaking": "#7c3aed",
    "waiting": "#d97706",
    "error": "#dc2626",
}

STYLE = """
QWidget#overlay { background: rgba(24, 24, 27, 238); border-radius: 12px; }
QLabel, QPlainTextEdit, QLineEdit { color: #e4e4e7; font-size: 10pt; }
QLabel#transcript { color: #a1a1aa; font-style: italic; }
QLabel.badge { border-radius: 8px; padding: 2px 8px; font-weight: 600; font-size: 8.5pt; }
QPlainTextEdit { background: #09090b; border: 1px solid #3f3f46; border-radius: 6px;
                 font-family: Consolas, 'Cascadia Mono', monospace; font-size: 9pt; }
QLineEdit { background: #27272a; border: 1px solid #3f3f46; border-radius: 6px; padding: 5px; }
QPushButton { background: #3f3f46; color: #fafafa; border: none; border-radius: 6px; padding: 5px 12px; }
QPushButton:hover { background: #52525b; }
QPushButton:disabled { background: #27272a; color: #71717a; }
QWidget#feed, QWidget#qt_scrollarea_viewport { background: transparent; }
QPushButton#stop { background: #b91c1c; font-weight: 700; }
QPushButton#apply { background: #15803d; font-weight: 700; }
QPushButton#reject { background: #7f1d1d; }
QFrame#card { background: #18181b; border: 1px solid #d97706; border-radius: 8px; }
QScrollArea { border: none; background: transparent; }
"""


def _dot_icon(color: str) -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(6, 6, 52, 52)
    p.end()
    return QIcon(pix)


class QtSink(QObject):
    """UiSink implementation: every call becomes a queued signal."""

    sig_status = Signal(str, str)
    sig_transcript = Signal(str)
    sig_assistant = Signal(str)
    sig_activity = Signal(str)
    sig_code = Signal(str, str, str)
    sig_approval = Signal(object)
    sig_approval_closed = Signal(str, str)
    sig_modes = Signal(str, bool)

    def status(self, state: str, text: str) -> None:
        self.sig_status.emit(state, text)

    def transcript(self, text: str) -> None:
        self.sig_transcript.emit(text)

    def assistant_text(self, text: str) -> None:
        self.sig_assistant.emit(text)

    def activity(self, text: str) -> None:
        self.sig_activity.emit(text)

    def show_code(self, title: str, code: str, language: str) -> None:
        self.sig_code.emit(title, code, language)

    def request_approval(self, req: ApprovalRequest) -> None:
        self.sig_approval.emit(req)

    def approval_closed(self, req_id: str, outcome: str) -> None:
        self.sig_approval_closed.emit(req_id, outcome)

    def modes(self, edit_mode: str, remote: bool) -> None:
        self.sig_modes.emit(edit_mode, remote)


class CodeBlock(QFrame):
    def __init__(self, code: str, title: str = "", diff: bool = False) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        if title:
            t = QLabel(title)
            t.setWordWrap(True)
            head.addWidget(t, 1)
        else:
            head.addStretch(1)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(code), copy.setText("Copied ✓")))
        head.addWidget(copy)
        lay.addLayout(head)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        view.setPlainText(code)
        if diff:
            self._color_diff(view)
        lines = min(max(code.count("\n") + 1, 3), 18)
        view.setFixedHeight(int(lines * view.fontMetrics().lineSpacing() + 14))
        lay.addWidget(view)

    @staticmethod
    def _color_diff(view: QPlainTextEdit) -> None:
        doc = view.document()
        for i in range(doc.blockCount()):
            block = doc.findBlockByNumber(i)
            text = block.text()
            color = None
            if text.startswith("+") and not text.startswith("+++"):
                color = "#4ade80"
            elif text.startswith("-") and not text.startswith("---"):
                color = "#f87171"
            elif text.startswith("@@"):
                color = "#60a5fa"
            if color:
                cursor = view.textCursor()
                cursor.setPosition(block.position())
                cursor.setPosition(block.position() + block.length() - 1, cursor.MoveMode.KeepAnchor)
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(color))
                cursor.setCharFormat(fmt)


class Overlay(QWidget):
    def __init__(self, orch: "Orchestrator", sink: QtSink, home: Path) -> None:
        super().__init__(None, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.orch = orch
        self.home = home
        self.setObjectName("overlay")
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(460)
        self.resize(480, 560)
        self._drag: QPoint | None = None
        self._cards: dict[str, QFrame] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)

        top = QHBoxLayout()
        self.dot = QLabel("●")
        self.state_label = QLabel("Starting…")
        self.state_label.setFont(QFont(self.state_label.font().family(), 10, QFont.Bold))
        self.mode_badge = QLabel()
        self.mode_badge.setProperty("class", "badge")
        self.remote_badge = QLabel("REMOTE")
        self.remote_badge.setProperty("class", "badge")
        self.remote_badge.setStyleSheet("background:#b45309;color:white;border-radius:8px;padding:2px 8px;")
        self.remote_badge.hide()
        hide = QPushButton("–")
        hide.setFixedWidth(28)
        hide.clicked.connect(self.hide)
        top.addWidget(self.dot)
        top.addWidget(self.state_label, 1)
        top.addWidget(self.remote_badge)
        top.addWidget(self.mode_badge)
        top.addWidget(hide)
        root.addLayout(top)

        self.transcript_label = QLabel("")
        self.transcript_label.setObjectName("transcript")
        self.transcript_label.setWordWrap(True)
        root.addWidget(self.transcript_label)

        self.feed = QVBoxLayout()
        self.feed.addStretch(1)
        feed_host = QWidget()
        feed_host.setObjectName("feed")
        feed_host.setLayout(self.feed)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(feed_host)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.scroll, 1)
        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(lambda _lo, hi: bar.setValue(hi))  # follow new content

        self.activity_view = QPlainTextEdit()
        self.activity_view.setReadOnly(True)
        self.activity_view.setMaximumBlockCount(300)
        self.activity_view.setFixedHeight(80)
        root.addWidget(self.activity_view)

        bottom = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type instead of talking, press Enter…")
        self.input.returnPressed.connect(self._send_typed)
        stop = QPushButton("■ Stop")
        stop.setObjectName("stop")
        stop.clicked.connect(orch.stop_threadsafe)
        bottom.addWidget(self.input, 1)
        bottom.addWidget(stop)
        root.addLayout(bottom)

        sink.sig_status.connect(self.on_status)
        sink.sig_transcript.connect(lambda t: self.transcript_label.setText(f"You: {t}"))
        sink.sig_assistant.connect(self.on_assistant)
        sink.sig_activity.connect(self.activity_view.appendPlainText)
        sink.sig_code.connect(lambda title, code, _lang: self._add(CodeBlock(code, title)))
        sink.sig_approval.connect(self.on_approval)
        sink.sig_approval_closed.connect(self.on_approval_closed)
        sink.sig_modes.connect(self.on_modes)

        self._place_bottom_right()

    # -- slots -------------------------------------------------------------

    def on_status(self, state: str, text: str) -> None:
        color = STATE_COLORS.get(state, "#6b7280")
        self.dot.setStyleSheet(f"color:{color}; font-size:14pt;")
        self.state_label.setText(text)
        if state in ("listening", "waiting") and not self.isVisible():
            self.show()

    def on_assistant(self, text: str) -> None:
        for seg in split_code_blocks(text):
            if seg.kind == "code":
                self._add(CodeBlock(seg.text, seg.language))
            else:
                label = QLabel()
                label.setTextFormat(Qt.MarkdownText)
                label.setText(seg.text)
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
                label.setOpenExternalLinks(True)
                self._add(label)

    def on_approval(self, req: ApprovalRequest) -> None:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        title = QLabel(f"<b>Approve?</b> {req.title}")
        title.setWordWrap(True)
        reason = QLabel(f"Tier T{req.tier}: {req.reason}")
        reason.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(reason)
        lay.addWidget(CodeBlock(req.body, diff=(req.body_kind == "diff")))
        row = QHBoxLayout()
        apply_btn = QPushButton("Apply  (say “apply”)")
        apply_btn.setObjectName("apply")
        reject_btn = QPushButton("Reject")
        reject_btn.setObjectName("reject")
        apply_btn.clicked.connect(lambda: self.orch.resolve_approval_threadsafe(req.id, True))
        reject_btn.clicked.connect(lambda: self.orch.resolve_approval_threadsafe(req.id, False))
        row.addWidget(apply_btn)
        row.addWidget(reject_btn)
        lay.addLayout(row)
        self._cards[req.id] = card
        self._add(card)
        self.show()
        self.raise_()

    def on_approval_closed(self, req_id: str, outcome: str) -> None:
        card = self._cards.pop(req_id, None)
        if card is None:
            return
        for btn in card.findChildren(QPushButton):
            if btn.text().startswith(("Apply", "Reject")):
                btn.setEnabled(False)
        card.setStyleSheet("QFrame#card { border-color: %s; }" % ("#15803d" if outcome == "approved" else "#52525b"))
        card.layout().addWidget(QLabel(outcome.capitalize()))

    def on_modes(self, edit_mode: str, remote: bool) -> None:
        names = {"suggest": "SUGGEST ONLY", "review": "REVIEW EDITS", "auto": "AUTO-APPLY"}
        colors = {"suggest": "#0e7490", "review": "#4338ca", "auto": "#be123c"}
        self.mode_badge.setText(names.get(edit_mode, edit_mode))
        self.mode_badge.setStyleSheet(
            f"background:{colors.get(edit_mode, '#3f3f46')};color:white;border-radius:8px;padding:2px 8px;")
        self.remote_badge.setVisible(remote)

    # -- helpers -----------------------------------------------------------

    def _add(self, widget: QWidget) -> None:
        self.feed.insertWidget(self.feed.count() - 1, widget)
        # Keep memory bounded on all-day sessions.
        while self.feed.count() > 80:
            item = self.feed.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _send_typed(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.orch.submit_text_threadsafe(text, source="typed")

    def _place_bottom_right(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 16, screen.bottom() - self.height() - 16)

    def toggle(self) -> None:
        self.setVisible(not self.isVisible())

    def mousePressEvent(self, e):  # type: ignore[no-untyped-def]  # drag the frameless window
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):  # type: ignore[no-untyped-def]
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e):  # type: ignore[no-untyped-def]
        self._drag = None


class Tray(QSystemTrayIcon):
    def __init__(self, orch: "Orchestrator", overlay: Overlay, sink: QtSink, home: Path, on_quit) -> None:  # type: ignore[no-untyped-def]
        super().__init__(_dot_icon(STATE_COLORS["idle"]))
        self.setToolTip("PC co-worker")
        menu = QMenu()
        menu.addAction("Show / hide overlay", overlay.toggle)
        menu.addSeparator()
        group = QActionGroup(menu)
        self._mode_actions: dict[str, QAction] = {}
        for mode, label in ((EditMode.SUGGEST, "Suggest only"), (EditMode.REVIEW, "Review each edit"),
                            (EditMode.AUTO, "Auto-apply (git-tracked)")):
            act = QAction(label, menu, checkable=True)
            act.triggered.connect(lambda _c=False, m=mode: orch.set_edit_mode_threadsafe(m))
            group.addAction(act)
            menu.addAction(act)
            self._mode_actions[mode.value] = act
        self.remote_action = QAction("Remote mode (look + suggest only)", menu, checkable=True)
        self.remote_action.triggered.connect(orch.set_remote_threadsafe)
        menu.addAction(self.remote_action)
        menu.addSeparator()
        menu.addAction("Open home folder", lambda: _open_path(home))
        menu.addAction("Open audit log", lambda: _open_path(home / "logs" / "audit.jsonl"))
        menu.addAction("Edit settings (config.toml)", lambda: _open_path(home / "config.toml"))
        menu.addSeparator()
        menu.addAction("Quit", on_quit)
        self.setContextMenu(menu)
        self.activated.connect(lambda reason: overlay.toggle() if reason == QSystemTrayIcon.Trigger else None)
        sink.sig_status.connect(lambda state, _t: self.setIcon(_dot_icon(STATE_COLORS.get(state, "#6b7280"))))
        sink.sig_modes.connect(self._on_modes)
        self._menu = menu  # keep a reference; Qt does not own it

    def _on_modes(self, edit_mode: str, remote: bool) -> None:
        if edit_mode in self._mode_actions:
            self._mode_actions[edit_mode].setChecked(True)
        self.remote_action.setChecked(remote)


def _open_path(path: Path) -> None:
    if not path.exists():
        return
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - opening the user's own file
    else:
        subprocess.Popen(["xdg-open", str(path)])
