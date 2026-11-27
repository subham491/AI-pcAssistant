import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from assistant.ui import pill as pill_mod  # noqa: E402
from assistant.ui import speech as speech_mod  # noqa: E402
from assistant.ui.orb import Orb  # noqa: E402
from assistant.ui.pill import ActionPill  # noqa: E402
from assistant.ui.sink import ApprovalRequest  # noqa: E402
from assistant.ui.speech import MAX_WIDTH, SpeechBubble, side_for  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeOrch:
    def __init__(self):
        self.resolved, self.typed = [], []

    def resolve_approval_threadsafe(self, req_id, approved):
        self.resolved.append((req_id, approved))

    def submit_text_threadsafe(self, text, source="voice"):
        self.typed.append((text, source))


def make_orb():
    orb = Orb("Hellbot", "Ctrl+Alt+Space")
    orb.move(400, 300)
    return orb


def test_side_for_prefers_the_left_and_flips_when_there_is_no_room():
    assert side_for(1500, 1668, 440, 0, 1919) == "left"
    assert side_for(100, 268, 440, 0, 1919) == "right"
    assert side_for(2000, 2168, 440, 1920, 3839) == "right"  # second monitor: its own left edge counts


def test_bubble_never_takes_the_mouse_and_shows_only_while_there_are_words(app, monkeypatch):
    clock = [50.0]
    monkeypatch.setattr(speech_mod.time, "monotonic", lambda: clock[0])
    orb = make_orb()
    bubble = SpeechBubble(orb)
    flags = bubble.windowFlags()
    assert flags & Qt.WindowTransparentForInput and flags & Qt.WindowDoesNotAcceptFocus
    assert not bubble.isVisible()

    bubble.on_transcript("clean up my inbox")
    assert bubble.isVisible() and bubble.captions.you == "clean up my inbox"
    bubble.on_status("thinking", "Thinking…")
    clock[0] += 120
    bubble._tick()
    assert bubble.isVisible()  # your words wait for the answer

    long = "I found two hundred and twelve updates older than a year, mostly newsletters and receipts. " * 3
    bubble.on_spoken(long)
    assert bubble.captions.say and not bubble.captions.you
    assert bubble.width() <= MAX_WIDTH + 40 and bubble.height() > 80  # wraps instead of growing sideways
    bubble.grab()  # paints without error

    bubble.on_spoken(None)
    bubble.on_status("idle", "Ready")
    clock[0] += 10
    bubble._tick()
    assert not bubble.isVisible()


def test_bubble_moves_up_when_the_pill_is_showing(app):
    orb = make_orb()
    bubble = SpeechBubble(orb)
    bubble.on_spoken("Hello.")
    y = bubble.y()
    bubble.set_lift(60)
    assert bubble.y() == y - 60
    bubble.on_status("listening", "Listening…")  # a new request clears the last answer straight away
    assert not bubble.isVisible()


def test_pill_approval_buttons_answer_and_closing_means_no(app, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(pill_mod, "open_in_editor", opened.append)
    orch = FakeOrch()
    pill = ActionPill(make_orb(), orch, tmp_path / "snippets")
    heights = []
    pill.resized.connect(heights.append)
    assert not pill.isVisible()

    diff = "--- a/todo.md\n+++ b/todo.md\n@@\n-old\n+new\n"
    pill.on_approval(ApprovalRequest("a1", "Edit todo.md", "review", "review mode", diff, "diff"))
    assert pill.isVisible() and heights[-1] > 0
    assert pill.title.text() == "Edit todo.md?" and "+1 −1 lines" in pill.sub.text()
    assert pill.view_btn.isVisible() and pill.view_btn.text() == "View changes"
    pill.grab()

    pill.view_btn.click()
    assert opened and opened[0].suffix == ".diff" and opened[0].read_text(encoding="utf-8") == diff

    pill.primary.click()
    assert orch.resolved == [("a1", True)]
    pill.on_approval_closed("a1", "approved")
    assert not pill.isVisible() and heights[-1] == 0

    pill.on_approval(ApprovalRequest("a2", "Run command", "review", "why", "pip install x", "text"))
    assert not pill.view_btn.isVisible()  # a short command fits on the pill
    pill._dismiss()
    assert orch.resolved[-1] == ("a2", False)


def test_pill_hands_over_code_from_the_answer_and_copies_it(app, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(pill_mod, "open_in_editor", opened.append)
    pill = ActionPill(make_orb(), FakeOrch(), tmp_path / "snippets")
    pill.on_assistant("Here is the script.\n```python\nprint('hi')\n```\nRun it when ready.")
    assert pill.isVisible() and pill.primary.text() == "Copy" and pill.secondary.text() == "Open in editor"
    pill.primary.click()
    assert QApplication.clipboard().text().strip() == "print('hi')" and pill.primary.text() == "Copied ✓"
    pill.secondary.click()
    assert opened[0].suffix == ".py"
    pill.copy_last_answer()
    assert QApplication.clipboard().text().startswith("Here is the script.")

    pill.on_status("error", "Speech-to-text failed: no model")
    assert pill.title.text() == "Something went wrong" and pill.primary.text() == "OK"
    pill.primary.click()
    assert pill.primary.text() == "Copy"  # back to the code underneath
    pill.on_transcript("something else")  # a new request: old code is no longer the topic
    assert not pill.isVisible()


def test_typing_box_sends_what_you_type_and_closes(app, tmp_path):
    orch = FakeOrch()
    pill = ActionPill(make_orb(), orch, tmp_path / "snippets", name="Hellbot")
    pill.toggle_typing()
    assert pill.isVisible() and pill.input.isVisible() and not pill.title.isVisible()
    assert "Hellbot" in pill.input.placeholderText()
    pill.input.setText("  open my notes ")
    pill.input.returnPressed.emit()
    assert orch.typed == [("open my notes", "typed")] and not pill.isVisible()
    pill.toggle_typing()
    pill.toggle_typing()  # the hotkey again closes it
    assert not pill.isVisible() and orch.typed == [("open my notes", "typed")]
