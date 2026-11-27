"""Turning Claude's tool calls into things a person can read at a glance.

`describe` maps a tool call to a `Step` ("reading… todo.md"). `Trail` keeps the
last few steps of the current turn and says what the puff trail above the orb
should show at a given moment. Both are pure (no Qt, no clock of their own) so
they are unit-tested; ui/trail.py only paints what `Trail.puffs` returns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

# kind -> the word shown while it runs. The orb picks its eyes from the kind too.
REACTIONS = {
    "think": "hmm…",
    "read": "reading…",
    "search": "looking…",
    "run": "running…",
    "edit": "writing…",
    "web": "browsing…",
    "screen": "looking at…",
    "control": "doing…",
    "ask": "asking…",
    "plan": "planning…",
    "other": "working…",
}
TAGS = {"think": "", "read": "READ", "search": "FIND", "run": "RUN", "edit": "EDIT", "web": "WEB",
        "screen": "LOOK", "control": "DO", "ask": "HELP", "plan": "PLAN", "other": "TOOL"}

_KINDS = {
    "Read": "read", "NotebookRead": "read", "LS": "read",
    "Glob": "search", "Grep": "search", "ToolSearch": "search",
    "Bash": "run", "PowerShell": "run", "BashOutput": "run", "KillShell": "run",
    "Edit": "edit", "Write": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
    "WebFetch": "web", "WebSearch": "web",
    "Task": "ask", "Agent": "ask",
    "TodoWrite": "plan",
}
_DESKTOP_KINDS = {
    "screenshot": "screen", "list_monitors": "screen", "list_windows": "screen", "ui_tree": "screen",
    "open_url": "web", "show_code": "edit",
}
MAX_LABEL = 48


@dataclass(frozen=True)
class Step:
    """One tool call, described for the UI."""

    id: str
    kind: str  # a key of REACTIONS
    label: str  # short: a file name, a command, a search term
    detail: str = ""  # the full path or command, for the activity log

    @property
    def reaction(self) -> str:
        return REACTIONS.get(self.kind, REACTIONS["other"])

    @property
    def tag(self) -> str:
        return TAGS.get(self.kind, TAGS["other"])


def _short(text: Any, limit: int = MAX_LABEL) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _name(path: Any) -> str:
    # PurePath alone would not split "C:\a\b.py" on Linux (CI), so normalise first.
    text = str(path or "").replace("\\", "/").rstrip("/")
    return PurePath(text).name or text


def _host(url: Any) -> str:
    m = re.match(r"^[a-z]+://([^/?#]+)", str(url or ""), re.I)
    return m.group(1) if m else _short(url, 32)


def describe(step_id: str, tool_name: str, tool_input: dict[str, Any] | None) -> Step:
    """What a tool call is doing, in a few words. Never raises on odd input."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    base = tool_name.rsplit("__", 1)[-1] if tool_name.startswith("mcp__") else tool_name
    if tool_name.startswith("mcp__desktop__"):
        kind = _DESKTOP_KINDS.get(base, "control")
    elif tool_name.startswith("mcp__"):
        return _describe_app(step_id, tool_name, inp)
    else:
        kind = _KINDS.get(tool_name, "other")

    path = inp.get("file_path") or inp.get("notebook_path") or inp.get("path")
    if kind in ("read", "edit") and path:
        label, detail = _name(path), str(path)
    elif kind == "search":
        term = inp.get("pattern") or inp.get("query") or ""
        label, detail = (f"“{_short(term, 30)}”" if term else "files"), _short(f"{term} in {path}" if path else term, 200)
    elif kind == "run":
        cmd = inp.get("command") or ""
        label, detail = _short(inp.get("description") or cmd or "a command"), _short(cmd, 200)
    elif kind == "web":
        target = inp.get("url") or inp.get("query") or ""
        label = _host(target) if inp.get("url") else f"“{_short(target, 30)}”"
        detail = _short(target, 200)
    elif kind == "ask":
        label, detail = _short(inp.get("description") or inp.get("subagent_type") or "a helper"), ""
    elif kind == "plan":
        todos = inp.get("todos") if isinstance(inp.get("todos"), list) else []
        label, detail = (f"{len(todos)} steps" if todos else "the steps"), ""
    elif kind == "screen":
        label, detail = {"screenshot": "your screen", "ui_tree": "the window", "list_windows": "open windows",
                         "list_monitors": "your monitors"}.get(base, base.replace("_", " ")), ""
    else:
        first = next((v for v in inp.values() if isinstance(v, (str, int, float)) and str(v).strip()), "")
        label, detail = base.replace("_", " "), _short(first, 200)
        if kind == "control" and first:
            label = _short(f"{label}: {first}")
    return Step(step_id, kind, _short(label) or tool_name, detail)


