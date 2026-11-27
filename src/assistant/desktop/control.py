"""Light screen control: the fallback when there is no file or API to use.

UI Automation first (click a control by its name), raw coordinates last.
Every function here is T2 in the policy, so it only runs after you approve
it beside the orb. All of it is Windows-only and untested off Windows.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from . import win32
from .capture import Shot

log = logging.getLogger(__name__)

MAX_TYPE_CHARS = 2000

_MODIFIER_NAMES = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift", "win": "cmd", "windows": "cmd",
                   "cmd": "cmd"}


class ControlError(RuntimeError):
    pass


def parse_combo(combo: str) -> list[str]:
    """'Ctrl+Shift+S' -> ['ctrl', 'shift', 's']; validates key names."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ControlError("empty key combination")
    return [_MODIFIER_NAMES.get(p, p) for p in parts]


def _pynput_key(name: str) -> Any:
    from pynput.keyboard import Key, KeyCode

    if len(name) == 1:
        return KeyCode.from_char(name)
    aliases = {"enter": "enter", "return": "enter", "esc": "esc", "escape": "esc", "del": "delete",
               "pgup": "page_up", "pgdn": "page_down", "pageup": "page_up", "pagedown": "page_down"}
    attr = aliases.get(name, name)
    if hasattr(Key, attr):
        return getattr(Key, attr)
    raise ControlError(f"unknown key '{name}'")


def press_keys(combo: str) -> None:
    from pynput.keyboard import Controller

    keys = [_pynput_key(k) for k in parse_combo(combo)]
    kb = Controller()
    try:
        for k in keys:
            kb.press(k)
    finally:
        for k in reversed(keys):
            kb.release(k)


def type_text(text: str) -> None:
    from pynput.keyboard import Controller

    if len(text) > MAX_TYPE_CHARS:
        raise ControlError(f"refusing to type more than {MAX_TYPE_CHARS} characters; edit the file instead")
    Controller().type(text)


def click(shot: Shot, x: float, y: float, button: str = "left", double: bool = False) -> tuple[int, int]:
    """Click at image coordinates (x, y) of a previous screenshot."""
    from pynput.mouse import Button, Controller

    if not (0 <= x <= shot.width and 0 <= y <= shot.height):
        raise ControlError(f"({x}, {y}) is outside the {shot.width}x{shot.height} screenshot")
    if shot.hwnd is not None:
        # Refuse if the window moved or resized since the screenshot: the
        # click would land somewhere else.
        current = next((w for w in win32.list_windows() if w.hwnd == shot.hwnd), None)
        if current is None:
            raise ControlError("the captured window is gone; take a new screenshot")
        if current.rect != shot.region:
            raise ControlError("the window moved or resized since the screenshot; take a new one")
    sx, sy = shot.to_screen(x, y)
    mouse = Controller()
    mouse.position = (sx, sy)
    time.sleep(0.05)
    mouse.click(Button.right if button == "right" else Button.left, 2 if double else 1)
    return sx, sy


def release_all() -> None:
    """Kill-switch helper: make sure no modifier or mouse button stays held."""
    try:
        from pynput.keyboard import Controller as KB, Key
        from pynput.mouse import Button, Controller as Mouse

        kb = KB()
        for k in (Key.ctrl, Key.ctrl_r, Key.alt, Key.alt_r, Key.shift, Key.shift_r, Key.cmd):
            try:
                kb.release(k)
            except Exception:  # noqa: BLE001 - releasing an unpressed key can raise on some backends
                pass
        m = Mouse()
        for b in (Button.left, Button.right, Button.middle):
            try:
                m.release(b)
            except Exception:  # noqa: BLE001
                pass
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# UI Automation
# ---------------------------------------------------------------------------


def ui_tree(max_depth: int = 4, max_nodes: int = 250) -> str:
    """The focused window's control tree as indented text (names, types, bounds)."""
    import uiautomation as auto

    fg = win32.foreground_window()
    if fg is None:
        return "No focused window."
    lines = [f"Window '{fg.title}' ({fg.process})"]
    with auto.UIAutomationInitializerInThread():
        root = auto.ControlFromHandle(fg.hwnd)
        if root is None:
            return lines[0] + "\n(UI Automation unavailable for this window)"
        for count, (ctrl, depth) in enumerate(auto.WalkControl(root, includeTop=False, maxDepth=max_depth)):
            if count >= max_nodes:
                lines.append(f"... truncated at {max_nodes} nodes")
                break
            r = ctrl.BoundingRectangle
            name = (ctrl.Name or "").replace("\n", " ")[:80]
            aid = f" id={ctrl.AutomationId}" if ctrl.AutomationId else ""
            lines.append(f"{'  ' * depth}{ctrl.ControlTypeName} '{name}'{aid} [{r.left},{r.top},{r.right},{r.bottom}]")
    return "\n".join(lines)


def click_element(name: str, control_type: str | None = None) -> str:
    """Invoke/click a control in the focused window by its accessible name."""
    import uiautomation as auto

    fg = win32.foreground_window()
    if fg is None:
        raise ControlError("no focused window")
    with auto.UIAutomationInitializerInThread():
        root = auto.ControlFromHandle(fg.hwnd)
        kwargs: dict[str, Any] = {"searchFromControl": root, "searchDepth": 12, "Name": name}
        if control_type:
            kwargs["ControlTypeName"] = control_type
        ctrl = auto.Control(**kwargs)
        if not ctrl.Exists(maxSearchSeconds=2):
            raise ControlError(f"no control named '{name}' in {fg.process}")
        try:
            ctrl.GetInvokePattern().Invoke()
            how = "invoked"
        except Exception:  # noqa: BLE001 - not every control supports Invoke
            ctrl.Click(simulateMove=False)
            how = "clicked"
        return f"{how} {ctrl.ControlTypeName} '{ctrl.Name}' in {fg.process}"
