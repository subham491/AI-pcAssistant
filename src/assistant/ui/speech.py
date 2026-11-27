"""Smoke speech: what you said and what Hellbot is saying, as smoke beside the orb.

There is no answer card. Your words drift in as a small wisp once they are
transcribed; when Hellbot answers, the sentence it is speaking sits in one
smoke bubble next to its face and changes with the voice. Both fade on their
own.

Like the puff trail this window ignores the mouse entirely
(`WindowTransparentForInput`), so it can never block what is behind it. What to
show is decided by `assistant.bubbles.Captions` (pure, unit-tested).
"""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QWidget

from ..bubbles import Captions, CaptionView
from .orb import CY, SIZE, Orb

MAX_WIDTH = 440
MARGIN = 14  # room for the glow around the bubble
OVERLAP = 30  # the orb window has an empty margin around the smoke
YOU_PAD, SAY_PAD = (13.0, 7.0), (18.0, 13.0)
GAP = 6.0
WRAP = int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop)


def side_for(orb_left: int, orb_right: int, width: int, screen_left: int, screen_right: int) -> str:
    """Which side of the orb a window of this width opens on: left unless there is no room."""
    if orb_left + OVERLAP - width >= screen_left:
        return "left"
    return "right" if orb_right - OVERLAP + width <= screen_right else "left"


class SpeechBubble(QWidget):
    def __init__(self, orb: Orb) -> None:
        super().__init__(None, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.orb = orb
        self.captions = Captions()
        self.lift = 0  # pixels to move up by, when the action pill is showing underneath
        self._side = "left"
        self._shown = ("", "")
        self._you_rect = self._say_rect = QRectF()
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._you_font = QFont(self.font())
        self._you_font.setPointSizeF(10)
        self._you_font.setItalic(True)
        self._say_font = QFont(self.font())
        self._say_font.setPointSizeF(12.5)

    # -- events (GUI thread) ---------------------------------------------------

    def on_status(self, state: str, _text: str = "") -> None:
        if state == "listening":
            self.captions.clear()  # a new request: nothing of the last one should linger
        elif state in ("thinking", "waiting"):
            self.captions.working()
        elif state == "idle":
            self.captions.settle(time.monotonic())
        self._changed()

    def on_transcript(self, text: str) -> None:
        self.captions.heard(text, time.monotonic())
        self._changed()

    def on_spoken(self, sentence: object) -> None:
        self.captions.spoken(sentence if isinstance(sentence, str) else None, time.monotonic())
        self._changed()

    def set_lift(self, lift: int) -> None:
        if lift != self.lift:
            self.lift = lift
            if self.isVisible():
                self.reposition()

    # -- layout ------------------------------------------------------------------

    def _changed(self) -> None:
        view = self.captions.view(time.monotonic())
        if not view.visible:
            self._tick()
            return
        if (view.you, view.say) != self._shown:
            self._layout(view)
        if not self._timer.isActive():
            self.show()
            self._timer.start()
        self.update()

    def _tick(self) -> None:
        view = self.captions.view(time.monotonic())
        if view.visible:
            self.update()
            return
        self._timer.stop()
        self._shown = ("", "")
        self.hide()

    def _layout(self, view: CaptionView) -> None:
        self._shown = (view.you, view.say)
        y = float(MARGIN)
        width = 0.0
        self._you_rect = self._say_rect = QRectF()
        for text, font, pad, name in ((view.you, self._you_font, YOU_PAD, "_you_rect"),
                                      (view.say, self._say_font, SAY_PAD, "_say_rect")):
            if not text:
                continue
            fm = QFontMetricsF(font)
            box = fm.boundingRect(QRectF(0, 0, MAX_WIDTH - 2 * pad[0], 2000), WRAP, text)
            rect = QRectF(0, y, box.width() + 2 * pad[0] + 2, box.height() + 2 * pad[1])
            setattr(self, name, rect)
            y = rect.bottom() + GAP
            width = max(width, rect.width())
        self.setFixedSize(int(width + 2 * MARGIN) + 1, int(y - GAP + MARGIN) + 1)
        self.reposition()

    def reposition(self) -> None:
        """Beside the orb's face; on its other side when there is no room."""
        orb = self.orb.frameGeometry()
        area = (QApplication.screenAt(orb.center()) or QApplication.primaryScreen()).availableGeometry()
        self._side = side_for(orb.left(), orb.right(), self.width(), area.left(), area.right())
        x = orb.left() + OVERLAP - self.width() if self._side == "left" else orb.right() - OVERLAP
        y = orb.top() + int(CY) + 22 - self.height() + MARGIN - self.lift  # the bubble's foot sits by the mouth
        x = max(area.left(), min(x, area.right() - self.width() + 1))
        y = max(area.top(), min(y, area.bottom() - self.height() + 1))
        self.move(x, y)
        self.update()

    # -- painting ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        view = self.captions.view(time.monotonic())
        you, say = self._shown
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setOpacity(max(0.0, min(1.0, view.opacity)))
        inner = self.width() - 2 * MARGIN
        for text, rect, font, pad, kind in ((you, self._you_rect, self._you_font, YOU_PAD, "you"),
                                            (say, self._say_rect, self._say_font, SAY_PAD, "say")):
            if not text or rect.isEmpty():
                continue
            # Hug the orb: right-aligned when the orb is to the right, left-aligned otherwise.
            x = MARGIN + (inner - rect.width() if self._side == "left" else 0)
            box = QRectF(x, rect.top(), rect.width(), rect.height())
            self._paint_smoke(p, box, kind)
            p.setFont(font)
            p.setPen(QColor("#a99bb8") if kind == "you" else QColor("#ffffff"))
            p.drawText(box.adjusted(pad[0], pad[1], -pad[0], -pad[1]), WRAP, text)
        p.end()

    def _paint_smoke(self, p: QPainter, box: QRectF, kind: str) -> None:
        radius = min(22.0 if kind == "say" else 16.0, box.height() / 2)
        if kind == "say":
            glow = QColor(255, 90, 61)
            for grow, alpha in ((7.0, 14), (4.0, 26)):
                glow.setAlpha(alpha)
                p.setPen(QPen(glow, grow))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(box, radius, radius)
            fill = QRadialGradient(box.left() + box.width() * 0.25, box.top() + box.height() * 0.15, box.width() * 1.1)
            fill.setColorAt(0, QColor(74, 58, 92, 248))
            fill.setColorAt(0.7, QColor(36, 26, 48, 248))
            p.setBrush(fill)
            p.setPen(QPen(QColor(255, 90, 61, 120), 1.2))
        else:
            p.setBrush(QColor(36, 26, 48, 215))
            p.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        p.drawRoundedRect(box, radius, radius)
        # The corner nearest the orb is squarer, like a speech tail.
        tail = 7.0
        corner = QRectF(box.right() - tail * 2 if self._side == "left" else box.left(), box.bottom() - tail * 2, tail * 2, tail * 2)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(corner.adjusted(1, 1, -1, -1), 3, 3)


__all__ = ["SpeechBubble", "side_for", "SIZE"]
