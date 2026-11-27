"""What Hellbot shows beside the orb now that there is no answer card.

Two small models, both pure (no Qt, time passed in) so they are unit-tested:

* `Captions`: your words, then the sentence being spoken, one at a time.
* `Pills`: the few things that need a click (an approval, code to copy, an
  error, the typing box) and which one is in front.

ui/speech.py paints the captions as smoke; ui/pill.py shows the pill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

YOU_LINGER_S = 6.0  # your words stay this long if nothing is said back
SAY_LINGER_S = 1.6  # the last sentence stays this long after the voice stops
FADE_S = 0.4


@dataclass(frozen=True)
class CaptionView:
    you: str
    say: str
    opacity: float

    @property
    def visible(self) -> bool:
        return self.opacity > 0.01 and bool(self.you or self.say)


@dataclass
class Captions:
    """Your words until Hellbot answers, then its current sentence."""

    you: str = ""
    say: str = ""
    _until: float | None = None  # when the current text starts to fade (None = stays)

    def heard(self, text: str, now: float) -> None:
        self.you, self.say, self._until = _clean(text), "", now + YOU_LINGER_S

    def working(self) -> None:
        """Claude is on it: keep your words up while it thinks."""
        if self.you and not self.say:
            self._until = None

    def spoken(self, sentence: str | None, now: float) -> None:
        """A sentence starts (text) or the voice went quiet (None)."""
        if sentence:
            self.you, self.say, self._until = "", _clean(sentence), None
        elif self.say:
            self._until = now + SAY_LINGER_S

    def clear(self) -> None:
        self.you, self.say, self._until = "", "", None

    def settle(self, now: float) -> None:
        """The request ended: whatever is still up may go."""
        if self._until is None and (self.you or self.say):
            self._until = now + (SAY_LINGER_S if self.say else 0.0)

    def view(self, now: float) -> CaptionView:
        opacity = 1.0
        if self._until is not None:
            opacity = max(0.0, min(1.0, 1 - (now - self._until) / FADE_S))
            if opacity <= 0:
                self.clear()
        return CaptionView(self.you, self.say, opacity)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pill:
    kind: str  # approval | code | error | typing
    id: str
    title: str = ""
    sub: str = ""
    body: str = ""  # the diff, command or code behind it
    body_kind: str = "text"  # "diff" | "text"
    language: str = ""

    @property
    def has_view(self) -> bool:
        """Worth a "View" button: more than fits on the pill's one line."""
        return self.body_kind == "diff" or len(self.body) > 70 or "\n" in self.body


_EXT = {"python": "py", "py": "py", "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts", "powershell": "ps1",
        "ps1": "ps1", "bash": "sh", "sh": "sh", "shell": "sh", "json": "json", "html": "html", "css": "css",
        "sql": "sql", "yaml": "yml", "yml": "yml", "toml": "toml", "markdown": "md", "md": "md", "diff": "diff",
        "c": "c", "cpp": "cpp", "java": "java", "go": "go", "rust": "rs", "xml": "xml", "csv": "csv"}


def extension(language: str, body_kind: str = "text") -> str:
    """File extension to open a snippet with, so the editor highlights it."""
    if body_kind == "diff":
        return "diff"
    return _EXT.get(language.strip().lower(), "txt")


def summarise(body: str, body_kind: str, language: str = "") -> str:
    """One line about what is behind a pill."""
    lines = body.splitlines()
    if body_kind == "diff":
        added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
        return f"+{added} −{removed} lines"
    count = "1 line" if len(lines) <= 1 else f"{len(lines)} lines"
    return f"{language} · {count}" if language else count


def _one_line(text: str, limit: int = 70) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass
class Pills:
    """Everything waiting for a click. `current()` is the one to show."""

    approvals: list[Pill] = field(default_factory=list)
    codes: list[Pill] = field(default_factory=list)
    error: Pill | None = None
    typing: bool = False
    _n: int = 0

    def add_approval(self, req_id: str, title: str, reason: str, body: str, body_kind: str) -> None:
        sub = summarise(body, body_kind) if body_kind == "diff" else _one_line(body) or _one_line(reason)
        self.approvals.append(Pill("approval", req_id, f"{title}?", sub, body, body_kind))

    def close_approval(self, req_id: str) -> None:
        self.approvals[:] = [p for p in self.approvals if p.id != req_id]

    def add_code(self, title: str, code: str, language: str) -> None:
        if not code.strip():
            return
        self._n += 1
        prose = language.strip().lower() in ("markdown", "md", "text", "txt", "")
        self.codes.append(Pill("code", f"c{self._n}", _one_line(title, 48) or ("Your notes are ready" if prose else "Your code is ready"),
                               summarise(code, "text", language), code, "text", language))
        del self.codes[:-8]  # an all-day session must not pile these up

    def set_error(self, text: str) -> None:
        self.error = Pill("error", "error", "Something went wrong", _clean(text))

    def clear_error(self) -> None:
        self.error = None

    def new_request(self) -> None:
        """You asked for something else: old code and errors are no longer the topic."""
        self.codes.clear()
        self.error = None

    def dismiss(self, pill_id: str) -> None:
        self.codes[:] = [p for p in self.codes if p.id != pill_id]
        if self.error is not None and self.error.id == pill_id:
            self.error = None

    def current(self) -> Pill | None:
        if self.typing:
            return Pill("typing", "typing")
        if self.approvals:
            return self.approvals[0]  # oldest first: that is the one Claude is blocked on
        if self.error is not None:
            return self.error
        if self.codes:
            newest = self.codes[-1]
            more = len(self.codes) - 1
            return newest if not more else Pill(newest.kind, newest.id, newest.title, f"{newest.sub} · {more} more",
                                                newest.body, newest.body_kind, newest.language)
        return None
