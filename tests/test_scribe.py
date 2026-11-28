import asyncio

import pytest

from assistant import scribe
from assistant.config import Config


@pytest.fixture
def cfg(tmp_path):
    c = Config(home=tmp_path / "home")
    c.brain.gemini_key_file = "key.txt"
    return c


ITEMS = [
    {"user": "add a retry to fetch in client.py", "reply": "Added 3 retries to fetch in client.py."},
    {"user": "now write a test for it", "reply": "Working on test_client.py."},
]


def seq(*replies):
    """A fake gemini.ask that returns each reply in turn."""
    calls = list(replies)
    def fake(*a, **k):
        return calls.pop(0)
    return fake


def test_skips_without_items_or_key(cfg, monkeypatch):
    monkeypatch.setattr("assistant.gemini.ask", lambda *a, **k: "whatever")
    assert asyncio.run(scribe.summarize(cfg, [])).outcome == "skip"
    cfg.brain.gemini_key_file = ""
    assert asyncio.run(scribe.summarize(cfg, ITEMS)).outcome == "skip"


def test_good_summary_passes_on_the_first_try(cfg, monkeypatch):
    good = "The user is working in client.py, where fetch now has 3 retries. Next they want a test, test_client.py is in progress."
    monkeypatch.setattr("assistant.gemini.ask", seq(good))
    res = asyncio.run(scribe.summarize(cfg, ITEMS))
    assert res.outcome == "ok" and res.note == good


def test_a_bad_first_draft_is_rerun_and_the_good_rerun_is_kept(cfg, monkeypatch):
    # First draft invents a file (server.py) that the conversation never mentioned -> rejected.
    bad = "The user edited server.py and config.py for the deployment."
    good = "Work is in client.py: fetch got 3 retries; a test in test_client.py is next."
    monkeypatch.setattr("assistant.gemini.ask", seq(bad, good))
    res = asyncio.run(scribe.summarize(cfg, ITEMS))
    assert res.outcome == "rerun" and res.note == good


def test_two_bad_drafts_fall_back_to_the_raw_carryover(cfg, monkeypatch):
    truncated = "The user is working on client.py and wants to,"  # looks truncated
    invented = "They changed totally_unrelated.py."  # invents a file
    monkeypatch.setattr("assistant.gemini.ask", seq(truncated, invented))
    res = asyncio.run(scribe.summarize(cfg, ITEMS))
    assert res.outcome == "fallback" and res.note is None


def test_verify_catches_the_core_failures():
    ok, _ = scribe.verify("Good note about client.py with 3 retries; a test is next.", ITEMS)
    assert ok
    assert scribe.verify("", ITEMS)[0] is False  # empty
    assert scribe.verify("Working on client.py and then,", ITEMS)[0] is False  # truncated
    assert scribe.verify("They edited ghost.py instead.", ITEMS)[0] is False  # invented file
