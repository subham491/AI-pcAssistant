"""On-demand screenshots: focused window by default, dual-monitor aware.

Nothing is captured unless a turn asks for it (a look-phrase, or Claude
calling the screenshot tool). Nothing is saved unless config says so.

The geometry logic (which monitor is "left", "main", "other") is pure and
tested; the actual pixel grab uses mss.
"""

from __future__ import annotations

import io
import itertools
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from ..config import CaptureConfig
from ..intents import ScreenTarget, TargetKind
from . import win32
from .win32 import Rect, WindowInfo

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Monitor:
    index: int  # 1-based, left-to-right order is NOT guaranteed; see choose_monitors
    rect: Rect
    primary: bool


@dataclass(frozen=True)
class Shot:
    """One captured image plus what is needed to map clicks back to the screen."""

    id: str
    label: str  # "focused window: Code.exe", "left screen", ...
    png: bytes
    region: Rect  # screen area in physical pixels
    scale: float  # screen px per image px (>= 1 after downscaling)
    redacted: list[str]
    width: int
    height: int
    hwnd: int | None = None  # set when a single window was captured

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        return (round(self.region.left + x * self.scale), round(self.region.top + y * self.scale))


class CaptureError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Pure geometry
# ---------------------------------------------------------------------------


def monitor_of(monitors: list[Monitor], rect: Rect) -> Monitor | None:
    """The monitor holding most of `rect` (falls back to the centre point)."""
    best, best_area = None, 0
    for m in monitors:
        inter = m.rect.intersect(rect)
        area = inter.width * inter.height if inter else 0
        if area > best_area:
            best, best_area = m, area
    if best is None:
        cx, cy = rect.center
        best = next((m for m in monitors if m.rect.contains(cx, cy)), None)
    return best


def main_monitor(monitors: list[Monitor], main_index: int) -> Monitor:
    if main_index:
        for m in monitors:
            if m.index == main_index:
                return m
    return next((m for m in monitors if m.primary), monitors[0])


def choose_monitors(
    target: ScreenTarget,
    monitors: list[Monitor],
    foreground: WindowInfo | None,
    main_index: int = 0,
    find_app: Callable[[str], WindowInfo | None] = lambda _name: None,
) -> tuple[list[Monitor], str]:
    """Resolve a spoken target to monitor(s). Returns (monitors, label)."""
    if not monitors:
        raise CaptureError("no monitors found")
    fg_mon = monitor_of(monitors, foreground.rect) if foreground else None
    ordered = sorted(monitors, key=lambda m: (m.rect.left, m.rect.top))

    if target.kind is TargetKind.ALL_MONITORS:
        return ordered, "all screens"
    if target.kind is TargetKind.APP:
        win = find_app(target.which)
        if win is None:
            raise CaptureError(f"no open window matches '{target.which}'")
        mon = monitor_of(monitors, win.rect) or main_monitor(monitors, main_index)
        return [mon], f"screen with {target.which}"
    if target.kind is TargetKind.MONITOR:
        which = target.which
        if which == "left":
            return [ordered[0]], "left screen"
        if which == "right":
            return [ordered[-1]], "right screen"
        if which == "main":
            return [main_monitor(monitors, main_index)], "main screen"
        if which == "other":
            if len(monitors) < 2:
                return [monitors[0]], "only screen"
            ref = fg_mon or main_monitor(monitors, main_index)
            return [next(m for m in ordered if m != ref)], "other screen"
    # FOREGROUND_MONITOR, or a window target whose window is unknown.
    return [fg_mon or main_monitor(monitors, main_index)], "this screen"


def fit_scale(width: int, height: int, max_long_edge: int) -> float:
    long_edge = max(width, height)
    return 1.0 if long_edge <= max_long_edge else long_edge / max_long_edge


