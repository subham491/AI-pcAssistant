"""Local intent parsing: things handled on this PC without asking the model.

Control words (stop, apply, mode changes) must never round-trip through the
model: they have to work while the model is busy, offline, or confused, and
they must be instant. Everything else becomes a normal turn for Claude.

Pure functions only, so all of this is unit-tested.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from enum import Enum

from .config import EditMode


class Kind(str, Enum):
    STOP = "stop"
    APPROVE = "approve"
    REJECT = "reject"
    SET_EDIT_MODE = "set_edit_mode"
    REMOTE_ON = "remote_on"
    REMOTE_OFF = "remote_off"
    UNDO = "undo"
    PULL_PROJECT = "pull_project"
    REMEMBER = "remember"
    NEW_SESSION = "new_session"
    TURN = "turn"  # anything else: send to Claude


@dataclass(frozen=True)
class Intent:
    kind: Kind
    text: str = ""
    edit_mode: EditMode | None = None


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s'-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Whisper sometimes transcribes the tail of the wake word.
    text = re.sub(r"^(hey|okay|ok)?\s*jarvis\b\s*", "", text)
    return text


_STOP = {"stop", "stop it", "stop that", "cancel", "cancel that", "abort", "halt", "stop stop", "never mind", "nevermind"}
_APPROVE = {"apply", "apply it", "apply that", "approve", "approved", "confirm", "confirmed", "yes", "yes please",
            "yes apply", "do it", "go ahead", "ok apply", "okay apply", "accept"}
_REJECT = {"reject", "reject it", "no", "nope", "deny", "denied", "don't", "do not", "skip", "skip it", "discard",
           "no thanks"}
_UNDO = {"undo", "undo that", "undo it", "revert that", "undo the last edit", "revert the last edit"}
_NEW = {"new conversation", "start over", "new session", "fresh start", "reset conversation"}
_REMOTE_ON = {"remote mode", "switch to remote mode", "remote mode on", "enter remote mode", "go remote"}
_REMOTE_OFF = {"local mode", "exit remote mode", "leave remote mode", "remote mode off", "switch to local mode"}

_MODE_PATTERNS: list[tuple[re.Pattern[str], EditMode]] = [
    (re.compile(r"^(switch to |set |use |go to )?(suggest|suggestion|suggest only|suggestions only)( only)?( mode)?$"),
     EditMode.SUGGEST),
    (re.compile(r"^(switch to |set |use |go to )?(review|review each edit|review mode|ask before edits)( mode)?$"),
     EditMode.REVIEW),
    (re.compile(r"^(switch to |set |use |go to )?(auto|auto apply|auto-apply|automatic)( edits)?( mode)?$"),
     EditMode.AUTO),
]

_PULL = re.compile(r"^(pull|grab|fetch|copy) (her|my sister's|sister's|the remote|the) (project|code|codebase)\b")
_REMEMBER = re.compile(r"^(please )?remember(,)? (that |this )?(?P<fact>.+)$", re.IGNORECASE)


def parse(text: str) -> Intent:
    norm = normalize(text)
    if not norm:
        return Intent(Kind.TURN, "")
    if norm in _STOP:
        return Intent(Kind.STOP)
    if norm in _APPROVE:
        return Intent(Kind.APPROVE)
    if norm in _REJECT:
        return Intent(Kind.REJECT)
    if norm in _UNDO:
        return Intent(Kind.UNDO)
    if norm in _NEW:
        return Intent(Kind.NEW_SESSION)
    if norm in _REMOTE_ON:
        return Intent(Kind.REMOTE_ON)
    if norm in _REMOTE_OFF:
        return Intent(Kind.REMOTE_OFF)
    for pattern, mode in _MODE_PATTERNS:
        if pattern.match(norm):
            return Intent(Kind.SET_EDIT_MODE, edit_mode=mode)
    if _PULL.match(norm):
        return Intent(Kind.PULL_PROJECT)
    m = _REMEMBER.match(_strip_wake(text.strip()))
    if m:
        return Intent(Kind.REMEMBER, m.group("fact").strip().rstrip("."))
    return Intent(Kind.TURN, text.strip())


def strip_wake_phrase(text: str, phrase: str, min_ratio: float = 0.85) -> str | None:
    """The rest of `text` if it starts with the wake phrase, else None.

    Fuzzy on purpose: Whisper may write "Hell bot" or "Helbot" for "Hellbot".
    0.85 keeps "hello" out while letting those spellings in.
    """
    key = re.sub(r"[\W_]", "", phrase.lower())
    words = text.split()
    best_n, best = 0, 0.0
    for n in range(1, min(len(words), len(phrase.split()) + 1) + 1):
        head = re.sub(r"[\W_]", "", "".join(words[:n]).lower())
        ratio = difflib.SequenceMatcher(None, head, key).ratio()
        if ratio > best:
            best_n, best = n, ratio
    if not key or best < min_ratio:
        return None
    return " ".join(words[best_n:]).lstrip(" ,.!?:;-")


def _strip_wake(text: str) -> str:
    # Keep the user's original casing for remembered facts.
    return re.sub(r"^(?i:(hey|okay|ok)?\s*jarvis[,.!]?\s*)", "", text).strip()


# --------------------------------------------------------------------------
# Screen targets: which pixels does a turn want?
# --------------------------------------------------------------------------


class TargetKind(str, Enum):
    NONE = "none"  # no screenshot attached
    FOREGROUND_WINDOW = "foreground_window"
    FOREGROUND_MONITOR = "foreground_monitor"
    MONITOR = "monitor"  # a specific one, see ScreenTarget.which
    APP = "app"  # monitor showing a named app
    ALL_MONITORS = "all_monitors"


@dataclass(frozen=True)
class ScreenTarget:
    kind: TargetKind
    which: str = ""  # left | right | main | other | <app name>


_LOOK_WORDS = re.compile(
    r"\b(look|looking|see|seeing|screen|screens|monitor|monitors|window|this|these|here|showing|"
    r"on my screen|what's on|whats on|read this|error|highlighted|selected)\b"
)
_APP_TARGET = re.compile(r"\b(?:the )?(?:screen|monitor|one) (?:with|showing|that has) (?:the )?(?P<app>[\w .-]+?)(?: on it)?$")


def screen_target(text: str) -> ScreenTarget:
    """Decide what to capture from what was said.

    Default is the focused window only: fewer pixels, faster, and the other
    monitor stays private unless asked for.
    """
    norm = normalize(text)
    if re.search(r"\b(both|all|every) (screens|monitors|displays)\b", norm):
        return ScreenTarget(TargetKind.ALL_MONITORS)
    m = _APP_TARGET.search(norm)
    if m:
        return ScreenTarget(TargetKind.APP, m.group("app").strip())
    for side in ("left", "right"):
        if re.search(rf"\b{side} (screen|monitor|display|one)\b", norm):
            return ScreenTarget(TargetKind.MONITOR, side)
    if re.search(r"\b(main|primary) (screen|monitor|display)\b", norm):
        return ScreenTarget(TargetKind.MONITOR, "main")
    if re.search(r"\b(other|second|secondary) (screen|monitor|display|one)\b", norm):
        return ScreenTarget(TargetKind.MONITOR, "other")
    if re.search(r"\b(this|whole|entire|my) (screen|monitor|display)\b", norm):
        return ScreenTarget(TargetKind.FOREGROUND_MONITOR)
    if _LOOK_WORDS.search(norm):
        return ScreenTarget(TargetKind.FOREGROUND_WINDOW)
    return ScreenTarget(TargetKind.NONE)
