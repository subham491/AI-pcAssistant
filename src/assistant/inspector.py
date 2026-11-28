"""The Inspector: a second pair of eyes from the other model family on risky work.

When a Claude turn changes something irreversible (edits a file, runs a changing shell command),
Gemini reviews the result. Different family, different blind spots. It is advice only: it never
edits or runs anything, it only returns text, which the orchestrator speaks briefly and shows with
mcp__desktop__show_code.

Privacy (plan section 4): Gemini sees only the user's own request and the diff/command summary of
what changed — never a screenshot, a note, or a secret. (The policy layer already blocks edits to
credential files, so a diff cannot carry one.) Fail soft: if Gemini is down this returns None and
the orchestrator skips the review with a quiet activity line. Gated by [brain].gemini_key_file.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import gemini

log = logging.getLogger(__name__)

_PROMPT = (
    "You are a careful code reviewer checking another assistant's work. The user asked for something "
    "and the assistant (Claude) made the changes below. Point out the single most important bug, risk "
    "or mistake, if any, in 1 to 3 short spoken sentences, plain text (no markdown or code). If the "
    "changes look correct and safe, reply exactly: Looks fine.\n\n"
)


def build_prompt(user_request: str, changes: list[str]) -> str:
    return f"{_PROMPT}User asked: {user_request}\n\nChanges made:\n" + "\n\n".join(changes)


async def review(cfg: Any, user_request: str, changes: list[str]) -> str | None:
    """Gemini's short review of what Claude changed, or None to skip (no key, nothing to review, or
    Gemini unavailable)."""
    if not changes or not cfg.brain.gemini_key_file:
        return None
    try:
        finding = await asyncio.to_thread(
            gemini.ask, build_prompt(user_request, changes), cfg.brain.gemini_key_file,
            gemini.DEFAULT_MODEL, 30.0,
        )
    except Exception:  # noqa: BLE001 - a review is a bonus; never let it break or delay the turn badly
        log.debug("inspector review failed", exc_info=True)
        return None
    finding = (finding or "").strip()
    return finding or None
