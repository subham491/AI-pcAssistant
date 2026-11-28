"""What the assistant costs: per-day turns, dollars and tokens, read from the audit log.

Run `python -m assistant.usage`. Dollars are the SDK's estimate at API prices (entries
without `session_cost_usd` hold a running total per connection and are converted to per-turn here); on a Pro/Max login
they count against your plan limit instead of being billed. Turns logged before token tracking was
added have a cost but no token counts.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Iterable

TOKEN_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def turn_cost(total: float, seen: float) -> float:
    """One turn's cost from the SDK's running total. The total restarts at zero with a new
    connection or a conversation reset, so a drop means this turn's cost is the total itself."""
    return total - seen if total >= seen else total


def summarize(lines: Iterable[str]) -> dict[str, dict[str, float]]:
    days: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    seen: dict[str, float] = defaultdict(float)  # running total per profile, for old entries
    for line in lines:
        if '"turn_done"' not in line and '"session_connected"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        profile = rec.get("profile") or ""
        if rec.get("event") == "session_connected":
            seen[profile] = 0.0
            continue
        if rec.get("event") != "turn_done":
            continue
        day = days[rec["ts"][:10]]
        day["turns"] += 1
        cost = rec.get("cost_usd")
        if cost is not None and "session_cost_usd" not in rec:
            # Old entries logged the SDK's running total for the connection, not the turn's cost.
            seen[profile], cost = cost, turn_cost(cost, seen[profile])
        day["cost"] += cost or 0
        day["shots"] += rec.get("shots") or 0
        if rec.get("usage"):
            day["tracked"] += 1
            for k in TOKEN_KEYS:
                day[k] += rec["usage"].get(k) or 0
        if rec.get("context_tokens"):
            # The session's real size: what the turn's last model call read. `usage` above sums every
            # model call inside the turn, so it overstates turns that used many tools.
            day["sized"] += 1
            day["context_sum"] += rec["context_tokens"]
            day["context_max"] = max(day["context_max"], rec["context_tokens"])
    return days


def main() -> int:
    from .config import load_config

    path = load_config().logs_dir / "audit.jsonl"
    days = summarize(path.read_text(encoding="utf-8").splitlines()) if path.exists() else {}
    for date in sorted(days):
        d = days[date]
        line = f"{date}: {d['turns']:.0f} turns, ${d['cost']:.2f} (avg ${d['cost'] / d['turns']:.2f})"
        if d["tracked"]:
            ctx = (d["input_tokens"] + d["cache_creation_input_tokens"] + d["cache_read_input_tokens"]) / d["tracked"]
            line += (f"; {d['tracked']:.0f} turns with tokens: context avg {ctx / 1000:.0f}k in, "
                     f"{d['output_tokens'] / d['tracked']:.0f} out, {d['shots']:.0f} screenshots")
        if d["sized"]:
            line += (f"; session size avg {d['context_sum'] / d['sized'] / 1000:.0f}k, "
                     f"max {d['context_max'] / 1000:.0f}k")
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
