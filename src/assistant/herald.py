"""The Herald: a free, local rule-based router that runs before a turn becomes a Claude Code turn.

It sits logically between `intents.parse()` (which already catches STOP / APPROVE / mode changes /
profile switches) and a full Claude turn. For whatever is left — a real question or request — the
Herald decides who should handle it:

* "claude"  -> a normal Claude Code turn (it can read the screen, edit files, run commands, use skills).
* "steward" -> a cheap direct answer from Gemini (see the Steward path in the orchestrator), which
               has no tools and only returns text. If Gemini errs or hedges, the Steward falls back
               to Claude, so a Steward route never leaves the user without an answer.

Rules first, cheapest-safe default: anything that touches code, files, the screen, or anything risky
(money, logins, deletes, sending) goes to Claude; anything that asks the PC to *do* something goes to
Claude (Gemini cannot act); only plain talk and facts are handed to the Steward.

Pure functions only, so all of this is unit-tested. No model call, no network, no cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .intents import TargetKind, normalize, screen_target


class Rung(str, Enum):
    CLAUDE = "claude"  # a full Claude Code turn
    STEWARD = "steward"  # a cheap Gemini direct answer (with Claude fallback)


@dataclass(frozen=True)
class Route:
    rung: Rung
    reason: str


# Anything risky: a mistake here is expensive, so Claude (and the policy layer) handles it.
_RISK = re.compile(
    r"\b(money|pay|paid|payment|buy|purchase|order|invoice|bank|card|price|cost|refund|transfer|"
    r"login|log in|logged in|log out|sign in|sign-in|signin|password|passcode|credential|account|"
    r"auth|otp|2fa|token|api key|secret|"
    r"delete|remove|uninstall|wipe|erase|format|drop|rm |trash|"
    r"send|email|e-mail|message|dm|reply|forward|post|publish|tweet|push|deploy|commit|merge|"
    r"install|payroll|bill|subscribe|unsubscribe)\b"
)

# Anything about code, files or the machine's own contents: Claude needs its tools for this.
_CODE = re.compile(
    r"\b(code|coding|function|class|method|variable|bug|debug|error|exception|traceback|stack trace|"
    r"compile|build|lint|test|tests|refactor|repo|repository|git|branch|commit|pull request|pr|"
    r"file|files|folder|directory|path|script|program|module|package|import|syntax|"
    r"python|javascript|typescript|java|rust|golang|go code|c\+\+|html|css|sql|json|yaml|toml|regex|"
    r"terminal|command line|shell|bash|powershell|config|configuration|log|logs|server|localhost|port)\b"
)

# Anything that asks the assistant to look at or act on the screen / windows / apps / devices.
_SCREEN = re.compile(
    r"\b(screen|screens|monitor|monitors|display|window|windows|highlighted|selected|this|these|here|"
    r"snap|maximize|minimise|minimize|resize|move)\b"
)

# Action verbs: the user wants the PC to *do* something. Only Claude (through the desktop tools and
# skills) can act; Gemini only talks, so routing these to the Steward would just waste a round trip.
_ACTION = re.compile(
    r"\b(open|close|launch|start|run|stop|restart|quit|kill|play|pause|mute|unmute|turn (on|off|up|down)|"
    r"volume|channel|click|double-click|type|press|scroll|drag|snap|maximize|minimize|resize|move|"
    r"screenshot|capture|take a picture|record|"
    r"create|make|write|edit|change|update|rename|save|download|upload|copy|paste|cut|"
    r"set|enable|disable|toggle|switch|schedule|remind)\b"
)


def route(text: str) -> Route:
    """Decide who should handle a turn's text. Rules only; defaults to Claude when unsure."""
    norm = normalize(text)
    if not norm:
        return Route(Rung.CLAUDE, "empty")

    # If the words point at the screen, a screenshot is coming: that is a Claude turn.
    if screen_target(text).kind is not TargetKind.NONE:
        return Route(Rung.CLAUDE, "screen")
    if _RISK.search(norm):
        return Route(Rung.CLAUDE, "risky")
    if _CODE.search(norm):
        return Route(Rung.CLAUDE, "code")
    if _SCREEN.search(norm):
        return Route(Rung.CLAUDE, "screen")
    if _ACTION.search(norm):
        return Route(Rung.CLAUDE, "action")
    # What is left is plain talk or a factual question: cheap enough for the Steward to try.
    return Route(Rung.STEWARD, "simple")


# A Gemini answer that looks like a refusal or an "I can't do that / I don't know" hedge: the Steward
# treats it as a miss and falls back to Claude rather than speaking a non-answer.
_HEDGE = re.compile(
    r"(i can't|i cannot|i can not|i'm not able|i am not able|i'm unable|i am unable|unable to|"
    r"i don't have|i do not have|i don't know|i do not know|not sure|i'm not sure|i am not sure|"
    r"as an ai|language model|i cannot provide|i can't provide|i'm sorry|i am sorry|"
    r"i don't have access|no access to|real-time|real time|up to date|up-to-date|"
    r"cannot access|can't access|i'm just|i am just|need to check that)",
    re.IGNORECASE,
)


def looks_like_non_answer(text: str) -> bool:
    """True if Gemini's reply is empty or reads like a refusal/hedge, so the Steward should fall back."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    return bool(_HEDGE.search(stripped))
