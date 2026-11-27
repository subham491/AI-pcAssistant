"""The chimney trail: puffs that rise out of the orb's horns, one per step Claude takes.

The newest puff says what is happening ("reading… todo.md"); older ones shrink
to small ticked chips as they drift higher, so the last few steps stay readable.

This is its own window, not part of the orb, for one reason: it must never get
in the way of the desktop. The whole window ignores the mouse
(`WindowTransparentForInput`, which Qt maps to WS_EX_TRANSPARENT on Windows), so
clicks go straight through to whatever is behind it, while the orb underneath
stays clickable and draggable. It is hidden, with its timer stopped, whenever
there is nothing to show.

What to show is decided by `assistant.steps.Trail` (pure, unit-tested); this
file only places and paints.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from ..steps import Puff, Step, Trail
from .orb import CX, CY, R, SIZE, Orb

WIDTH, HEIGHT = 360, 214
HEAD_GAP = int(CY - 1.45 * R)  # y of the horn tips inside the orb window: the trail ends there
FULL_H, CHIP_H = 34.0, 24.0
PAD = 10.0

AMBER, OK, BAD, GOLD, INK, DIM = "#ffae42", "#7fdc8c", "#ff6a5a", "#ffc65c", "#ece6f2", "#a99bb8"
TAG_COLOURS = {"READ": "#9cc8ff", "FIND": "#d2b4ff", "RUN": AMBER, "EDIT": "#8fe0c0", "WEB": "#9cc8ff",
               "LOOK": "#d2b4ff", "DO": AMBER, "HELP": "#d2b4ff", "PLAN": DIM, "TOOL": DIM}


def puff_parts(puff: Puff) -> list[tuple[str, str, str]]:
    """The pieces of text in a puff, left to right, as (text, colour, style). style: tag | bold | plain."""
    if puff.status == "summary":
        return [("✓", OK, "bold"), (puff.reaction, INK, "bold"), (puff.label, DIM, "plain")]
    if puff.compact:
        return [("✗" if puff.status == "failed" else "✓", BAD if puff.status == "failed" else OK, "bold"),
                (puff.label or "thought", DIM, "plain")]
    if puff.status == "failed":
        return [("✗", BAD, "bold"), (puff.label, INK, "bold"), (puff.note or "failed", BAD, "plain")]
    if puff.status == "done":
        return [("✓", OK, "bold"), (puff.label or "thought", INK, "plain")]
    parts = [(puff.tag, TAG_COLOURS.get(puff.tag, DIM), "tag")] if puff.tag else []
    parts.append((puff.reaction, AMBER, "bold"))
    if puff.label:
        parts.append((puff.label, INK, "bold"))
    return parts


def puff_centre(slot: int, rise: float, t: float, flipped: bool) -> tuple[float, float]:
    """Centre of a puff in window coordinates. Slot 0 sits nearest the head."""
    dist = FULL_H / 2 + 6 if slot == 0 else FULL_H + 6 + 10 + (slot - 1) * (CHIP_H + 6) + CHIP_H / 2
    ease = 1 - (1 - rise) ** 3
    dist = dist * ease - 8 * (1 - ease)  # starts just inside the head
    x = WIDTH / 2 + math.sin(t * 1.2 + slot) * 5 * slot
    return x, (dist if flipped else HEIGHT - dist)


class TrailWindow(QWidget):
    """Shows the steps of the current request above the orb, and tells the orb which eyes to wear."""

    def __init__(self, orb: Orb) -> None:
        super().__init__(None, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(WIDTH, HEIGHT)
        self.orb = orb
        self.trail = Trail()
        self._flipped = False
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._font = QFont(self.font())
        self._font.setPointSizeF(10)
        self._bold = QFont(self._font)
        self._bold.setBold(True)
        self._small = QFont(self._font)
        self._small.setPointSizeF(8.5)
        self._tag = QFont("Consolas")
        self._tag.setStyleHint(QFont.Monospace)
        self._tag.setPointSizeF(7.5)
        self._tag.setBold(True)

    # -- events from the core (GUI thread, via QtSink signals) -----------------

    def on_status(self, state: str, _text: str = "") -> None:
        now = time.monotonic()
        if state == "listening":
            self.trail.clear()  # a new request: nothing of the last one should linger
        elif state == "thinking":
            self.trail.thinking(now)
        elif state in ("speaking", "error"):
            self.trail.speaking(now)
        self._changed()

    def on_step_started(self, step: Step) -> None:
        self.trail.start(step, time.monotonic())
        self._changed()

    def on_step_finished(self, step_id: str, ok: bool, note: str) -> None:
        self.trail.finish(step_id, ok, note, time.monotonic())
        self._changed()

    def on_turn_finished(self, ok: bool) -> None:
        self.trail.turn_done(ok, time.monotonic())
        self.orb.turn_finished(ok)
        self._changed()

    # -- showing and placing -----------------------------------------------------

    def _changed(self) -> None:
        active = self.trail.active(time.monotonic())
        self.orb.set_step(self.trail.current_kind(), trail_visible=active)
        if active and not self._timer.isActive():
            self.reposition()
            self.show()
            self._timer.start()
        self.update()

    def _tick(self) -> None:
        if self.trail.active(time.monotonic()):
            self.update()
            return
        self._timer.stop()
        self.hide()
        self.orb.set_step(None, trail_visible=False)

    def reposition(self) -> None:
        """Sit above the orb's horns; below the orb instead when there is no room above."""
        orb = self.orb.frameGeometry()
        screen = QApplication.screenAt(orb.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        y = orb.top() + HEAD_GAP - HEIGHT
        self._flipped = y < area.top()
        if self._flipped:
            y = orb.top() + int(CY + R) + 4
        x = orb.left() + SIZE // 2 - WIDTH // 2
        x = max(area.left(), min(x, area.right() - WIDTH + 1))
        self.move(x, y)

    # -- painting --------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        now = time.monotonic()
        t = now - self._t0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        # The window may have been nudged sideways to stay on screen: keep puffs over the horns.
        shift = (self.orb.frameGeometry().left() + CX) - (self.x() + WIDTH / 2)
        for puff in sorted(self.trail.puffs(now), key=lambda q: -q.slot):  # newest on top
            self._paint_puff(p, puff, t, shift)
        p.end()

    def _fonts(self, style: str, compact: bool) -> QFont:
        if style == "tag":
            return self._tag
        if compact:
            return self._small
        return self._bold if style == "bold" else self._font

    def _paint_puff(self, p: QPainter, puff: Puff, t: float, shift: float) -> None:
        height = CHIP_H if puff.compact else FULL_H
        pad, gap = (PAD if puff.compact else PAD + 4), 6.0
        max_text = WIDTH - 16 - 2 * pad
        # Measure, and shorten the last piece if the whole line is too long.
        parts = [(text, colour, style, self._fonts(style, puff.compact)) for text, colour, style in puff_parts(puff)]
        widths = [QFontMetricsF(f).horizontalAdvance(text) + (10 if style == "tag" else 0) for text, _c, style, f in parts]
        over = sum(widths) + gap * (len(parts) - 1) - max_text
        if over > 0 and parts:
            text, colour, style, f = parts[-1]
            fm = QFontMetricsF(f)
            text = fm.elidedText(text, Qt.ElideRight, max(24.0, widths[-1] - over))
            parts[-1], widths[-1] = (text, colour, style, f), fm.horizontalAdvance(text)
        width = sum(widths) + gap * (len(parts) - 1) + 2 * pad

        cx, cy = puff_centre(puff.slot, puff.rise, t, self._flipped)
        cx = max(width / 2 + 4, min(WIDTH - width / 2 - 4, cx + shift))
        scale = 0.5 + 0.5 * (1 - (1 - puff.rise) ** 3)
        p.save()
        p.setOpacity(max(0.0, min(1.0, puff.opacity)))
        p.translate(cx, cy)
        p.scale(scale, scale)
        rect = QRectF(-width / 2, -height / 2, width, height)
        fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
        fill.setColorAt(0, QColor(74, 58, 92, 245))
        fill.setColorAt(1, QColor(36, 26, 48, 245))
        edge = {"running": QColor(255, 174, 66, 150), "failed": QColor(255, 42, 42, 190),
                "summary": QColor(255, 198, 92, 170)}.get(puff.status, QColor(255, 255, 255, 34))
        if puff.status in ("running", "failed", "summary") and not puff.compact:  # a soft glow, like the orb's
            glow = QColor(edge)
            for grow, alpha in ((5.0, 18), (3.0, 34)):
                glow.setAlpha(alpha)
                p.setPen(QPen(glow, grow))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect, height / 2, height / 2)
        p.setPen(QPen(edge, 1.3))
        p.setBrush(fill)
        p.drawRoundedRect(rect, height / 2, height / 2)

        x = rect.left() + pad
        for (text, colour, style, font), w in zip(parts, widths):
            p.setFont(font)
            if style == "tag":
                chip = QRectF(x, -8, w, 16)
                back = QColor(colour)
                back.setAlpha(40)
                p.setPen(Qt.NoPen)
                p.setBrush(back)
                p.drawRoundedRect(chip, 4, 4)
                p.setPen(QColor(colour))
                p.drawText(chip, Qt.AlignCenter, text)
            else:
                p.setPen(QColor(colour))
                p.drawText(QRectF(x, -height / 2, w + 2, height), Qt.AlignVCenter | Qt.AlignLeft, text)
            x += w + gap
        p.restore()
