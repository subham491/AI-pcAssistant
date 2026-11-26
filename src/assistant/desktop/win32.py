"""Thin ctypes wrappers over the few Win32 calls we need.

No pywin32 here on purpose: ctypes is in the standard library and these
calls are stable. Every function degrades to a harmless value off Windows
so the rest of the code (and the tests) can import this module anywhere.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def intersect(self, other: "Rect") -> "Rect | None":
        r = Rect(max(self.left, other.left), max(self.top, other.top),
                 min(self.right, other.right), min(self.bottom, other.bottom))
        return r if r.width > 0 and r.height > 0 else None

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process: str  # e.g. "Code.exe"
    pid: int
    rect: Rect


def set_dpi_awareness() -> None:
    """Per-monitor DPI awareness: all coordinates become physical pixels.

    Without this, screenshots and clicks disagree on scaled displays, which is
    the most common Windows automation bug. Must run before any window/Qt call.
    """
    if not IS_WINDOWS:
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        log.warning("could not set DPI awareness; clicks may be offset on scaled displays")


def _process_name(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:  # noqa: BLE001 - access denied, gone, psutil missing
        return ""


if IS_WINDOWS:
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _dwmapi = ctypes.windll.dwmapi
    _EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _GW_HWNDNEXT = 2
    _DWMWA_EXTENDED_FRAME_BOUNDS = 9
    _DWMWA_CLOAKED = 14

    def _title(hwnd: int) -> str:
        n = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _rect(hwnd: int) -> Rect:
        r = wintypes.RECT()
        # Extended frame bounds exclude the invisible resize border.
        if _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            _user32.GetWindowRect(hwnd, ctypes.byref(r))
        return Rect(r.left, r.top, r.right, r.bottom)

    def _pid(hwnd: int) -> int:
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _cloaked(hwnd: int) -> bool:
        val = ctypes.c_int(0)
        _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
        return val.value != 0

    def _info(hwnd: int) -> WindowInfo:
        pid = _pid(hwnd)
        return WindowInfo(int(hwnd), _title(hwnd), _process_name(pid), pid, _rect(hwnd))

    def _usable(hwnd: int) -> bool:
        return bool(
            _user32.IsWindowVisible(hwnd)
            and not _user32.IsIconic(hwnd)
            and not _cloaked(hwnd)
            and _user32.GetWindowTextLengthW(hwnd) > 0
        )

    def foreground_window(skip_own_process: bool = True) -> WindowInfo | None:
        """The focused window; if that is the assistant's own overlay, the one below it."""
        hwnd = _user32.GetForegroundWindow()
        own = os.getpid()
        while hwnd:
            if _usable(hwnd) and not (skip_own_process and _pid(hwnd) == own):
                return _info(hwnd)
            hwnd = _user32.GetWindow(hwnd, _GW_HWNDNEXT)
        return None

    def list_windows() -> list[WindowInfo]:
        """Visible, titled top-level windows in z-order (topmost first)."""
        found: list[WindowInfo] = []

        def cb(hwnd: int, _lparam: int) -> bool:
            if _usable(hwnd):
                found.append(_info(hwnd))
            return True

        _user32.EnumWindows(_EnumWindowsProc(cb), 0)
        return found

    def focus_window(hwnd: int) -> bool:
        return bool(_user32.SetForegroundWindow(hwnd))

else:  # pragma: no cover - non-Windows stubs

    def foreground_window(skip_own_process: bool = True) -> WindowInfo | None:
        return None

    def list_windows() -> list[WindowInfo]:
        return []

    def focus_window(hwnd: int) -> bool:
        return False
