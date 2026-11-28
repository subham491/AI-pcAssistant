import asyncio

import pytest

from assistant import inspector
from assistant.config import Config


@pytest.fixture
def cfg(tmp_path):
    c = Config(home=tmp_path / "home")
    c.brain.gemini_key_file = "key.txt"
    return c


def test_no_review_without_changes_or_key(cfg, monkeypatch):
    calls = []
    monkeypatch.setattr("assistant.gemini.ask", lambda *a, **k: calls.append(a) or "x")
    assert asyncio.run(inspector.review(cfg, "do it", [])) is None  # nothing changed
    cfg.brain.gemini_key_file = ""
    assert asyncio.run(inspector.review(cfg, "do it", ["Edit a.py"])) is None  # Steward/Inspector off
    assert calls == []  # Gemini never consulted in either case


def test_review_returns_gemini_finding(cfg, monkeypatch):
    seen = {}
    def fake(prompt, key_file, model, timeout):
        seen["prompt"] = prompt
        return "  The new code ignores the error case.  "
    monkeypatch.setattr("assistant.gemini.ask", fake)
    out = asyncio.run(inspector.review(cfg, "harden the parser", ["Edit parser.py\n- old\n+ new"]))
    assert out == "The new code ignores the error case."
    # The prompt carries the user request and the diff, and nothing else (no screenshots/notes).
    assert "harden the parser" in seen["prompt"] and "parser.py" in seen["prompt"]


def test_review_fails_soft_when_gemini_errors(cfg, monkeypatch):
    from assistant import gemini
    def boom(*a, **k):
        raise gemini.GeminiError("offline")
    monkeypatch.setattr("assistant.gemini.ask", boom)
    assert asyncio.run(inspector.review(cfg, "do it", ["ran: rm -rf build"])) is None
