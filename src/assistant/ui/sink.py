"""What the core tells the UI. The overlay implements this; tests use a fake.

Calls arrive from the asyncio worker thread. Implementations must be
thread-safe (the Qt overlay forwards everything through queued signals).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
        """state: idle | listening | thinking | speaking | waiting | error."""

    def transcript(self, text: str) -> None: ...

    def assistant_text(self, text: str) -> None:
        """A complete text block from Claude (markdown; may contain code fences)."""

    def activity(self, text: str) -> None:
        """One-line log entry: tool calls, decisions, snapshots."""

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
    def show_code(self, title: str, code: str, language: str) -> None: ...
    def request_approval(self, req: ApprovalRequest) -> None: ...
    def approval_closed(self, req_id: str, outcome: str) -> None: ...
    def modes(self, edit_mode: str, remote: bool) -> None: ...
