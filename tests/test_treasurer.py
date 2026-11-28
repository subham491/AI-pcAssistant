import json
from datetime import date

from assistant.config import Config
from assistant.treasurer import Treasurer, daily_line
from assistant.usage import rung_counts, summarize


def _turn(ts, cost, rung="sonnet"):
    return [
        json.dumps({"ts": ts, "event": "turn_done", "cost_usd": cost, "session_cost_usd": cost}),
        json.dumps({"ts": ts, "event": "routed", "rung": rung}),
    ]


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make(tmp_path, lines, **treasurer):
    cfg = Config(home=tmp_path / "home")
    for k, v in treasurer.items():
        setattr(cfg.treasurer, k, v)
    path = tmp_path / "audit.jsonl"
    _write(path, lines)
    return Treasurer(cfg, path), cfg


def test_spent_sums_day_and_trailing_week(tmp_path):
    lines = (_turn("2026-11-28T10:00:00+00:00", 1.0) + _turn("2026-11-28T11:00:00+00:00", 0.5)
             + _turn("2026-11-24T10:00:00+00:00", 2.0) + _turn("2026-11-10T10:00:00+00:00", 9.0))
    tr, _ = make(tmp_path, lines)
    day, week = tr.spent(date(2026, 11, 28))
    assert day == 1.5
    assert week == 1.5 + 2.0  # the Nov 10 turn is outside the trailing 7 days


def test_no_cap_means_no_pressure_and_full_ceiling(tmp_path):
    tr, _ = make(tmp_path, _turn("2026-11-28T10:00:00+00:00", 100.0))
    assert tr.pressure(date(2026, 11, 28)) == 0.0
    assert tr.ceiling("code", date(2026, 11, 28)) == "fable"
    assert tr.should_warn(date(2026, 11, 28)) is False


def test_downgrade_caps_the_ladder_but_never_below_sonnet_for_code(tmp_path):
    tr, _ = make(tmp_path, _turn("2026-11-28T10:00:00+00:00", 9.5), daily_cap_usd=10.0)
    today = date(2026, 11, 28)
    assert tr.pressure(today) == 0.95  # past downgrade_at (0.9)
    assert tr.ceiling("code", today) == "sonnet"   # risky floor holds
    assert tr.ceiling("risky", today) == "sonnet"
    assert tr.ceiling("general", today) == "haiku"  # cheap work may drop further


def test_below_downgrade_threshold_keeps_full_ceiling(tmp_path):
    tr, _ = make(tmp_path, _turn("2026-11-28T10:00:00+00:00", 5.0), daily_cap_usd=10.0)
    assert tr.ceiling("code", date(2026, 11, 28)) == "fable"  # 0.5 < downgrade_at


def test_warn_fires_once_per_day(tmp_path):
    tr, _ = make(tmp_path, _turn("2026-11-28T10:00:00+00:00", 8.5), daily_cap_usd=10.0)
    today = date(2026, 11, 28)
    assert tr.should_warn(today) is True  # 0.85 >= warn_at (0.8)
    tr.mark_warned(today)
    assert tr.should_warn(today) is False  # already warned today


def test_weekly_cap_also_drives_pressure(tmp_path):
    lines = _turn("2026-11-28T10:00:00+00:00", 1.0) + _turn("2026-11-25T10:00:00+00:00", 8.0)
    tr, _ = make(tmp_path, lines, weekly_cap_usd=10.0)
    assert tr.pressure(date(2026, 11, 28)) == 0.9
    assert tr.ceiling("code", date(2026, 11, 28)) == "sonnet"


def test_daily_line_shows_turns_per_rung(tmp_path):
    lines = (_turn("2026-11-28T10:00:00+00:00", 1.0, "sonnet")
             + _turn("2026-11-28T11:00:00+00:00", 0.1, "steward")
             + _turn("2026-11-28T12:00:00+00:00", 2.0, "opus"))
    days, rungs = summarize(lines), rung_counts(lines)
    line = daily_line(days, rungs, "2026-11-28")
    assert "2026-11-28" in line and "3 turns" in line and "$3.10" in line
    assert "steward 1" in line and "sonnet 1" in line and "opus 1" in line
