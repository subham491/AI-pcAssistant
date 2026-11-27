"""The Qt side of the UI interface (QtSink) and the tray icon with its menu.

There is no answer card: everything Hellbot shows lives around the orb
(ui/orb.py, ui/trail.py, ui/speech.py, ui/pill.py). The tray menu is the same
menu a right-click on the orb opens.

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
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..config import EditMode
from ..steps import Step
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
    sig_spoken = Signal(object)  # the sentence being spoken, or None when the voice goes quiet (app.py)

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


class Tray(QSystemTrayIcon):
    def __init__(self, orch: "Orchestrator", sink: QtSink, home: Path, on_quit,  # type: ignore[no-untyped-def]
                 name: str = "Hellbot", on_talk=None, on_type=None, on_copy_answer=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(_dot_icon(STATE_COLORS["idle"]))
        self.setToolTip(name)
        menu = QMenu()
        menu.addAction("■ Stop everything", orch.stop_threadsafe)
        menu.addSeparator()
        if on_talk is not None:
            menu.addAction("Talk / finish talking", on_talk)
        if on_type is not None:
            menu.addAction("Type instead of talking", on_type)
        if on_copy_answer is not None:
            menu.addAction("Copy last answer", on_copy_answer)
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
        menu.addAction("Open activity log", lambda: _open_path(home / "logs" / "assistant.log"))
        menu.addAction("Open audit log", lambda: _open_path(home / "logs" / "audit.jsonl"))
        menu.addAction("Edit settings (config.toml)", lambda: _open_path(home / "config.toml"))
        menu.addSeparator()
        menu.addAction("Quit", on_quit)
        self.setContextMenu(menu)
        if on_type is not None:
            self.activated.connect(lambda reason: on_type() if reason == QSystemTrayIcon.Trigger else None)
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
