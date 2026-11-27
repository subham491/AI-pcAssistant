"""What the core tells the UI. The Qt UI (ui/tray.py QtSink) implements this; tests use a fake.

Calls arrive from the asyncio worker thread. Implementations must be
thread-safe (QtSink forwards everything through queued signals).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..steps import Step


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    title: str  # "Edit app.py", "Run command", ...
    tier: int
    reason: str
    body: str  # diff, command, or tool input as JSON
    body_kind: str = "text"  # "diff" | "text"


class UiSink(Protocol):
    def status(self, state: str, text: str) -> None:
        """state: idle | listening | transcribing | thinking | speaking | waiting | error."""

    def transcript(self, text: str) -> None: ...

    def assistant_text(self, text: str) -> None:
        """A complete text block from Claude (markdown; may contain code fences)."""

    def activity(self, text: str) -> None:
        """One-line log entry: tool calls, decisions, snapshots."""

    def step_started(self, step: Step) -> None:
        """Claude started a tool call (read a file, ran a command, ...)."""

    def step_finished(self, step_id: str, ok: bool, note: str) -> None:
        """That tool call ended. `note` says why when it failed."""

    def turn_finished(self, ok: bool) -> None:
        """The whole request is done (and spoken)."""

    def show_code(self, title: str, code: str, language: str) -> None: ...

    def request_approval(self, req: ApprovalRequest) -> None: ...

    def approval_closed(self, req_id: str, outcome: str) -> None: ...

    def modes(self, edit_mode: str, remote: bool) -> None: ...


class NullSink:
    """Headless sink (text mode prints instead)."""

    def status(self, state: str, text: str) -> None: ...
    def transcript(self, text: str) -> None: ...
    def assistant_text(self, text: str) -> None: ...
    def activity(self, text: str) -> None: ...
    def step_started(self, step: Step) -> None: ...
    def step_finished(self, step_id: str, ok: bool, note: str) -> None: ...
    def turn_finished(self, ok: bool) -> None: ...
    def show_code(self, title: str, code: str, language: str) -> None: ...
    def request_approval(self, req: ApprovalRequest) -> None: ...
    def approval_closed(self, req_id: str, outcome: str) -> None: ...
    def modes(self, edit_mode: str, remote: bool) -> None: ...
