import pytest

from assistant.config import EditMode
from assistant.intents import Kind, TargetKind, parse, screen_target, strip_wake_phrase


@pytest.mark.parametrize("text, rest", [
    ("Hellbot, what time is it?", "what time is it?"),
    ("Hell bot open notepad", "open notepad"),  # Whisper may split or misspell the word
    ("Helbot stop!", "stop!"),
    ("Hello bot what's up", "what's up"),
    ("Hellbot.", ""),  # just the wake phrase: the app then listens for the command
    ("What are you doing, Hellbot?", "What are you doing"),  # the name can close the sentence too
    ("Tell Hellbot to stop now", None),  # ...but talking about it mid-sentence is not calling it
    ("I said it three times, check the clips.", None),
    ("Hello, how are you doing today?", None),  # close, but not the wake phrase
    ("Can you help me with the report?", None),
    ("", None),
])
def test_strip_wake_phrase(text, rest):
    assert strip_wake_phrase(text, "hellbot") == rest


def test_strip_wake_phrase_with_several_words():
    assert strip_wake_phrase("Hey computer, do it", "hey computer") == "do it"
    assert strip_wake_phrase("Hey there", "hey computer") is None


@pytest.mark.parametrize("text, kind", [
    ("Stop.", Kind.STOP), ("stop that", Kind.STOP), ("Hey Jarvis, stop", Kind.STOP),
    ("Apply", Kind.APPROVE), ("yes", Kind.APPROVE), ("go ahead", Kind.APPROVE),
    ("Reject.", Kind.REJECT), ("no", Kind.REJECT),
    ("undo that", Kind.UNDO), ("start over", Kind.NEW_SESSION),
    ("remote mode", Kind.REMOTE_ON), ("exit remote mode", Kind.REMOTE_OFF),
    ("pull her project", Kind.PULL_PROJECT), ("Pull the project.", Kind.PULL_PROJECT),
    ("stop the server on port 3000", Kind.TURN),  # contains "stop" but is a real request
    ("why is this test failing", Kind.TURN),
])
def test_kinds(text, kind):
    assert parse(text).kind is kind


@pytest.mark.parametrize("text, mode", [
    ("suggest only mode", EditMode.SUGGEST), ("switch to suggest mode", EditMode.SUGGEST),
    ("review mode", EditMode.REVIEW), ("auto apply mode", EditMode.AUTO), ("use auto mode", EditMode.AUTO),
])
def test_edit_modes(text, mode):
    intent = parse(text)
    assert intent.kind is Kind.SET_EDIT_MODE and intent.edit_mode is mode


def test_remember_keeps_original_casing():
    intent = parse("Hey Jarvis, remember that my sister's repo uses Node 20.")
    assert intent.kind is Kind.REMEMBER
    assert intent.text == "my sister's repo uses Node 20"


@pytest.mark.parametrize("text, kind, which", [
    ("what's the capital of France", TargetKind.NONE, ""),
    ("look at this error", TargetKind.FOREGROUND_WINDOW, ""),
    ("explain this", TargetKind.FOREGROUND_WINDOW, ""),
    ("what's on this screen", TargetKind.FOREGROUND_MONITOR, ""),
    ("check the left screen", TargetKind.MONITOR, "left"),
    ("look at the right monitor", TargetKind.MONITOR, "right"),
    ("read the main screen", TargetKind.MONITOR, "main"),
    ("no, the other one", TargetKind.MONITOR, "other"),
    ("compare both screens", TargetKind.ALL_MONITORS, ""),
    ("look at the screen with Teams", TargetKind.APP, "teams"),
])
def test_screen_targets(text, kind, which):
    t = screen_target(text)
    assert t.kind is kind and t.which == which
