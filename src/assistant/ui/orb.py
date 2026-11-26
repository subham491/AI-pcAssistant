"""The Hellbot orb: a small cloud of dark smoke with ember eyes that floats on the desktop.

Asleep (eyes closed) means the microphone is off. Click it, or press the talk
hotkey, and it wakes and listens; click again to finish. It billows while
thinking, and a puff drifts off towards the answer card when it replies.

How it is drawn: the smoke is a "metaball" field. Every blob (head, horns,
puffs) adds a soft bump to each pixel and the pixel is smoke where the sum passes 1,
so blobs that come close melt into each other like goo. That is a few numpy
operations on a ~200x200 image per frame, so there is no shader, no QML and
nothing to compile. `render_smoke` is pure numpy and unit-tested; the widget
only places blobs and paints the eyes on top.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

SIZE = 168  # logical pixels; the orb itself is ~64 across, the rest is room for smoke and glow
CX, CY, R = SIZE / 2, SIZE * 0.60, 30.0
DRAG_START_PX = 6

# App status -> how the orb looks.
LOOKS = {
    "idle": "asleep",
    "listening": "listening",
    "transcribing": "thinking",
    "thinking": "thinking",
    "speaking": "answer",
    "waiting": "answer",
    "error": "error",
}

REACH = 1.9  # a blob influences the field out to this many radii
ISO = (1 - 1 / REACH**2) ** 3  # so that a lone blob's edge sits exactly at its radius

BODY_TOP = np.array([86.0, 70.0, 104.0])
BODY_BOTTOM = np.array([26.0, 18.0, 36.0])


@dataclass(frozen=True)
class Look:
    glow: tuple[int, int, int]
    glow_strength: float
    eye: str  # eye colour
    fps: int


LOOK = {
    "asleep": Look((150, 120, 190), 0.16, "#6c5a78", 15),
    "listening": Look((255, 77, 58), 0.55, "#ff4d3a", 30),
    "thinking": Look((255, 174, 66), 0.45, "#ffae42", 30),
    "answer": Look((255, 77, 58), 0.34, "#ff5a3d", 30),
    "error": Look((255, 40, 40), 0.5, "#ff2a2a", 15),
}


def render_smoke(size: int, scale: float, blobs: list[tuple[float, float, float]],
                 glow: tuple[int, int, int], glow_strength: float, heat: float = 0.0) -> np.ndarray:
    """Return a (size*scale, size*scale, 4) uint8 image, premultiplied RGBA.

    blobs are (x, y, radius) in logical pixels. `heat` (0..1) lights the middle
    of the smoke in the glow colour, used for the voice level while listening.
    """
    n = max(1, round(size * scale))
    axis = (np.arange(n, dtype=np.float32) + 0.5) / scale
    x, y = axis[None, :], axis[:, None]
    field = np.zeros((n, n), dtype=np.float32)
    for bx, by, br in blobs:
        if br > 0:
            # Each blob only reaches REACH radii, so far-apart blobs stay separate shapes.
            d2 = ((x - bx) ** 2 + (y - by) ** 2) / (REACH * br) ** 2
            field += np.clip(1 - d2, 0, None) ** 3 / ISO

    edge = 0.07 + 0.05 / scale  # soft edge = antialiasing
    body = np.clip((field - (1 - edge)) / (2 * edge), 0, 1)
    halo = np.clip((field - 0.15) / 0.85, 0, 1) ** 1.6 * glow_strength * (1 - body)

    shade = np.clip(((x + y) - (CX + CY - 2 * R)) / (4 * R), 0, 1)[..., None]  # top-left light
    rgb = BODY_TOP * (1 - shade) + BODY_BOTTOM * shade
    rim = (np.clip((1.5 - field) / 0.5, 0, 1) * body)[..., None] * (1 - shade)
    rgb = rgb + 40 * rim
    glow_rgb = np.array(glow, dtype=np.float32)
    if heat > 0 and blobs:
        hx, hy, hr = blobs[0]  # the head: lit from inside, below the eyes
        d = np.sqrt((x - hx) ** 2 + (y - (hy + 0.25 * hr)) ** 2) / hr
        core = (np.clip(1 - d, 0, 1) ** 1.5 * heat * 0.6)[..., None]
        rgb = rgb * (1 - core) + glow_rgb * core

    out = np.empty((n, n, 4), dtype=np.float32)
    out[..., :3] = rgb * body[..., None] + glow_rgb * halo[..., None]
    out[..., 3] = (body + halo) * 255
    return np.clip(out, 0, 255).astype(np.uint8)


def blobs_for(look: str, t: float, level: float, since: float) -> list[tuple[float, float, float]]:
    """Where the smoke is at time t (seconds). `since` = seconds in the current look."""
    breathe = 1 + 0.035 * math.sin(t * 1.5)
    grow = 1.0
    if look == "listening":
        grow, breathe = 1.10 + 0.22 * level, 1.0
    elif look == "thinking":
        grow = 1.04
    r = R * grow * breathe
    lift = 4 * level if look == "listening" else 0.0
    blobs = [(CX, CY, r)]
    # Horns: a base blob and a smaller tip each, leaning outwards.
    for side in (-1, 1):
        blobs.append((CX + side * 0.56 * r, CY - 0.86 * r - lift * 0.4, 0.27 * r))
        blobs.append((CX + side * 0.80 * r, CY - 1.22 * r - lift, 0.16 * r))

    if look in ("asleep", "thinking", "error"):
        count, period, size, reach = (3, 3.4, 7.5, 44.0) if look != "thinking" else (5, 1.5, 10.0, 56.0)
        for i in range(count):
            phase = ((t / period) + i / count) % 1.0
            sway = math.sin(t * 1.3 + i * 2.1) * 9 * phase
            drift = (i - (count - 1) / 2) * 8 * phase
            blobs.append((CX + drift + sway, CY - 0.75 * r - reach * phase, size * (1 - phase) ** 0.7))
    elif look == "answer" and since < 0.9:
        # One puff leaves towards the answer card (drawn to the left of the orb).
        p = since / 0.9
        ease = p * p * (3 - 2 * p)
        blobs.append((CX - (R + 60) * ease, CY - 6 * math.sin(p * math.pi), 13 * (1 - 0.55 * p)))
    return blobs


class Orb(QWidget):
    """Frameless always-on-top smoke orb. Emits `clicked` for click-to-talk."""

    clicked = Signal()
    menu_requested = Signal(QPoint)
    moved = Signal()

    def __init__(self, name: str, talk_hint: str, state_file: Path | None = None) -> None:
        super().__init__(None, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)  # never steal focus from your editor
        self.setFixedSize(SIZE, SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.name, self.talk_hint = name, talk_hint
        self._state_file = state_file
        self.look = "asleep"
        self.level = 0.0
        self._shown_level = 0.0
        self._look_since = time.monotonic()
        self._t0 = time.monotonic()
        self._press: QPoint | None = None
        self._dragging = False
        self._image: QImage | None = None
        self._pixels: np.ndarray | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._apply_look()

    # -- state ---------------------------------------------------------------

    def set_status(self, state: str, _text: str = "") -> None:
        look = LOOKS.get(state, "asleep")
        if look != self.look:
            self.look, self._look_since = look, time.monotonic()
            self._apply_look()
        self.update()

    def set_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, level))

    def _apply_look(self) -> None:
        self._timer.start(1000 // LOOK[self.look].fps)
        tips = {
            "asleep": f"{self.name} is asleep (mic off). Click or press {self.talk_hint} to talk.",
            "listening": "Listening. Click again when you are done.",
            "thinking": "Thinking…",
            "answer": f"{self.name} is answering.",
            "error": "Something went wrong. Click to try again.",
        }
        self.setToolTip(tips[self.look])

    # -- painting ------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        now = time.monotonic()
        t, since = now - self._t0, now - self._look_since
        self._shown_level += (self.level - self._shown_level) * 0.45  # smooth the meter
        level = self._shown_level if self.look == "listening" else 0.0
        look = LOOK[self.look]
        scale = self.devicePixelRatioF()
        self._pixels = render_smoke(SIZE, scale, blobs_for(self.look, t, level, since), look.glow,
                                    look.glow_strength * (1 + 0.5 * level), heat=level)
        h, w, _ = self._pixels.shape
        self._image = QImage(self._pixels.data, w, h, w * 4, QImage.Format_RGBA8888_Premultiplied)
        self._image.setDevicePixelRatio(scale)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawImage(0, 0, self._image)
        self._paint_eyes(p, t, level, since)
        p.end()

    def _paint_eyes(self, p: QPainter, t: float, level: float, since: float) -> None:
        colour = QColor(LOOK[self.look].eye)
        grow = 1.10 + 0.22 * level if self.look == "listening" else 1.0
        gap, ey = 9.0 * grow, CY + 2
        dx = dy = 0.0
        if self.look == "thinking":
            dx, dy = 2.0 * math.sin(t * 2.2), -4.0
        elif self.look == "answer":
            dx, dy = -3.5 * min(1.0, since / 0.4), 1.0  # reading the card to its left
        for side in (-1, 1):
            ex = CX + side * gap + dx
            if self.look == "asleep":
                pen = QPen(colour, 2.0, Qt.SolidLine, Qt.RoundCap)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawArc(QRectF(ex - 4.5, ey - 4 + dy, 9, 6), 200 * 16, 140 * 16)  # closed: a small smile-arc
                continue
            if self.look == "error":
                p.setPen(QPen(colour, 2.2, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(ex - 3.5, ey), QPointF(ex + 3.5, ey))
                continue
            blink = 0.12 if (t % 4.3) < 0.12 else 1.0
            w, h = 7.5 * grow, (6.0 + 3.0 * level) * blink
            p.setPen(Qt.NoPen)
            halo = QColor(colour)
            halo.setAlpha(70)
            p.setBrush(halo)
            p.drawEllipse(QPointF(ex, ey + dy + h / 2), w * 0.95, h * 0.95 + 1.5)
            # Ember eye: flat on top, round underneath, slanted a little towards the middle.
            path = QPainterPath()
            top = ey + dy
            path.moveTo(ex - w / 2, top - side * 0.9)
            path.lineTo(ex + w / 2, top + side * 0.9)
            path.quadTo(ex + w / 2, top + h, ex, top + h)
            path.quadTo(ex - w / 2, top + h, ex - w / 2, top - side * 0.9)
            p.setBrush(colour)
            p.drawPath(path)

    # -- mouse ---------------------------------------------------------------

    def mousePressEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._dragging = False
        elif e.button() == Qt.RightButton:
            self.menu_requested.emit(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        pos = e.globalPosition().toPoint()
        if not self._dragging and (pos - self._press).manhattanLength() < DRAG_START_PX:
            return
        if not self._dragging:
            self._dragging = True
            self._grab = self._press - self.frameGeometry().topLeft()
        self.move(pos - self._grab)
        self.moved.emit()

    def mouseReleaseEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        if e.button() != Qt.LeftButton or self._press is None:
            return
        self._press = None
        if self._dragging:
            self._dragging = False
            self._save_position()
        else:
            self.clicked.emit()

    # -- placement -----------------------------------------------------------

    def place(self, corner: str, monitor: int) -> None:
        """Remembered position if it is still on a screen, else the configured corner."""
        saved = self._load_position()
        if saved is not None and any(s.availableGeometry().contains(saved + QPoint(SIZE // 2, SIZE // 2))
                                     for s in QApplication.screens()):
            self.move(saved)
            return
        screens = QApplication.screens()
        screen = screens[monitor - 1] if 0 < monitor <= len(screens) else QApplication.primaryScreen()
        area = screen.availableGeometry()
        pad = 4
        x = area.right() - SIZE - pad if "right" in corner else area.left() + pad
        y = area.bottom() - SIZE - pad if "bottom" in corner else area.top() + pad
        self.move(x, y)

    def _load_position(self) -> QPoint | None:
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))  # type: ignore[union-attr]
            return QPoint(int(data["x"]), int(data["y"]))
        except (AttributeError, OSError, ValueError, KeyError, TypeError):
            return None

    def _save_position(self) -> None:
        if self._state_file is None:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps({"x": self.x(), "y": self.y()}), encoding="utf-8")
        except OSError:
            pass  # remembering the position is a nicety
