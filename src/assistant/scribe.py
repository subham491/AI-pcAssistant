"""The Scribe: Gemini writes the hand-over note when a session rolls, and the note is checked before
it is trusted.

A long session is replaced by a fresh one to keep costs down (see Brain._roll). Without help, the
fresh session only gets the last few exchanges, clipped (Carryover). The Scribe does better: it asks
Gemini — whose big context is cheap — to summarise those exchanges into a few sentences of real
context (open tasks, decisions), which becomes the note the fresh session opens with.

Check-and-rerun: a summary that drops the conversation's key facts, looks truncated, or invents
details it cannot support would poison the fresh session, so every summary is verified with cheap
LOCAL checks (no extra model call): it must be non-empty and complete, keep the key facts from the
raw exchanges (file paths, numbers, the user's last open request), and introduce no specific path or
number the raw text does not contain. If a check fails the summary is rerun once, telling Gemini
what was missing; if the rerun also fails we fall back to the raw Carryover and never keep a bad
summary. The outcome (ok / rerun / fallback) is logged by the Brain.

Privacy (plan section 4): only the already-clipped user/assistant text goes to Gemini — never a
screenshot, a file or a secret. Gated by [brain].gemini_key_file, like the Steward and Inspector.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from . import gemini

log = logging.getLogger(__name__)

_PROMPT = (
    "You are the note-keeper for a voice assistant whose session is being restarted to save cost. "
    "Summarise the recent conversation below in 3 to 5 short sentences so a fresh session can carry "
    "on seamlessly: capture open tasks, decisions made, file names and anything the user will refer "
    "back to, including their most recent request. Plain text only, no preamble, no markdown. Do not "
    "invent files, numbers or details that are not in the conversation.\n\n"
)

# A "specific": a number, or a token with a file extension / path separator (foo.py, a/b.txt).
_NUM = re.compile(r"\d+")
_PATH = re.compile(r"[\w.\\/-]*[\\/][\w.\\/-]+|\b\w[\w-]*\.[A-Za-z][A-Za-z0-9]+\b")


def _specifics(text: str) -> tuple[set[str], set[str]]:
    return set(_NUM.findall(text)), set(_PATH.findall(text))


@dataclass(frozen=True)
class ScribeResult:
    note: str | None  # the summary to carry over, or None to fall back to the raw Carryover
    outcome: str  # "ok" | "rerun" | "fallback" | "skip"


def verify(summary: str, items: list[dict[str, Any]]) -> tuple[bool, str]:
    """Cheap, local sanity check of a summary against the raw exchanges. Returns (ok, what_was_missing).

    Heuristic by design: it cannot prove a summary is faithful, only catch the obvious failures —
    emptiness, truncation, dropped files, a missed last request, and invented specifics."""
    s = (summary or "").strip()
    if not s:
        return False, "the summary was empty"
    if s.endswith(("…", "...", ",", ":", ";", "-")):
        return False, "the summary looks truncated"

    raw = " ".join(f"{i.get('user', '')} {i.get('reply', '')}" for i in items)
    raw_nums, raw_paths = _specifics(raw)
    sum_nums, sum_paths = _specifics(s)

    invented = (sum_paths - raw_paths) | (sum_nums - raw_nums)
    if invented:
        return False, "it mentions " + ", ".join(sorted(invented)) + " which the conversation does not"

    if raw_paths and not (raw_paths & sum_paths):  # it dropped every file the conversation named
        return False, "it drops the files " + ", ".join(sorted(raw_paths))

    last_user = (items[-1].get("user", "") if items else "").lower()
    salient = re.findall(r"[a-z]{4,}", last_user)
    if salient and not any(w in s.lower() for w in salient):
        return False, "it misses the user's last request: " + (items[-1].get("user", "") if items else "")

    return True, ""


async def _ask(cfg: Any, prompt: str) -> str | None:
    try:
        text = await asyncio.to_thread(gemini.ask, prompt, cfg.brain.gemini_key_file, gemini.DEFAULT_MODEL, 30.0)
    except Exception:  # noqa: BLE001 - the roll must never fail because Gemini is down
        log.debug("scribe gemini call failed", exc_info=True)
        return None
    return (text or "").strip() or None


async def summarize(cfg: Any, items: list[dict[str, Any]]) -> ScribeResult:
    """A verified hand-over note for a rolled session, rerun once if the first draft fails its checks,
    or None (fall back to the raw Carryover) if even the rerun is unusable."""
    if not items or not cfg.brain.gemini_key_file:
        return ScribeResult(None, "skip")

    convo = "\n".join(f"User: {i.get('user', '')}\nAssistant: {i.get('reply', '')}" for i in items)
    note = await _ask(cfg, _PROMPT + convo)
    missing = "the summarizer returned nothing"
    if note:
        ok, missing = verify(note, items)
        if ok:
            return ScribeResult(note, "ok")

    # One corrective rerun, told what the first draft got wrong.
    retry = await _ask(cfg, f"{_PROMPT}{convo}\n\nYour previous attempt was rejected because "
                            f"{missing}. Write a corrected summary that fixes exactly this.")
    if retry and verify(retry, items)[0]:
        return ScribeResult(retry, "rerun")

    return ScribeResult(None, "fallback")
