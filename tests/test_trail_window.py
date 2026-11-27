import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from assistant.steps import Puff, describe  # noqa: E402
from assistant.ui import trail as trail_mod  # noqa: E402
from assistant.ui.orb import Orb  # noqa: E402
from assistant.ui.trail import HEIGHT, WIDTH, TrailWindow, puff_centre, puff_parts  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def puff(**kw):
    base = dict(key="k", slot=0, status="running", tag="READ", reaction="reading…", label="todo.md",
                compact=False, opacity=1.0, rise=1.0, age=2.0)
    return Puff(**{**base, **kw})


def test_puff_text_for_each_state():
    assert [t for t, _c, _s in puff_parts(puff())] == ["READ", "reading…", "todo.md"]
    assert [t for t, _c, _s in puff_parts(puff(tag="", reaction="hmm…", label=""))] == ["hmm…"]
    assert [t for t, _c, _s in puff_parts(puff(status="done", compact=True, slot=1))] == ["✓", "todo.md"]
    assert [t for t, _c, _s in puff_parts(puff(status="failed", note="no such file"))] == ["✗", "todo.md", "no such file"]
    assert [t for t, _c, _s in puff_parts(puff(status="summary", reaction="all done", label="3 steps · 11s"))][1:] \
        == ["all done", "3 steps · 11s"]


def test_puffs_stack_upwards_from_the_head_and_downwards_when_flipped():
    ys = [puff_centre(slot, 1.0, 0.0, False)[1] for slot in range(4)]
    assert ys == sorted(ys, reverse=True) and 0 < ys[-1] and ys[0] < HEIGHT  # slot 0 lowest, all inside the window
    flipped = [puff_centre(slot, 1.0, 0.0, True)[1] for slot in range(4)]
    assert flipped == sorted(flipped) and flipped[-1] < HEIGHT
    assert puff_centre(0, 0.0, 0.0, False)[1] > puff_centre(0, 1.0, 0.0, False)[1]  # it rises into place
    assert puff_centre(0, 1.0, 0.0, False)[0] == WIDTH / 2


def test_window_never_takes_the_mouse_and_only_shows_while_there_is_something_to_say(app, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(trail_mod.time, "monotonic", lambda: clock[0])
    orb = Orb("Hellbot", "Ctrl+Alt+Space")
    orb.move(400, 500)
    win = TrailWindow(orb)
    assert win.windowFlags() & Qt.WindowTransparentForInput and win.windowFlags() & Qt.WindowDoesNotAcceptFocus
    assert not win.isVisible()

    win.on_status("thinking", "Thinking…")
    win.on_step_started(describe("t1", "Read", {"file_path": "notes/todo.md"}))
    assert win.isVisible() and orb._kind == "read"
    assert win.y() + HEIGHT <= orb.y() + orb.height() // 2  # above the orb
    win.grab()  # paints without error
    win.on_step_finished("t1", True, "")
    assert orb._kind is None

    win.on_turn_finished(True)
    clock[0] += 60
    win._tick()
    assert not win.isVisible() and not win._timer.isActive()

    orb.move(400, 0)  # no room above: the trail hangs below the orb instead
    win.on_status("thinking", "Thinking…")
    assert win._flipped and win.y() > orb.y()
    win.on_status("listening", "Listening…")  # a new request wipes the old trail
    assert not win.trail.active(clock[0])
