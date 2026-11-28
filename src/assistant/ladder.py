"""The escalation ladder: which servant (model) answers a turn, and when to climb a rung.

The Herald (herald.py) already decides *who* handles a turn — the Steward (Gemini, direct) or
Claude. This module decides, for a Claude turn, *which model* and whether a doubtful result should
be retried one rung higher. One rung at a time, cheapest first:

    steward (Gemini flash-lite) -> haiku -> sonnet (default) -> opus -> fable

"steward" is the Gemini direct path (handled in the orchestrator before Claude is ever asked); only
the four Claude rungs are real models this module ever passes to the Brain.

How a turn's rung is chosen:
* A per-job starting rung from the config table ([ladder].jobs), "default" meaning [brain].model
  (so with an empty table everything runs on today's model — behaviour is preserved).
* A risky topic starts one rung higher (a mistake there is expensive).
* A fix that already failed once jumps straight to the Minister (Opus).
* The Treasurer may lower the ceiling as the budget cap nears; a floor keeps code/risky work at
  Sonnet or above however tight the budget.

How a turn climbs: after it runs, a hedge/low-confidence reply (reuse herald.looks_like_non_answer)
or a tool failure is doubt. If the turn made no irreversible change it is re-run one rung up; if it
did change something it is not silently redone — the doubt is remembered so the next related turn
starts higher instead.

Per-turn model switching uses the SDK's ClaudeSDKClient.set_model() (streaming mode only): no new
connection is needed, the running session simply changes model for the next query. The Brain owns
that call (Brain._apply_model); this module is pure functions, unit-tested, with no SDK dependency.
"""

from __future__ import annotations

from .herald import looks_like_non_answer

# Capability/cost order, cheapest first. Index into this list is a rung's height.
LADDER: tuple[str, ...] = ("steward", "haiku", "sonnet", "opus", "fable")
CLAUDE_RUNGS: tuple[str, ...] = ("haiku", "sonnet", "opus", "fable")  # real models the Brain can take
DEFAULT_RUNG = "sonnet"  # the Knight: Claude's everyday default when [brain].model is unset
TOP_RUNG = "fable"

# Job types the config table ([ladder].jobs) may key on. They are the Herald's own route reasons,
# plus "general" for anything else (empty/steward_fallback).
JOBS: tuple[str, ...] = ("simple", "code", "risky", "action", "screen", "general")
# Work where a mistake is expensive: the Treasurer may never downgrade these below Sonnet.
RISKY_JOBS: frozenset[str] = frozenset({"code", "risky", "action"})


def index(rung: str) -> int:
    return LADDER.index(rung)


def job_for(reason: str) -> str:
    """Map a Herald route reason (or "steward_fallback"/"empty") to a job type in the table."""
    return reason if reason in JOBS else "general"


def resolve_default(base_model: str | None) -> str:
    """The rung name the configured default model sits on. Anything that is not a named Claude rung
    (None, a dated model id, an unknown alias) is treated as the Knight, Sonnet, for ladder maths."""
    return base_model if base_model in CLAUDE_RUNGS else DEFAULT_RUNG


def floor_for(job: str) -> str:
    """The lowest rung a turn of this job may ever run on, even under budget pressure."""
    return "sonnet" if job in RISKY_JOBS else "haiku"


def _at_least(rung: str, floor: str) -> str:
    return LADDER[max(index(rung), index(floor))]


def _at_most(rung: str, ceiling: str) -> str:
    return LADDER[min(index(rung), index(ceiling))]


def climb(rung: str, ceiling: str = TOP_RUNG) -> str:
    """One rung up, never above the ceiling (and never down)."""
    return LADDER[min(index(rung) + 1, index(ceiling), index(TOP_RUNG))] if index(rung) < index(ceiling) else rung


def clamp(rung: str, ceiling: str, floor: str) -> str:
    """Keep a rung within [floor, ceiling]. The floor wins if they cross, so code/risky work stays at
    Sonnet or above even when the budget ceiling would push it lower."""
    return _at_least(_at_most(rung, ceiling), floor)


def start_rung(job: str, jobs_table: dict[str, str], base_model: str | None, failed_fix: bool = False) -> str:
    """The rung a turn starts on, before the Treasurer's ceiling is applied.

    The table maps a job to a model, "default" meaning [brain].model (today's behaviour). A risky
    topic starts one rung higher; a fix that already failed once starts at the Minister (Opus)."""
    want = jobs_table.get(job, "default")
    rung = resolve_default(base_model) if want == "default" else want
    if job == "risky":
        rung = climb(rung)
    if failed_fix and job in RISKY_JOBS:
        rung = _at_least(rung, "opus")
    return rung


def resolve_model(rung: str, base_model: str | None) -> str | None:
    """The model string to hand the Brain for this rung, or None to keep the session's default.

    When the rung is just the configured default and no model is pinned, None keeps Claude Code's own
    default for the plan (and avoids a needless set_model), preserving today's behaviour exactly."""
    if base_model is None and rung == resolve_default(base_model):
        return None
    return rung


def climb_triggers(result: object) -> str | None:
    """Why a finished turn is in doubt and should climb, or None if it looks fine.

    `result` is a brain.TurnResult; duck-typed here to keep this module SDK-free."""
    if getattr(result, "interrupted", False):
        return None
    if getattr(result, "tool_failed", False):
        return "tool_failed"
    if looks_like_non_answer(getattr(result, "text", "") or ""):
        return "hedge"
    return None


def retry_safe(result: object) -> bool:
    """True if the turn made no irreversible change, so re-running it a rung higher is harmless."""
    return not bool(getattr(result, "changes", None))
