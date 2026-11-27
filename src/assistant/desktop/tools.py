"""The `desktop` MCP server: Claude's eyes and (light) hands on this PC.

Runs in-process via the Agent SDK (create_sdk_mcp_server), so there is no
extra server to install. Tool names reach Claude as mcp__desktop__<name>.
Which of these need your approval is decided in policy.py, not here.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import webbrowser
from typing import Any, Awaitable, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig

from ..intents import ScreenTarget, TargetKind, screen_target
from ..policy import DESKTOP_SERVER
from ..ui.sink import UiSink
from . import control, win32
from .capture import CaptureError, ScreenCapturer, Shot

log = logging.getLogger(__name__)

Result = dict[str, Any]


def _text(msg: str) -> Result:
    return {"content": [{"type": "text", "text": msg}]}


def _error(msg: str) -> Result:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def shot_blocks(shots: list[Shot]) -> list[dict[str, Any]]:
    """Image + caption blocks for a tool result."""
    blocks: list[dict[str, Any]] = []
    for s in shots:
        caption = f"[{s.id}] {s.label}, {s.width}x{s.height}px"
        if s.redacted:
            caption += f"; blacked out for privacy: {', '.join(s.redacted)}"
        blocks.append({"type": "text", "text": caption})
        blocks.append({"type": "image", "data": base64.b64encode(s.png).decode(), "mimeType": "image/png"})
    return blocks


def _guard(fn: Callable[[dict[str, Any]], Awaitable[Result]]) -> Callable[[dict[str, Any]], Awaitable[Result]]:
    """Turn exceptions into tool errors Claude can read and recover from."""

    async def wrapper(args: dict[str, Any]) -> Result:
        try:
            return await fn(args)
        except (CaptureError, control.ControlError, ValueError) as exc:
            return _error(str(exc))
        except Exception as exc:  # noqa: BLE001 - a tool must never crash the session
            log.exception("desktop tool failed")
            return _error(f"{type(exc).__name__}: {exc}")

    return wrapper


def build_desktop_server(capturer: ScreenCapturer, ui: UiSink, remote: bool = False) -> McpSdkServerConfig:
    """remote=True: only look + show_code (the screen is someone else's machine)."""

    @tool("screenshot",
          "Capture the screen. `target` is plain words: 'focused window' (default), 'this screen', 'left screen', "
          "'right screen', 'main screen', 'other screen', 'both screens', or 'screen with <app>'. Returns image(s) "
          "with an id like s12 that click() uses.",
          {"type": "object", "properties": {"target": {"type": "string"}}})
    @_guard
    async def screenshot(args: dict[str, Any]) -> Result:
        words = str(args.get("target") or "focused window")
        target = screen_target(words)
        if target.kind is TargetKind.NONE or words.strip().lower() in {"focused window", "window", "this window"}:
            target = ScreenTarget(TargetKind.FOREGROUND_WINDOW)
        shots = await asyncio.to_thread(capturer.capture, target)
        ui.activity(f"Captured: {', '.join(s.label for s in shots)}")
        return {"content": shot_blocks(shots)}

    @tool("list_monitors", "List monitors with their position, size, and which one is primary.",
          {"type": "object", "properties": {}})
    @_guard
    async def list_monitors(_args: dict[str, Any]) -> Result:
        mons = await asyncio.to_thread(capturer.monitors)
        rows = [f"monitor {m.index}: {m.rect.width}x{m.rect.height} at ({m.rect.left},{m.rect.top})"
                f"{' primary' if m.primary else ''}" for m in mons]
        return _text("\n".join(rows) or "no monitors")

    @tool("list_windows", "List visible windows (title, process, position), topmost first.",
          {"type": "object", "properties": {}})
    @_guard
    async def list_windows(_args: dict[str, Any]) -> Result:
        wins = await asyncio.to_thread(win32.list_windows)
        rows = [f"{w.process} | {w.title[:90]} | [{w.rect.left},{w.rect.top},{w.rect.right},{w.rect.bottom}]"
                for w in wins[:60]]
        return _text("\n".join(rows) or "no windows")

    @tool("ui_tree",
          "UI Automation tree of the focused window: control types, names and screen bounds. Prefer this plus "
          "click_element over clicking pixels.",
          {"type": "object", "properties": {"max_depth": {"type": "integer", "minimum": 1, "maximum": 8}}})
    @_guard
    async def ui_tree(args: dict[str, Any]) -> Result:
        return _text(await asyncio.to_thread(control.ui_tree, int(args.get("max_depth") or 4)))

    @tool("show_code",
          "Hand code, a diff or any long text to the user as a pill beside the orb, with Copy and Open-in-editor "
          "buttons. It is not read aloud. Use this in suggest-only and remote mode instead of editing files.",
          {"type": "object",
           "properties": {"code": {"type": "string"}, "language": {"type": "string"}, "title": {"type": "string"}},
           "required": ["code"]})
    @_guard
    async def show_code(args: dict[str, Any]) -> Result:
        ui.show_code(str(args.get("title") or "Code"), str(args["code"]), str(args.get("language") or ""))
        return _text("Handed to the user as a pill with Copy and Open-in-editor buttons.")

    @tool("open_url", "Open an http(s) URL in the default browser.",
          {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]})
    @_guard
    async def open_url(args: dict[str, Any]) -> Result:
        url = str(args["url"]).strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("only http(s) URLs can be opened")
        await asyncio.to_thread(webbrowser.open, url)
        return _text(f"Opened {url}")

    @tool("open_app", "Launch a program or open a file with its default app (e.g. 'notepad', 'code .', a path).",
          {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]})
    @_guard
    async def open_app(args: dict[str, Any]) -> Result:
        target = str(args["target"]).strip()
        if os.name != "nt":
            raise ValueError("open_app is Windows-only")
        # `start` resolves App Paths (notepad, code, excel); not a shell=True of
        # arbitrary text: the approval card showed the user this exact string.
        await asyncio.to_thread(subprocess.Popen, ["cmd", "/c", "start", "", *target.split(" ", 1)],
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return _text(f"Started {target}")

    @tool("click", "Click at pixel (x, y) of screenshot `shot_id` (image coordinates, not screen).",
          {"type": "object",
           "properties": {"shot_id": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
                          "button": {"type": "string", "enum": ["left", "right"]}, "double": {"type": "boolean"}},
           "required": ["shot_id", "x", "y"]})
    @_guard
    async def click(args: dict[str, Any]) -> Result:
        shot = capturer.get(str(args["shot_id"]))
        if shot is None:
            raise ValueError("unknown or expired shot_id; take a new screenshot")
        sx, sy = await asyncio.to_thread(control.click, shot, float(args["x"]), float(args["y"]),
                                         str(args.get("button") or "left"), bool(args.get("double")))
        await asyncio.sleep(0.4)  # let the UI repaint before Claude looks again
        return _text(f"Clicked screen ({sx},{sy}). Take a screenshot to confirm the result.")

    @tool("click_element", "Click/invoke a control in the focused window by its accessible name (see ui_tree).",
          {"type": "object", "properties": {"name": {"type": "string"}, "control_type": {"type": "string"}},
           "required": ["name"]})
    @_guard
    async def click_element(args: dict[str, Any]) -> Result:
        return _text(await asyncio.to_thread(control.click_element, str(args["name"]), args.get("control_type")))

    @tool("type_text", "Type text into the focused field. Short text only; edit files directly for code.",
          {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})
    @_guard
    async def type_text(args: dict[str, Any]) -> Result:
        await asyncio.to_thread(control.type_text, str(args["text"]))
        return _text("Typed.")

    @tool("press_keys", "Press a key combination such as 'ctrl+s', 'alt+tab', 'enter'.",
          {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]})
    @_guard
    async def press_keys(args: dict[str, Any]) -> Result:
        await asyncio.to_thread(control.press_keys, str(args["keys"]))
        return _text(f"Pressed {args['keys']}.")

    look = [screenshot, list_monitors, list_windows, show_code]
    hands = [ui_tree, open_url, open_app, click, click_element, type_text, press_keys]
    return create_sdk_mcp_server(DESKTOP_SERVER, "0.1.0", look if remote else look + hands)


def describe_input(tool_input: dict[str, Any]) -> str:
    return json.dumps(tool_input, indent=2, ensure_ascii=False)[:4000]
