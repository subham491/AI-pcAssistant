"""Tray icon + the answer card that opens next to the orb (PySide6).

The card shows: status, mode badges, what you said, the reply (code blocks
get a Copy button), approval cards with the diff, an activity log, a text box
for typing instead of talking, and a big Stop button. It opens when there is
something to read and folds away again a while after the answer; the orb
(ui/orb.py) stays on screen.

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

from PySide6.QtCore import QObject, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QTextCharFormat,
)
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
from ..steps import Step
from .markdown import split_code_blocks
from .sink import ApprovalRequest

if TYPE_CHECKING:
    from ..orchestrator import Orchestrator

STATE_COLORS = {
    "idle": "#6c5a78",
    "listening": "#ff4d3a",
    "transcribing": "#ffae42",
    "thinking": "#ffae42",
    "speaking": "#ff5a3d",
    "waiting": "#ffae42",
    "error": "#ff2a2a",
}

SHORT, TALL = 440, 620  # card height: answers, and approvals with a diff

# Smoke and embers, to match the orb.
STYLE = """
QLabel, QPlainTextEdit, QLineEdit { color: #ece6f2; font-size: 10pt; }
QLabel#name { color: #ffae42; font-weight: 700; font-size: 8.5pt; letter-spacing: 2px; }
QLabel#transcript { color: #a99bb8; font-style: italic; }
QLabel.badge { border-radius: 8px; padding: 2px 8px; font-weight: 600; font-size: 8.5pt; }
QPlainTextEdit { background: #120c19; border: 1px solid #3a2c48; border-radius: 6px;
                 font-family: Consolas, 'Cascadia Mono', monospace; font-size: 9pt; }
QLineEdit { background: #241a2e; border: 1px solid #3a2c48; border-radius: 6px; padding: 5px; }
QLineEdit:focus { border-color: #ff5a36; }
QPushButton { background: #3a2c48; color: #fafafa; border: none; border-radius: 6px; padding: 5px 12px; }
QPushButton:hover { background: #4d3b60; }
QPushButton:disabled { background: #241a2e; color: #7a6c88; }
QWidget#feed, QWidget#qt_scrollarea_viewport { background: transparent; }
QPushButton#stop { background: #b91c1c; font-weight: 700; }
QPushButton#apply { background: #15803d; font-weight: 700; }
QPushButton#reject { background: #7f1d1d; }
QFrame#card { background: #1a1222; border: 1px solid #ffae42; border-radius: 8px; }
QScrollArea { border: none; background: transparent; }
QToolTip { color: #ece6f2; background: #1a1222; border: 1px solid #3a2c48; }
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
    sig_step_started = Signal(object)
    sig_step_finished = Signal(str, bool, str)
    sig_turn_finished = Signal(bool)
    sig_code = Signal(str, str, str)
    sig_approval = Signal(object)
    sig_approval_closed = Signal(str, str)
    sig_modes = Signal(str, bool)
    sig_level = Signal(float)  # microphone level 0..1 while listening (app.py, not the core)

    def status(self, state: str, text: str) -> None:
        self.sig_status.emit(state, text)

    def transcript(self, text: str) -> None:
        self.sig_transcript.emit(text)

    def assistant_text(self, text: str) -> None:
        self.sig_assistant.emit(text)

    def activity(self, text: str) -> None:
        self.sig_activity.emit(text)

    def step_started(self, step: Step) -> None:
        self.sig_step_started.emit(step)

    def step_finished(self, step_id: str, ok: bool, note: str) -> None:
        self.sig_step_finished.emit(step_id, ok, note)

    def turn_finished(self, ok: bool) -> None:
        self.sig_turn_finished.emit(ok)

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
    def __init__(self, orch: "Orchestrator", sink: QtSink, home: Path, name: str = "Hellbot",
                 hide_after_s: float = 12.0) -> None:
        super().__init__(None, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.orch = orch
        self.home = home
        self.setObjectName("overlay")
        self.setAttribute(Qt.WA_TranslucentBackground, True)  # rounded smoke card, painted below
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(420)
        self.resize(440, SHORT)
        self._drag: QPoint | None = None
        self._cards: dict[str, QFrame] = {}
        self._anchor: QWidget | None = None
        self._moved_by_hand = False
        self._state = "idle"
        self._glow = QColor(STATE_COLORS["idle"])
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(int(hide_after_s * 1000))
        self._hide_timer.timeout.connect(self._auto_hide)
        self._hide_after_s = hide_after_s
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(260)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)

        top = QHBoxLayout()
        name_label = QLabel(name.upper())
        name_label.setObjectName("name")
        top.addWidget(name_label)
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
        sink.sig_transcript.connect(self.on_transcript)
        sink.sig_assistant.connect(self.on_assistant)
        sink.sig_activity.connect(self.activity_view.appendPlainText)
        sink.sig_code.connect(lambda title, code, _lang: (self._add(CodeBlock(code, title)), self.reveal()))
        sink.sig_approval.connect(self.on_approval)
        sink.sig_approval_closed.connect(self.on_approval_closed)
        sink.sig_modes.connect(self.on_modes)

        self._place()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
        fill.setColorAt(0, QColor(58, 44, 72, 244))
        fill.setColorAt(1, QColor(23, 15, 32, 248))
        p.setBrush(fill)
        edge = QColor(self._glow)
        edge.setAlpha(150)
        p.setPen(QPen(edge, 1.4))
        p.drawRoundedRect(rect, 16, 16)
        p.end()

    # -- slots -------------------------------------------------------------

    def on_status(self, state: str, text: str) -> None:
        color = STATE_COLORS.get(state, STATE_COLORS["idle"])
        self._state = state
        self._glow = QColor(color)
        self.dot.setStyleSheet(f"color:{color}; font-size:14pt;")
        self.state_label.setText(text)
        self.update()
        if state in ("waiting", "error"):
            self.reveal()
        if state == "idle":
            self._arm_hide()
        else:
            self._hide_timer.stop()

    def on_transcript(self, text: str) -> None:
        self.transcript_label.setText(f"You: {text}")
        self.reveal()

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
        self.reveal()

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
        self._resize_to(TALL)  # a diff needs room
        self.reveal()

    def on_approval_closed(self, req_id: str, outcome: str) -> None:
        card = self._cards.pop(req_id, None)
        if card is None:
            return
        for btn in card.findChildren(QPushButton):
            if btn.text().startswith(("Apply", "Reject")):
                btn.setEnabled(False)
        card.setStyleSheet("QFrame#card { border-color: %s; }" % ("#15803d" if outcome == "approved" else "#52525b"))
        card.layout().addWidget(QLabel(outcome.capitalize()))
        self._arm_hide()

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

    # -- showing, hiding and following the orb ----------------------------------

    def follow(self, orb: QWidget) -> None:
        """Open next to the orb, and move with it when it is dragged."""
        self._anchor = orb
        self._place()

    def orb_moved(self) -> None:
        self._moved_by_hand = False
        self._place()

    def _place(self) -> None:
        if self._moved_by_hand:
            return
        if self._anchor is None:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 16, screen.bottom() - self.height() - 16)
            return
        orb = self._anchor.frameGeometry()
        screen = (QApplication.screenAt(orb.center()) or QApplication.primaryScreen()).availableGeometry()
        overlap = 34  # the orb window has an empty margin around the smoke
        x = orb.left() - self.width() + overlap
        if x < screen.left() + 8:  # orb is on the left side: open to its right instead
            x = orb.right() - overlap
        y = orb.bottom() - self.height() - 12
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, min(y, screen.bottom() - self.height() - 8))
        self.move(x, y)

    def reveal(self) -> None:
        """Fade in without taking keyboard focus away from what you are doing."""
        self._hide_timer.stop()
        if not self.isVisible():
            self._place()
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self.setWindowOpacity(0.0)
            self.show()
            self._fade.stop()
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()
        self.raise_()
        if self._state == "idle":
            self._arm_hide()

    def _arm_hide(self) -> None:
        if self._hide_after_s > 0 and self.isVisible():
            self._hide_timer.start()

    def _auto_hide(self) -> None:
        busy = (self._state != "idle" or self._cards or self.input.hasFocus() or self.input.text()
                or self.geometry().contains(QCursor.pos()))
        if busy:  # you are reading, typing or deciding: check again later
            self._arm_hide()
        else:
            self.hide()
            self._resize_to(SHORT)

    def _resize_to(self, height: int) -> None:
        if self.height() != height and not self._moved_by_hand:
            self.resize(self.width(), height)
            self._place()

    def toggle(self) -> None:
        if self.isVisible():
            self._hide_timer.stop()
            self.hide()
        else:
            self.reveal()
            self.setAttribute(Qt.WA_ShowWithoutActivating, False)
            self.activateWindow()  # opened on purpose: ready to type
            self.input.setFocus()

    def mousePressEvent(self, e):  # type: ignore[no-untyped-def]  # drag the frameless window
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):  # type: ignore[no-untyped-def]
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self._moved_by_hand = True  # stays where you put it until the orb is moved
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e):  # type: ignore[no-untyped-def]
        self._drag = None


class Tray(QSystemTrayIcon):
    def __init__(self, orch: "Orchestrator", overlay: Overlay, sink: QtSink, home: Path, on_quit,  # type: ignore[no-untyped-def]
                 name: str = "Hellbot", on_talk=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(_dot_icon(STATE_COLORS["idle"]))
        self.setToolTip(name)
        menu = QMenu()
        if on_talk is not None:
            menu.addAction("Talk / finish talking", on_talk)
        menu.addAction("Show / hide chat (type instead)", overlay.toggle)
        menu.addAction("Stop everything", orch.stop_threadsafe)
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
        sink.sig_status.connect(lambda state, _t: self.setIcon(_dot_icon(STATE_COLORS.get(state, STATE_COLORS["idle"]))))
        sink.sig_modes.connect(self._on_modes)
        self._menu = menu  # keep a reference; Qt does not own it

    def popup(self, pos: QPoint) -> None:
        """The same menu, for a right-click on the orb."""
        self._menu.popup(pos)

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
