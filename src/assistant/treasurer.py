"""The Treasurer: reads the real cost from the audit log, keeps a daily and weekly tally, and as a
configured cap nears, warns by voice and lowers the ladder's ceiling (never below Sonnet for
code/risky work).

Cost comes straight from the Phase 0 audit log — usage.summarize already does the per-day dollar
maths (turning old running-total entries into per-turn cost), so the Treasurer reuses it rather than
re-deriving anything. Caps live in [treasurer]; 0 means off, so by default the Treasurer only
observes and never changes a turn.

    python -m assistant.treasurer   # one-line daily total, with turns per rung
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import ladder
from .usage import rung_counts, summarize

RUNG_ORDER = ("steward", "haiku", "sonnet", "opus", "fable")


class Treasurer:
    def __init__(self, cfg: Any, audit_path: Path) -> None:
        self.cfg = cfg
        self.path = audit_path
        self._warned_day: str | None = None  # the day we last spoke a budget warning (once per day)

    # -- reading the ledger ------------------------------------------------

    def _lines(self) -> list[str]:
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    def _days(self) -> dict[str, dict[str, float]]:
        return summarize(self._lines())

    def spent(self, today: date | None = None) -> tuple[float, float]:
        """(today's dollars, this week's dollars incl. today). The week is the trailing 7 days."""
        today = today or datetime.now().date()
        days = self._days()
        day_cost = days.get(today.isoformat(), {}).get("cost", 0.0)
        week_start = today - timedelta(days=6)
        week_cost = sum(d.get("cost", 0.0) for iso, d in days.items()
                        if week_start <= _as_date(iso) <= today)
        return day_cost, week_cost

    def pressure(self, today: date | None = None) -> float:
        """How full the tightest active cap is, 0..n (0 if no cap is set)."""
        day_cost, week_cost = self.spent(today)
        fractions = []
        if self.cfg.treasurer.daily_cap_usd > 0:
            fractions.append(day_cost / self.cfg.treasurer.daily_cap_usd)
        if self.cfg.treasurer.weekly_cap_usd > 0:
            fractions.append(week_cost / self.cfg.treasurer.weekly_cap_usd)
        return max(fractions) if fractions else 0.0

    # -- steering the ladder ----------------------------------------------

    def ceiling(self, job: str, today: date | None = None) -> str:
        """The highest rung this job may use right now. Normally [ladder].max_rung; once the budget
        passes the downgrade threshold it drops to the job's floor to save money (Sonnet for
        code/risky, Haiku otherwise)."""
        top = self.cfg.ladder.max_rung
        if self.pressure(today) < self.cfg.treasurer.downgrade_at:
            return top
        floor = ladder.floor_for(job)
        return ladder.LADDER[min(ladder.index(top), ladder.index(floor))]

    def should_warn(self, today: date | None = None) -> bool:
        """True once per day when spending crosses the warn threshold of an active cap."""
        today = today or datetime.now().date()
        cfg = self.cfg.treasurer
        if cfg.daily_cap_usd <= 0 and cfg.weekly_cap_usd <= 0:
            return False
        return self.pressure(today) >= cfg.warn_at and self._warned_day != today.isoformat()

    def mark_warned(self, today: date | None = None) -> None:
        self._warned_day = (today or datetime.now().date()).isoformat()


def _as_date(iso: str) -> date:
    return datetime.strptime(iso, "%Y-%m-%d").date()


def daily_line(days: dict[str, dict[str, float]], rungs: dict[str, dict[str, int]], iso: str) -> str:
    """The one-line daily total for `iso`: dollars, turns, and turns per rung."""
    d = days.get(iso, {})
    turns = d.get("turns", 0)
    line = f"{iso}: {turns:.0f} turns, ${d.get('cost', 0.0):.2f}"
    by_rung = rungs.get(iso, {})
    if by_rung:
        parts = [f"{r} {by_rung[r]}" for r in RUNG_ORDER if by_rung.get(r)]
        parts += [f"{r} {n}" for r, n in sorted(by_rung.items()) if r not in RUNG_ORDER]
        line += " | rungs: " + ", ".join(parts)
    return line


def main() -> int:
    from .config import load_config

    path = load_config().logs_dir / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    days, rungs = summarize(lines), rung_counts(lines)
    for iso in sorted(set(days) | set(rungs)):
        print(daily_line(days, rungs, iso))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
