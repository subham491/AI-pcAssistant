import json

import pytest

from assistant.usage import summarize, turn_cost


def rec(ts, cost, usage=None, shots=0):
    return json.dumps({"ts": ts, "event": "turn_done", "cost_usd": cost, "usage": usage, "shots": shots})


def test_sums_per_day_and_tolerates_old_entries_without_tokens():
    lines = [
        rec("2026-11-27T10:00:00+00:00", 2.0),  # logged before token tracking
        rec("2026-11-27T11:00:00+00:00", 2.5, {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5}, 1),
        json.dumps({"ts": "2026-11-27T11:00:01+00:00", "event": "tool_result"}),
        "not json but mentions turn_done",
        rec("2026-11-28T09:00:00+00:00", 0.1, {"input_tokens": 1, "output_tokens": 2}),
    ]
    days = summarize(lines)
    d = days["2026-11-27"]
    assert (d["turns"], d["cost"], d["tracked"], d["shots"]) == (2, 2.5, 1, 1)
    assert d["input_tokens"] + d["cache_read_input_tokens"] == 100 and d["output_tokens"] == 5
    assert days["2026-11-28"]["turns"] == 1


def test_old_entries_are_running_totals_per_connection():
    connected = json.dumps({"ts": "2026-11-27T12:00:00+00:00", "event": "session_connected", "profile": None})
    lines = [
        rec("2026-11-27T10:00:00+00:00", 1.0),
        rec("2026-11-27T10:01:00+00:00", 1.5),
        rec("2026-11-27T10:02:00+00:00", None),  # failed turn: no cost, total carries on
        rec("2026-11-27T10:03:00+00:00", 1.5),
        connected,
        rec("2026-11-27T12:01:00+00:00", 2.0),  # higher than before, but a new connection
        rec("2026-11-27T12:02:00+00:00", 0.25),  # a drop without a connect line is a restart too
    ]
    d = summarize(lines)["2026-11-27"]
    assert d["turns"] == 6 and d["cost"] == pytest.approx(1.5 + 2.0 + 0.25)


def test_new_entries_already_hold_the_turn_cost():
    def new(ts, cost, total):
        return json.dumps({"ts": ts, "event": "turn_done", "cost_usd": cost, "session_cost_usd": total})

    lines = [new("2026-11-28T10:00:00+00:00", 0.25, 0.25), new("2026-11-28T10:01:00+00:00", 0.5, 0.75),
             rec("2026-11-28T10:02:00+00:00", 1.0)]  # an old entry after them still counts as a total
    assert summarize(lines)["2026-11-28"]["cost"] == pytest.approx(1.75)


def test_context_size_is_tracked_separately_from_summed_usage():
    def sized(ts, usage, context):
        return json.dumps({"ts": ts, "event": "turn_done", "cost_usd": 0.1, "usage": usage,
                           "context_tokens": context, "shots": 0})

    lines = [
        sized("2026-11-28T10:00:00+00:00", {"input_tokens": 200, "cache_read_input_tokens": 50_000}, 50_200),
        sized("2026-11-28T10:01:00+00:00", {"input_tokens": 100, "cache_read_input_tokens": 120_000}, 120_100),
        rec("2026-11-28T10:02:00+00:00", 0.1, {"input_tokens": 1, "output_tokens": 2}),  # no context field
    ]
    d = summarize(lines)["2026-11-28"]
    assert d["turns"] == 3 and d["tracked"] == 3  # every usage turn still counts for the old report
    assert d["sized"] == 2  # only the two with a context_tokens field
    assert d["context_sum"] == 170_300 and d["context_max"] == 120_100


def test_turn_cost():
    assert turn_cost(0.75, 0.25) == 0.5
    assert turn_cost(0.1, 0.75) == 0.1
