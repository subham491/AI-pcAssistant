import pytest

from assistant.steps import DONE_LINGER_S, FADE_S, FAIL_LINGER_S, MAX_PUFFS, Step, Trail, describe


@pytest.mark.parametrize("tool, inp, kind, label", [
    ("Read", {"file_path": r"C:\Users\me\notes\todo.md"}, "read", "todo.md"),
    ("Read", {"file_path": "/home/me/notes/todo.md"}, "read", "todo.md"),
    ("Edit", {"file_path": "src/app.py", "old_string": "a", "new_string": "b"}, "edit", "app.py"),
    ("Grep", {"pattern": "TODO", "path": "notes"}, "search", "“TODO”"),
    ("Glob", {"pattern": "**/*.py"}, "search", "“**/*.py”"),
    ("Bash", {"command": "git log --oneline", "description": "Check recent commits"}, "run", "Check recent commits"),
    ("Bash", {"command": "npm test"}, "run", "npm test"),
    ("WebFetch", {"url": "https://docs.python.org/3/library/asyncio.html", "prompt": "x"}, "web", "docs.python.org"),
    ("WebSearch", {"query": "pyside6 click through window"}, "web", "“pyside6 click through window”"),
    ("Task", {"description": "Review the diff", "prompt": "..."}, "ask", "Review the diff"),
    ("TodoWrite", {"todos": [{}, {}, {}]}, "plan", "3 steps"),
    ("mcp__desktop__screenshot", {}, "screen", "your screen"),
    ("mcp__desktop__press_keys", {"keys": "ctrl+s"}, "control", "press keys: ctrl+s"),
    ("mcp__github__list_issues", {"repo": "a/b"}, "search", "Github: a/b"),
    ("mcp__claude_ai_Gmail__search_threads", {"query": "category:updates older_than:1y"}, "search",
     "Gmail: category:updates older_than:1y"),
    ("mcp__claude_ai_Google_Calendar__create_event", {}, "edit", "Google Calendar: event"),
    ("mcp__notes__summarize", {}, "other", "Notes: summarize"),
])
def test_describe_names_the_step_in_plain_words(tool, inp, kind, label):
    step = describe("id1", tool, inp)
    assert (step.id, step.kind, step.label) == ("id1", kind, label)
    assert step.reaction.endswith("…")


def test_describe_survives_odd_input_and_keeps_labels_short():
    assert describe("x", "Bash", None).label == "a command"
    assert describe("x", "Read", {"file_path": 7}).label == "7"
    assert describe("x", "SomethingNew", {"a": ["list"]}).kind == "other"
    long = describe("x", "Bash", {"command": "echo " + "word " * 80})
    assert len(long.label) <= 48 and long.label.endswith("…") and "\n" not in long.detail


def step(i, kind="read"):
    return Step(f"s{i}", kind, f"file{i}.py")


def test_trail_shows_newest_full_and_older_as_small_chips():
    trail = Trail()
    trail.thinking(0.0)
    assert [(p.reaction, p.label) for p in trail.puffs(1.0)] == [("hmm…", "")]
    for i in range(6):
        trail.start(step(i), 1.0 + i)
        trail.finish(f"s{i}", True, "", 1.5 + i) if i < 5 else None
    puffs = trail.puffs(7.0)
    assert len(puffs) == MAX_PUFFS and [p.slot for p in puffs] == [0, 1, 2, 3]
    newest = puffs[0]
    assert (newest.label, newest.status, newest.compact, newest.reaction) == ("file5.py", "running", False, "reading…")
    assert all(p.compact and p.status == "done" and p.reaction == "" for p in puffs[1:])
    assert puffs[1].opacity > puffs[3].opacity > 0
    assert trail.current_kind() == "read"
    assert not any(p.key == "__think__" for p in puffs)  # the "hmm" puff left when real work began


def test_new_puff_rises_out_of_the_head():
    trail = Trail()
    trail.start(step(1), 10.0)
    early, late = trail.puffs(10.1)[0], trail.puffs(11.5)[0]
    assert early.rise < 0.2 and early.opacity < 1 and late.rise == 1.0 and late.opacity == 1.0


def test_same_step_reported_twice_is_one_puff():
    trail = Trail()
    trail.start(step(1), 0.0)
    trail.start(step(1), 0.1)  # stream and hook both report it
    assert len(trail.puffs(1.0)) == 1 and trail.count == 1


def test_failed_step_stays_readable_then_goes():
    trail = Trail()
    trail.start(step(1, "run"), 0.0)
    trail.finish("s1", False, "fatal: not a git repository\nmore", 1.0)
    trail.speaking(1.2)
    p = trail.puffs(1.2 + FADE_S + 1)[0]  # everything else has faded by now
    assert p.status == "failed" and p.note.startswith("fatal: not a git repository") and p.opacity == 1.0
    assert trail.puffs(1.0 + FAIL_LINGER_S + 0.1) == []
    assert trail.current_kind() is None


def test_speaking_fades_the_puffs_and_done_shows_a_summary_for_a_moment():
    trail = Trail()
    trail.thinking(0.0)
    trail.start(step(1), 1.0)
    trail.finish("s1", True, "", 2.0)
    trail.start(step(2), 2.0)  # no finish event ever arrives (e.g. a declined approval)
    trail.speaking(3.0)
    assert trail.current_kind() is None  # ...so it must not keep spinning
    assert 0 < trail.puffs(3.0 + FADE_S / 2)[0].opacity < 1
    assert trail.puffs(3.0 + FADE_S + 0.1) == []
    trail.turn_done(True, 11.0)
    done = trail.puffs(11.5)
    assert [(p.status, p.reaction, p.label) for p in done] == [("summary", "all done", "2 steps · 11s")]
    assert not trail.active(11.0 + DONE_LINGER_S + 0.1)


def test_no_summary_for_a_failed_turn_or_a_turn_without_steps():
    trail = Trail()
    trail.thinking(0.0)
    trail.turn_done(True, 2.0)  # just an answer, no tools
    assert not trail.active(2.0)
    trail.thinking(3.0)
    trail.start(step(1), 3.5)
    trail.turn_done(False, 5.0)
    assert all(p.status != "summary" for p in trail.puffs(5.1))


def test_next_turn_starts_clean_and_long_turns_stay_bounded():
    trail = Trail()
    trail.thinking(0.0)
    for i in range(40):
        trail.start(step(i), 1.0 + i)
        trail.finish(f"s{i}", True, "", 1.5 + i)
    assert len(trail.entries) <= 12 and trail.count == 40
    trail.turn_done(True, 50.0)
    trail.thinking(60.0)
    assert trail.count == 0 and [p.reaction for p in trail.puffs(61.0)] == ["hmm…"]
