import pytest

from assistant.herald import Route, Rung, looks_like_non_answer, route


@pytest.mark.parametrize("text, rung", [
    # Plain talk / facts -> the Steward may try.
    ("what is the capital of France", Rung.STEWARD),
    ("how many continents are there", Rung.STEWARD),
    ("who wrote Hamlet", Rung.STEWARD),
    ("tell me a joke", Rung.STEWARD),
    ("what does serendipity mean", Rung.STEWARD),
    ("thanks, that's helpful", Rung.STEWARD),
    # Risky: money, logins, deletes, sending -> Claude.
    ("send an email to my boss", Rung.CLAUDE),
    ("delete the old backups", Rung.CLAUDE),
    ("log in to my bank account", Rung.CLAUDE),
    ("what's my password for GitHub", Rung.CLAUDE),
    ("push the branch and deploy", Rung.CLAUDE),
    ("how much did I pay for this", Rung.CLAUDE),
    # Code / files / machine -> Claude needs its tools.
    ("why is this test failing", Rung.CLAUDE),
    ("refactor the config module", Rung.CLAUDE),
    ("open the python file and fix the bug", Rung.CLAUDE),
    ("what's in the logs folder", Rung.CLAUDE),
    # Screen -> a screenshot turn is a Claude turn.
    ("look at this error", Rung.CLAUDE),
    ("what's on my screen", Rung.CLAUDE),
    ("explain this", Rung.CLAUDE),
    # Actions: only Claude can act on the PC.
    ("open Chrome", Rung.CLAUDE),
    ("mute the TV", Rung.CLAUDE),
    ("maximize this window", Rung.CLAUDE),
    ("turn the volume down", Rung.CLAUDE),
    ("remind me at five", Rung.CLAUDE),
])
def test_routing(text, rung):
    assert route(text).rung is rung


def test_route_returns_a_reason():
    r = route("what is the capital of France")
    assert isinstance(r, Route) and r.rung is Rung.STEWARD and r.reason == "simple"
    assert route("delete everything").reason == "risky"
    assert route("open Chrome").reason == "action"


def test_empty_text_goes_to_claude():
    assert route("   ").rung is Rung.CLAUDE


@pytest.mark.parametrize("answer", [
    "",
    "   ",
    "I can't help with that.",
    "I'm not sure, you may want to check online.",
    "I don't have access to real-time information.",
    "As an AI language model, I cannot browse the web.",
    "I need to check that.",
])
def test_non_answers_trigger_fallback(answer):
    assert looks_like_non_answer(answer) is True


@pytest.mark.parametrize("answer", [
    "The capital of France is Paris.",
    "There are seven continents.",
    "Hamlet was written by William Shakespeare.",
])
def test_real_answers_pass(answer):
    assert looks_like_non_answer(answer) is False