_APP_VERBS = (
    ("search", ("search", "find", "list", "query", "lookup", "suggest")),
    ("read", ("get", "read", "fetch", "download", "view", "export")),
    ("edit", ("create", "send", "update", "write", "delete", "trash", "reply", "forward", "label", "unlabel",
              "apply", "add", "remove", "move", "share", "copy", "upload", "respond", "mark", "unmark", "untrash",
              "edit", "merge", "push", "resolve")),
)


def _describe_app(step_id: str, tool_name: str, inp: dict[str, Any]) -> Step:
    """A connected app's tool, e.g. mcp__claude_ai_Gmail__search_threads -> "Gmail: category:updates"."""
    _, _, rest = tool_name.partition("mcp__")
    server, _, action = rest.rpartition("__")
    app = re.sub(r"^(claude_ai_|plugin_[^_]+_)", "", server).replace("_", " ").replace("-", " ").strip() or "app"
    app = app if any(c.isupper() for c in app) else app.title()
    words = action.replace("-", "_").split("_")
    kind = next((k for k, verbs in _APP_VERBS if words and words[0].lower() in verbs), "other")
    arg = next((v for v in inp.values() if isinstance(v, str) and v.strip()), "")
    what = _short(arg, 34) if arg else " ".join(words[1:] or words)
    return Step(step_id, kind, _short(f"{app}: {what}"), _short(f"{action} {arg}".strip(), 200))


# ---------------------------------------------------------------------------
# The trail: which puffs are on screen right now
# ---------------------------------------------------------------------------

MAX_PUFFS = 4  # the newest, full size, and up to three small ticked ones above it
RISE_S = 0.7  # a new puff takes this long to leave the head
FADE_S = 0.8
DONE_LINGER_S = 2.5  # "all done" stays this long
FAIL_LINGER_S = 8.0  # a failed step stays readable longer
THINK_ID = "__think__"


@dataclass
class _Entry:
    step: Step
    started: float
    status: str = "running"  # running | done | failed
    ended: float | None = None
    note: str = ""


@dataclass(frozen=True)
class Puff:
    """One puff as it should be drawn. `slot` 0 is the newest, nearest the head."""

    key: str
    slot: int
    status: str  # running | done | failed | summary
    tag: str
    reaction: str  # shown only on the newest while it runs
    label: str
    compact: bool
    opacity: float
    rise: float  # 0 at the head .. 1 in place
    age: float
    note: str = ""  # why it failed


