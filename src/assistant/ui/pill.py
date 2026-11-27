"""The action pill: the one small thing beside the orb you can click.

With no answer card, anything that needs a decision shows up here, one at a
time, and only while it is needed:

* an approval ("Run command?  Yes / No / View"), which you can also answer by voice;
* code or notes Claude wants to hand over (Copy / Open in your editor), never read aloud;
* an error that needs reading;
* a one-line box to type instead of talking (Ctrl+Alt+O).

Which one is in front is decided by `assistant.bubbles.Pills` (pure, unit-tested).
Unlike the puffs and the speech bubble this window takes the mouse, so it is
kept small and goes away as soon as it is answered.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ..bubbles import Pill, Pills, extension
from .markdown import split_code_blocks
from .orb import CY, R, Orb
from .sink import ApprovalRequest
from .speech import OVERLAP, side_for

if TYPE_CHECKING:
    from ..orchestrator import Orchestrator

log = logging.getLogger(__name__)

WIDTH = 380
EDGE = {"approval": QColor(255, 174, 66), "code": QColor(255, 174, 66), "error": QColor(255, 42, 42),
        "typing": QColor(255, 90, 61)}

STYLE = """
QLabel { color: #ece6f2; font-size: 10pt; background: transparent; }
QLabel#title { font-size: 12pt; font-weight: 700; }
QLabel#sub { color: #a99bb8; font-size: 9.5pt; }
QLineEdit { color: #ece6f2; background: transparent; border: none; font-size: 11pt; padding: 2px 0; }
QPushButton { background: #3a2c48; color: #fafafa; border: none; border-radius: 10px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background: #4d3b60; }
QPushButton#yes { background: #15803d; }
QPushButton#yes:hover { background: #1a9a4a; }
QPushButton#main { background: #ffae42; color: #1a1108; }
QPushButton#main:hover { background: #ffc06a; }
QPushButton#x { background: transparent; color: #a99bb8; padding: 2px 8px; font-size: 12pt; }
QPushButton#x:hover { color: #ffffff; }
"""


def open_in_editor(path: Path) -> None:
    """VS Code if it is on PATH, else whatever Windows opens the file with, else Notepad."""
    code = shutil.which("code")
    try:
        if code:
            subprocess.Popen([code, str(path)], shell=sys.platform == "win32")  # noqa: S602 - code.cmd needs the shell
        elif sys.platform == "win32":
            try:
                os.startfile(str(path))  # noqa: S606 - the user's own snippet
            except OSError:  # no app registered for this extension
                subprocess.Popen(["notepad.exe", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        log.exception("could not open %s", path)


class ActionPill(QWidget):
    resized = Signal(int)  # height taken beside the orb (0 when hidden), so the speech bubble can sit above

    def __init__(self, orb: Orb, orch: "Orchestrator", snippets_dir: Path, name: str = "Hellbot") -> None:
        super().__init__(None, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet(STYLE)
        self.setFixedWidth(WIDTH)
        self.orb, self.orch, self.snippets_dir = orb, orch, snippets_dir
        self.pills = Pills()
        self._shown: Pill | None = None
        self._side = "left"
        self.last_answer = ""  # for "Copy last answer" in the orb's menu

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 16, 14)
        root.setSpacing(4)
        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("title")
        self.title.setWordWrap(True)
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("x")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self._dismiss)
        head.addWidget(self.title, 1)
        head.addWidget(self.close_btn, 0, Qt.AlignTop)
        root.addLayout(head)
        self.sub = QLabel()
        self.sub.setObjectName("sub")
        self.sub.setWordWrap(True)
        root.addWidget(self.sub)
        self.input = QLineEdit()
        self.input.setPlaceholderText(f"Type to {name}, Enter to send, Esc to close")
        self.input.returnPressed.connect(self._send_typed)
        root.addWidget(self.input)
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        self.primary, self.secondary, self.view_btn = QPushButton(), QPushButton(), QPushButton()
        for btn in (self.primary, self.secondary, self.view_btn):
            btn.setCursor(Qt.PointingHandCursor)
            row.addWidget(btn)
        row.addStretch(1)
        self.buttons = row
        root.addLayout(row)
        self.primary.clicked.connect(self._primary)
        self.secondary.clicked.connect(self._secondary)
        self.view_btn.clicked.connect(self._view)

    # -- events from the core (GUI thread) -----------------------------------------

    def on_status(self, state: str, text: str) -> None:
        if state == "error":
            self.pills.set_error(text)
        elif state in ("listening", "thinking"):
            self.pills.clear_error()
        self.refresh()

    def on_transcript(self, _text: str) -> None:
        self.pills.new_request()
        self.refresh()

    def on_approval(self, req: ApprovalRequest) -> None:
        self.pills.add_approval(req.id, req.title, req.reason, req.body, req.body_kind)
        self.refresh()

    def on_approval_closed(self, req_id: str, _outcome: str) -> None:
        self.pills.close_approval(req_id)
        self.refresh()

    def on_code(self, title: str, code: str, language: str) -> None:
        self.pills.add_code(title, code, language)
        self.refresh()

    def on_assistant(self, text: str) -> None:
        """A reply block: remember it for "Copy last answer"; its code becomes a pill (never spoken)."""
        self.last_answer = text
        for seg in split_code_blocks(text):
            if seg.kind == "code":
                self.pills.add_code("", seg.text, seg.language)
        self.refresh()

    def copy_last_answer(self) -> None:
        if self.last_answer:
            QApplication.clipboard().setText(self.last_answer)

    def toggle_typing(self) -> None:
        self.pills.typing = not self.pills.typing
        self.refresh()

    # -- buttons ----------------------------------------------------------------------

    def _primary(self) -> None:
        pill = self._shown
        if pill is None:
            return
        if pill.kind == "approval":
            self.orch.resolve_approval_threadsafe(pill.id, True)
        elif pill.kind == "code":
            QApplication.clipboard().setText(pill.body)
            self.primary.setText("Copied ✓")
        elif pill.kind == "error":
            self._dismiss()

    def _secondary(self) -> None:
        pill = self._shown
        if pill is None:
            return
        if pill.kind == "approval":
            self.orch.resolve_approval_threadsafe(pill.id, False)
        elif pill.kind == "code":
            self._view()

    def _view(self) -> None:
        """Write what is behind the pill to a file and open it in the editor."""
        pill = self._shown
        if pill is None or not pill.body:
            return
        try:
            self.snippets_dir.mkdir(parents=True, exist_ok=True)
            path = self.snippets_dir / f"hellbot-{pill.kind}-{pill.id}.{extension(pill.language, pill.body_kind)}"
            path.write_text(pill.body, encoding="utf-8")
        except OSError:
            log.exception("could not write the snippet")
            return
        open_in_editor(path)

    def _dismiss(self) -> None:
        pill = self._shown
        if pill is None:
            return
        if pill.kind == "typing":
            self.pills.typing = False
        elif pill.kind == "approval":
            self.orch.resolve_approval_threadsafe(pill.id, False)  # closing it is a "no", never a silent yes
        else:
            self.pills.dismiss(pill.id)
        self.refresh()

    def _send_typed(self) -> None:
        text = self.input.text().strip()
        self.input.clear()
        self.pills.typing = False
        self.refresh()
        if text:
            self.orch.submit_text_threadsafe(text, source="typed")

    def keyPressEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        if e.key() == Qt.Key_Escape:
            self._dismiss()
        else:
            super().keyPressEvent(e)

    # -- showing ----------------------------------------------------------------------

    def refresh(self) -> None:
        pill = self.pills.current()
        if pill is None:
            self._shown = None
            if self.isVisible():
                self.hide()
            self.resized.emit(0)
            return
        if pill != self._shown:
            self._shown = pill
            self._fill(pill)
        self.adjustSize()
        self.reposition()
        if pill.kind == "typing":
            self.setAttribute(Qt.WA_ShowWithoutActivating, False)
            self.show()
            self.activateWindow()  # opened on purpose: ready to type
            self.input.setFocus()
        else:
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)  # never steal focus from your editor
            self.show()
        self.resized.emit(self.height() + 6)
        self.update()

    def _fill(self, pill: Pill) -> None:
        typing = pill.kind == "typing"
        self.title.setVisible(not typing)
        self.sub.setVisible(not typing and bool(pill.sub))
        self.input.setVisible(typing)
        self.title.setText(pill.title)
        self.sub.setText(pill.sub + ("  Or say “apply” or “reject”." if pill.kind == "approval" else ""))
        labels = {"approval": ("Yes, do it", "No", "View changes" if pill.body_kind == "diff" else "View"),
                  "code": ("Copy", "Open in editor", ""), "error": ("OK", "", ""), "typing": ("", "", "")}[pill.kind]
        for btn, label in zip((self.primary, self.secondary, self.view_btn), labels):
            btn.setText(label)
            btn.setVisible(bool(label))
        self.view_btn.setVisible(pill.kind == "approval" and pill.has_view)
        self.primary.setObjectName("yes" if pill.kind == "approval" else "main")
        self.primary.setStyleSheet("")  # re-apply the sheet for the new object name
        self.close_btn.setVisible(pill.kind == "code")  # the typing box closes with Esc or the hotkey
        self.setStyleSheet(STYLE)

    def reposition(self) -> None:
        orb = self.orb.frameGeometry()
        area = (QApplication.screenAt(orb.center()) or QApplication.primaryScreen()).availableGeometry()
        self._side = side_for(orb.left(), orb.right(), self.width(), area.left(), area.right())
        x = orb.left() + OVERLAP - self.width() if self._side == "left" else orb.right() - OVERLAP
        y = orb.top() + int(CY + R) + 6 - self.height()
        x = max(area.left() + 4, min(x, area.right() - self.width() - 3))
        y = max(area.top() + 4, min(y, area.bottom() - self.height() - 3))
        self.move(x, y)

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        kind = self._shown.kind if self._shown else "code"
        radius = min(22.0, box.height() / 2)
        edge = QColor(EDGE[kind])
        for grow, alpha in ((7.0, 14), (4.0, 26)):
            edge.setAlpha(alpha)
            p.setPen(QPen(edge, grow))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box, radius, radius)
        fill = QRadialGradient(box.left() + box.width() * 0.25, box.top() + box.height() * 0.15, box.width() * 1.1)
        fill.setColorAt(0, QColor(74, 58, 92, 250))
        fill.setColorAt(0.7, QColor(36, 26, 48, 250))
        edge.setAlpha(150)
        p.setPen(QPen(edge, 1.3))
        p.setBrush(fill)
        p.drawRoundedRect(box, radius, radius)
        p.end()