def redaction_boxes(region: Rect, windows: list[WindowInfo], redact_processes: list[str]) -> list[tuple[Rect, str]]:
    """Rects (relative to `region`) of windows that must be blacked out."""
    deny = {p.lower() for p in redact_processes}
    out = []
    for w in windows:
        if w.process.lower() in deny:
            inter = w.rect.intersect(region)
            if inter:
                rel = Rect(inter.left - region.left, inter.top - region.top,
                           inter.right - region.left, inter.bottom - region.top)
                out.append((rel, w.process))
    return out


# ---------------------------------------------------------------------------
# Grabbing pixels
# ---------------------------------------------------------------------------


class ScreenCapturer:
    """Grabs screenshots and keeps the last few so clicks can be mapped back."""

    _KEEP = 20

    def __init__(self, cfg: CaptureConfig, save_dir: Path | None = None) -> None:
        self.cfg = cfg
        self.save_dir = save_dir
        self.remote = False  # set by the orchestrator with remote mode: remote screens keep more detail
        self._shots: OrderedDict[str, Shot] = OrderedDict()
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def monitors(self) -> list[Monitor]:
        import mss  # lazy: not needed for tests

        with mss.mss() as sct:
            mons = sct.monitors[1:]  # [0] is the virtual union of all screens
        return [
            Monitor(i + 1, Rect(m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]),
                    primary=(m["left"] == 0 and m["top"] == 0))
            for i, m in enumerate(mons)
        ]

    def find_app(self, name: str) -> WindowInfo | None:
        needle = name.lower().strip()
        for w in win32.list_windows():
            if needle in w.title.lower() or needle in w.process.lower():
                return w
        return None

    def get(self, shot_id: str) -> Shot | None:
        with self._lock:
            return self._shots.get(shot_id)

    def capture(self, target: ScreenTarget) -> list[Shot]:
        fg = win32.foreground_window()
        if target.kind is TargetKind.FOREGROUND_WINDOW and fg is not None:
            label = f"focused window: {fg.process or fg.title}"
            return [self._grab(fg.rect, label, hwnd=fg.hwnd)]
        mons, label = choose_monitors(target, self.monitors(), fg, self.cfg.main_monitor, self.find_app)
        if len(mons) == 1:
            return [self._grab(mons[0].rect, label)]
        return [self._grab(m.rect, f"screen {n} of {len(mons)} (left to right)") for n, m in enumerate(mons, 1)]

    def capture_monitor_index(self, index: int) -> Shot:
        for m in self.monitors():
            if m.index == index:
                return self._grab(m.rect, f"monitor {index}")
        raise CaptureError(f"no monitor {index}")

    def _grab(self, region: Rect, label: str, hwnd: int | None = None) -> Shot:
        import mss

        if region.width <= 0 or region.height <= 0:
            raise CaptureError("window has no visible area (minimised?)")
        with mss.mss() as sct:
            raw = sct.grab({"left": region.left, "top": region.top, "width": region.width, "height": region.height})
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        boxes = redaction_boxes(region, win32.list_windows(), self.cfg.redact_processes)
        if boxes:
            draw = ImageDraw.Draw(img)
            for rect, proc in boxes:
                draw.rectangle([rect.left, rect.top, rect.right, rect.bottom], fill="black")
                draw.text((rect.left + 8, rect.top + 8), f"[hidden: {proc}]", fill="white")

        # PNG on purpose: the model bills by pixels, not bytes, so JPEG would blur text and save no tokens.
        # `scale` is stored on the Shot; Shot.to_screen (used by control.click) undoes exactly this resize.
        cap = self.cfg.remote_max_long_edge if self.remote else self.cfg.max_long_edge
        scale = fit_scale(img.width, img.height, cap)
        if scale > 1.0:
            img = img.resize((round(img.width / scale), round(img.height / scale)), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        shot = Shot(f"s{next(self._ids)}", label, buf.getvalue(), region, scale,
                    [p for _, p in boxes], img.width, img.height, hwnd)

        with self._lock:
            self._shots[shot.id] = shot
            while len(self._shots) > self._KEEP:
                self._shots.popitem(last=False)

        if self.cfg.save_screenshots and self.save_dir:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            (self.save_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{shot.id}.png").write_bytes(shot.png)
        return shot