@dataclass
class Trail:
    """The steps of the current turn. Every method takes `now` (seconds, monotonic)."""

    entries: list[_Entry] = field(default_factory=list)
    turn_started: float | None = None
    closing: float | None = None  # when the puffs started to fade (speaking or finished)
    summary: Puff | None = None
    summary_at: float | None = None
    count: int = 0  # real steps this turn

    # -- events ------------------------------------------------------------

    def begin(self, now: float) -> None:
        """A new turn: clear what is left of the last one."""
        self.entries.clear()
        self.turn_started, self.closing, self.summary, self.summary_at, self.count = now, None, None, None, 0

    def thinking(self, now: float) -> None:
        """Claude is thinking with no tool running: show a 'hmm…' puff."""
        if self.turn_started is None:
            self.begin(now)
        self.closing = None
        if not any(e.status == "running" for e in self.entries):
            self.entries.append(_Entry(Step(THINK_ID, "think", ""), now))

    def start(self, step: Step, now: float) -> None:
        if self.turn_started is None:
            self.begin(now)
        self.closing = None
        self._drop_think()
        if any(e.step.id == step.id for e in self.entries):
            return  # the same call reported twice
        self.entries.append(_Entry(step, now))
        self.count += 1
        del self.entries[: max(0, len(self.entries) - 12)]  # a long turn must not grow without bound

    def finish(self, step_id: str, ok: bool, note: str, now: float) -> None:
        for e in self.entries:
            if e.step.id == step_id and e.status == "running":
                e.status, e.ended, e.note = ("done" if ok else "failed"), now, _short(note, 60)

    def speaking(self, now: float) -> None:
        """The answer is being spoken: the puffs have done their job."""
        self._settle(now)
        if self.closing is None:
            self.closing = now

    def turn_done(self, ok: bool, now: float) -> None:
        self._settle(now)
        if self.closing is None:
            self.closing = now
        if ok and self.count and self.turn_started is not None:
            took = max(0.0, now - self.turn_started)
            steps = "1 step" if self.count == 1 else f"{self.count} steps"
            self.summary = Puff("__done__", 0, "summary", "", "all done", f"{steps} · {took:.0f}s", False, 1.0, 1.0, 0.0)
            self.summary_at = now
        self.turn_started = None

    def clear(self) -> None:
        self.entries.clear()
        self.turn_started = self.closing = self.summary = self.summary_at = None
        self.count = 0

    def _drop_think(self) -> None:
        self.entries[:] = [e for e in self.entries if e.step.id != THINK_ID]

    def _settle(self, now: float) -> None:
        """A step with no finish event (a declined approval, a stop) must not spin forever."""
        self._drop_think()
        for e in self.entries:
            if e.status == "running":
                e.status, e.ended = "done", now

    # -- what to draw --------------------------------------------------------

    def current_kind(self) -> str | None:
        """Kind of the step running now, for the orb's eyes."""
        for e in reversed(self.entries):
            if e.status == "running":
                return e.step.kind
        return None

    def puffs(self, now: float) -> list[Puff]:
        out: list[Puff] = []
        fade = 1.0 if self.closing is None else max(0.0, 1 - (now - self.closing) / FADE_S)
        shown = self.entries[-MAX_PUFFS:]
        for slot, e in enumerate(reversed(shown)):
            age = now - e.started
            opacity = 1.0 if slot == 0 else max(0.0, 0.85 - 0.2 * slot)
            if e.status == "failed":
                # A failure outlives the fade so it can be read, then goes too.
                left = FAIL_LINGER_S - (now - (e.ended or now))
                opacity = max(0.0, min(1.0, left / FADE_S)) if left < FADE_S else 1.0
            elif self.summary is not None:
                continue  # "all done" takes their place
            else:
                opacity *= fade
            if opacity <= 0.01:
                continue
            out.append(Puff(e.step.id, slot, e.status, e.step.tag, e.step.reaction if e.status == "running" else "",
                            e.step.label, compact=slot > 0, opacity=opacity * min(1.0, age / 0.25),
                            rise=min(1.0, max(0.0, age / RISE_S)), age=age, note=e.note))
        if self.summary is not None and self.summary_at is not None:
            age = now - self.summary_at
            left = DONE_LINGER_S - age
            if left <= 0:
                self.summary = self.summary_at = None
            else:
                s = self.summary
                out.append(Puff(s.key, 0, s.status, s.tag, s.reaction, s.label, False,
                                min(1.0, age / 0.25, left / FADE_S), min(1.0, age / RISE_S), age))
        return out

    def active(self, now: float) -> bool:
        return bool(self.puffs(now))
