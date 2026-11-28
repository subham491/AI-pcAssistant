import asyncio

import pytest

from assistant.brain import TurnResult
from assistant.config import EditMode
from assistant.orchestrator import Orchestrator


class RecSink:
    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        return lambda *a: self.events.append((name, a))


class RecSpeaker:
    speaking = False

    def __init__(self):
        self.said, self.stopped = [], 0

    def say(self, text):
        self.said.append(text)

    def stop(self):
        self.stopped += 1


class FakeCapturer:
    def __init__(self):
        self.targets = []

    def capture(self, target):
        self.targets.append(target)
        return []


class FakeBrain:
    def __init__(self, reply="Done. All good.", hang=False):
        self.reply, self.hang = reply, hang
        self.turns, self.interrupted, self.closed, self.fresh = [], 0, 0, 0
        self.models = []

    async def connect(self):
        pass

    async def run_turn(self, turn, on_delta, model=None):
        self.turns.append(turn)
        self.models.append(model)
        if self.hang:
            await asyncio.sleep(3600)
        on_delta(self.reply)
        return TurnResult(self.reply)

    async def interrupt(self):
        self.interrupted += 1

    async def close(self):
        self.closed += 1

    async def new_session(self):
        self.fresh += 1

    async def set_full_profile(self, full):
        self.full_profile = full
        return True


def make(cfg, brain=None, fg="Code.exe"):
    brains = {}

    def factory(_orch, profile):
        brains[profile] = brain or FakeBrain()
        return brains[profile]

    speaker, cap = RecSpeaker(), FakeCapturer()
    orch = Orchestrator(cfg, RecSink(), speaker, cap, brain_factory=factory, foreground_process=lambda: fg)
    return orch, speaker, cap, brains


async def started(orch):
    orch.loop = asyncio.get_running_loop()
    return orch


def test_turn_speaks_reply_and_captures_on_look_phrase(cfg):
    async def go():
        orch, speaker, cap, brains = make(cfg)
        await started(orch)
        await orch.handle_text("look at this error")
        await orch._turn_task
        assert brains["local"].turns[0].text == "look at this error"
        assert cap.targets and cap.targets[0].kind.value == "foreground_window"
        assert speaker.said == ["Done. All good."]
    asyncio.run(go())


def test_rdp_in_front_switches_to_remote_profile(cfg):
    async def go():
        orch, _, cap, brains = make(cfg, fg="mstsc.exe")
        await started(orch)
        await orch.handle_text("what is the capital of France")
        await orch._turn_task
        assert "remote" in brains and orch.policy.remote_mode
        assert cap.targets  # remote mode always looks at the screen
    asyncio.run(go())


def test_control_words_never_reach_claude(cfg):
    async def go():
        orch, speaker, _, brains = make(cfg)
        await started(orch)
        await orch.handle_text("suggest only mode")
        assert orch.policy.edit_mode is EditMode.SUGGEST
        await orch.handle_text("remember that I prefer pytest")
        assert "I prefer pytest" in (cfg.memory_dir / "about-me.md").read_text()
        await orch.handle_text("undo that")
        assert any("nothing to undo" in s for s in speaker.said)
        assert brains == {}
    asyncio.run(go())


def test_voice_apply_resolves_pending_approval(cfg):
    async def go():
        orch, _, _, _ = make(cfg)
        await started(orch)
        from assistant.ui.sink import ApprovalRequest
        waiter = asyncio.create_task(orch.approve(ApprovalRequest("x", "Run command", 2, "r", "npm test")))
        await asyncio.sleep(0)
        await orch.handle_text("apply")
        assert await waiter is True
    asyncio.run(go())


def test_yes_without_pending_approval_is_a_normal_turn(cfg):
    async def go():
        orch, _, _, brains = make(cfg)
        await started(orch)
        await orch.handle_text("yes")
        await orch._turn_task
        assert brains["local"].turns[0].text == "yes"
    asyncio.run(go())


def test_approval_timeout_rejects(cfg):
    cfg.safety.approval_timeout_s = 0.05

    async def go():
        orch, _, _, _ = make(cfg)
        await started(orch)
        from assistant.ui.sink import ApprovalRequest
        assert await orch.approve(ApprovalRequest("x", "Edit a.py", 1, "r", "diff")) is False
    asyncio.run(go())


def test_stop_interrupts_and_hard_stops_a_hung_turn(cfg, monkeypatch):
    killed = []
    monkeypatch.setattr("assistant.orchestrator.kill_child_processes", lambda: killed.append(1) or 0)
    monkeypatch.setattr("assistant.orchestrator.control.release_all", lambda: None)

    async def go():
        brain = FakeBrain(hang=True)
        orch, speaker, _, _ = make(cfg, brain=brain)
        await started(orch)
        await orch.handle_text("refactor everything")
        await asyncio.sleep(0.01)
        assert orch.busy
        await orch.handle_text("busy question")  # refused while busy
        assert any("still on the last" in s for s in speaker.said)
        await orch.handle_text("stop")
        assert brain.interrupted == 1 and speaker.stopped >= 1
        assert killed and brain.closed == 1 and not orch.busy
    asyncio.run(go())


def statuses(orch):
    return [a[0] for name, a in orch.ui.events if name == "status"]


def test_status_goes_ready_right_away_when_nothing_is_being_spoken(cfg):
    async def go():
        orch, _, _, _ = make(cfg)
        await started(orch)
        await orch.handle_text("what is two plus two")
        await orch._turn_task
        assert statuses(orch)[-2:] == ["speaking", "idle"]
        assert ("turn_finished", (True,)) in orch.ui.events
        assert orch._settling is None
    asyncio.run(go())


def test_status_stays_speaking_until_the_voice_is_done(cfg):
    async def go():
        orch, speaker, _, _ = make(cfg)
        await started(orch)
        speaker.speaking = True  # the voice is still reading the answer when Claude finishes
        await orch.handle_text("tell me a long story")
        await orch._turn_task
        assert not orch.busy  # the turn is over: a new request would be accepted
        await asyncio.sleep(0.15)
        assert statuses(orch)[-1] == "speaking" and not any(n == "turn_finished" for n, _ in orch.ui.events)
        speaker.speaking = False
        await asyncio.sleep(0.25)
        assert statuses(orch)[-1] == "idle" and orch.ui.events[-2] == ("turn_finished", (True,))
    asyncio.run(go())


def test_new_request_while_still_speaking_is_not_overwritten_by_the_old_ready(cfg):
    async def go():
        orch, speaker, _, brains = make(cfg)
        await started(orch)
        speaker.speaking = True
        await orch.handle_text("first question")
        await orch._turn_task
        brains["local"].hang = True
        await orch.handle_text("second question")  # barge-in
        speaker.speaking = False
        await asyncio.sleep(0.3)
        assert orch.busy and statuses(orch)[-1] == "thinking"  # not "idle" from the first answer
        await orch.stop()
    asyncio.run(go())


def _audit_events(cfg):
    import json
    path = cfg.logs_dir / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_steward_answers_simple_questions_without_claude(cfg, monkeypatch):
    cfg.brain.gemini_key_file = "key.txt"  # non-empty: the Steward is allowed to try
    calls = []
    monkeypatch.setattr("assistant.gemini.ask",
                        lambda *a, **k: calls.append(a) or "The capital of France is Paris.")

    async def go():
        orch, speaker, _, brains = make(cfg)
        await started(orch)
        await orch.handle_text("what is the capital of France")
        await orch._turn_task
        assert calls  # Gemini was consulted
        assert speaker.said == ["The capital of France is Paris."]
        assert brains == {}  # no Claude turn was spun up
        rungs = [e["rung"] for e in _audit_events(cfg) if e["event"] == "routed"]
        assert rungs == ["steward"]
    asyncio.run(go())


def test_steward_exchange_is_remembered_for_claude_and_logged(cfg, monkeypatch):
    cfg.brain.gemini_key_file = "key.txt"
    monkeypatch.setattr("assistant.gemini.ask", lambda *a, **k: "The capital of France is Paris.")

    async def go():
        orch, _, _, brains = make(cfg)
        await started(orch)
        await orch.handle_text("what is the capital of France")
        await orch._turn_task
        assert brains == {}
        log_text = (cfg.home / "notes" / "steward-log.md").read_text(encoding="utf-8")
        assert "Q: what is the capital of France" in log_text and "The capital of France is Paris." in log_text
        assert log_text.count("\n") == 1  # one line per exchange
        # The next Claude turn opens with it (no roll needed), tagged as not Claude's own answer, once.
        note = orch.carry.take("local")
        assert "what is the capital of France" in note and "Paris" in note and "Steward (Gemini" in note
        assert orch.carry.take("local") == ""
        # ...and it stays in the carry-over for a later session roll.
        orch.carry.mark_pending("local")
        assert "Steward (Gemini, not you): The capital of France is Paris." in orch.carry.take("local")
    asyncio.run(go())


def test_steward_miss_leaves_no_memory_or_log(cfg, monkeypatch):
    cfg.brain.gemini_key_file = "key.txt"
    monkeypatch.setattr("assistant.gemini.ask", lambda *a, **k: "I don't have access to that.")

    async def go():
        orch, _, _, _ = make(cfg)
        await started(orch)
        await orch.handle_text("what is the capital of France")
        await orch._turn_task
        assert not (cfg.home / "notes" / "steward-log.md").exists()
        assert orch.carry.take("local") == ""
    asyncio.run(go())


def test_start_resumes_a_pending_task_once(cfg, monkeypatch):
    from assistant.config import schedule_resume

    monkeypatch.setattr("assistant.orchestrator.prepare_auth_env", lambda mode: None)
    path = schedule_resume(cfg, "continue the kingdom plan\n")
    assert path == cfg.state_dir / "resume_task.txt"

    async def go():
        orch, speaker, _, brains = make(cfg)
        seen = []
        handle = orch.handle_text

        async def spy(text, source="voice"):
            seen.append((text, source, path.exists()))
            await handle(text, source)

        orch.handle_text = spy
        await orch.start()
        await orch._turn_task
        assert seen == [("continue the kingdom plan", "auto_resume", False)]  # deleted before dispatch
        assert brains["local"].turns[0].text == "continue the kingdom plan"
        assert speaker.said[0] == "Picking up where I left off."
        assert ("transcript", ("continue the kingdom plan",)) in orch.ui.events
    asyncio.run(go())


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_start_without_a_pending_task_is_unchanged(cfg, monkeypatch, content):
    monkeypatch.setattr("assistant.orchestrator.prepare_auth_env", lambda mode: None)
    if content is not None:
        cfg.state_dir.mkdir(parents=True)
        cfg.resume_task_file.write_text(content, encoding="utf-8")

    async def go():
        orch, speaker, _, _ = make(cfg)
        await orch.start()
        await asyncio.sleep(0.01)
        assert orch._turn_task is None and speaker.said == []
        assert not any(name == "transcript" for name, _ in orch.ui.events)
        assert statuses(orch) == ["idle"]
        assert not cfg.resume_task_file.exists()
    asyncio.run(go())


def test_steward_falls_back_to_claude_on_refusal(cfg, monkeypatch):
    cfg.brain.gemini_key_file = "key.txt"
    monkeypatch.setattr("assistant.gemini.ask", lambda *a, **k: "I don't have access to that.")

    async def go():
        orch, speaker, _, brains = make(cfg)
        await started(orch)
        await orch.handle_text("what is the capital of France")
        await orch._turn_task
        assert brains["local"].turns[0].text == "what is the capital of France"  # Claude handled it
        assert speaker.said == ["Done. All good."]
        # With no [brain].model pinned, a Claude turn lands on the default Knight rung, Sonnet.
        rungs = [e["rung"] for e in _audit_events(cfg) if e["event"] == "routed"]
        assert rungs == ["sonnet"]
    asyncio.run(go())


def test_steward_falls_back_to_claude_when_gemini_errors(cfg, monkeypatch):
    cfg.brain.gemini_key_file = "key.txt"
    from assistant import gemini

    def boom(*a, **k):
        raise gemini.GeminiError("offline")

    monkeypatch.setattr("assistant.gemini.ask", boom)

    async def go():
        orch, _, _, brains = make(cfg)
        await started(orch)
        await orch.handle_text("what is the capital of France")
        await orch._turn_task
        assert brains["local"].turns  # fell back to Claude
    asyncio.run(go())


def test_herald_routes_risky_and_code_turns_straight_to_claude(cfg, monkeypatch):
    cfg.brain.gemini_key_file = "key.txt"
    called = []
    monkeypatch.setattr("assistant.gemini.ask", lambda *a, **k: called.append(a) or "x")

    async def go():
        orch, _, _, brains = make(cfg)
        await started(orch)
        await orch.handle_text("delete the old backups")
        await orch._turn_task
        assert not called and brains["local"].turns  # Gemini never consulted; Claude handled it
    asyncio.run(go())


def test_no_gemini_key_means_no_steward(cfg, monkeypatch):
    assert cfg.brain.gemini_key_file == ""  # default: Steward disabled
    called = []
    monkeypatch.setattr("assistant.gemini.ask", lambda *a, **k: called.append(a) or "x")

    async def go():
        orch, _, _, brains = make(cfg)
        await started(orch)
        await orch.handle_text("what is the capital of France")
        await orch._turn_task
        assert not called and brains["local"].turns
    asyncio.run(go())


def test_full_profile_phrase_switches_all_brains_and_config(cfg):
    async def go():
        orch, speaker, _, brains = make(cfg)
        await started(orch)
        await orch.handle_text("what is the capital of France")  # spins up the local brain
        await orch._turn_task
        await orch.handle_text("full profile")
        assert cfg.brain.full_profile is True
        assert brains["local"].full_profile is True
        assert any("full profile" in s.lower() for s in speaker.said)
        await orch.handle_text("lean profile")
        assert cfg.brain.full_profile is False and brains["local"].full_profile is False
    asyncio.run(go())


class ScriptedBrain:
    """A brain whose reply depends on the model (rung) it is handed, to drive the ladder."""

    def __init__(self, script):
        self.script = script  # callable(model) -> TurnResult
        self.models, self.closed = [], 0

    async def connect(self):
        pass

    async def run_turn(self, turn, on_delta, model=None):
        self.models.append(model)
        result = self.script(model)
        on_delta(result.text)
        return result

    async def interrupt(self):
        pass

    async def close(self):
        self.closed += 1

    async def new_session(self):
        pass

    async def set_full_profile(self, full):
        return True


def test_ladder_retries_a_safe_hedge_one_rung_up(cfg):
    def script(model):
        if model is None:  # the default Sonnet rung hedges, with no side effects
            return TurnResult("I'm not sure about that.", changes=[])
        return TurnResult("It is 42.", changes=[])  # Opus answers

    async def go():
        orch, speaker, _, brains = make(cfg, brain=ScriptedBrain(script))
        await started(orch)
        await orch.handle_text("what is the meaning of life")  # a "simple" -> steward_fallback -> Claude turn
        await orch._turn_task
        assert brains["local"].models == [None, "opus"]  # climbed Sonnet -> Opus
        assert "It is 42." in speaker.said
        rungs = [e["rung"] for e in _audit_events(cfg) if e["event"] == "routed"]
        assert rungs == ["sonnet", "opus"]
    asyncio.run(go())


def test_a_changing_turn_in_doubt_is_not_redone_but_escalates_next_time(cfg):
    results = [TurnResult("tried but it broke", tool_failed=True, changes=["ran: build"]),
               TurnResult("fixed it", changes=[])]

    async def go():
        orch, _, _, brains = make(cfg, brain=ScriptedBrain(lambda m: results.pop(0) if results else TurnResult("x")))
        await started(orch)
        await orch.handle_text("fix the python traceback")  # a "code" turn that changed something
        await orch._turn_task
        assert brains["local"].models == [None]  # not silently re-run (it had side effects)
        assert orch._fix_failed is True
        await orch.handle_text("refactor the git branch logic")  # the next code turn starts at the Minister
        await orch._turn_task
        assert brains["local"].models[-1] == "opus"
    asyncio.run(go())


def test_budget_pressure_downgrades_a_risky_turn_and_warns(cfg):
    from datetime import datetime, timezone
    cfg.treasurer.daily_cap_usd = 1.0
    today = datetime.now(timezone.utc).isoformat()
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    (cfg.logs_dir / "audit.jsonl").write_text(
        __import__("json").dumps({"ts": today, "event": "turn_done", "cost_usd": 0.95,
                                  "session_cost_usd": 0.95}) + "\n", encoding="utf-8")

    async def go():
        orch, speaker, _, brains = make(cfg, brain=ScriptedBrain(lambda m: TurnResult("ok", changes=[])))
        await started(orch)
        await orch.handle_text("delete the old backups")  # risky: would start at Opus, capped to Sonnet
        await orch._turn_task
        # Downgraded from the risky Opus start to the Sonnet floor (logged as the rung; the passed
        # model is None because Sonnet is the unpinned default, so no needless set_model).
        rungs = [e["rung"] for e in _audit_events(cfg) if e["event"] == "routed"]
        assert rungs == ["sonnet"] and brains["local"].models == [None]
        assert any("budget cap" in s for s in speaker.said)
    asyncio.run(go())


def test_inspector_reviews_a_code_changing_turn(cfg, monkeypatch):
    cfg.brain.gemini_key_file = "key.txt"

    async def fake_review(c, request, changes):
        fake_review.seen = (request, list(changes))
        return "The retry has no backoff."
    monkeypatch.setattr("assistant.inspector.review", fake_review)

    async def go():
        brain = ScriptedBrain(lambda m: TurnResult("Added the retry.", changes=["Edit client.py\n+ retry"]))
        orch, speaker, _, _ = make(cfg, brain=brain)
        await started(orch)
        await orch.handle_text("refactor the git branch logic")  # a code turn
        await orch._turn_task
        assert fake_review.seen[1] == ["Edit client.py\n+ retry"]
        assert any(n == "show_code" and a[0] == "Inspector review (Gemini)" for n, a in orch.ui.events)
        assert "The retry has no backoff." in speaker.said
        assert any(e["event"] == "inspected" for e in _audit_events(cfg))
    asyncio.run(go())


def test_inspector_fails_soft_when_gemini_is_down(cfg, monkeypatch):
    cfg.brain.gemini_key_file = "key.txt"
    monkeypatch.setattr("assistant.inspector.review", lambda *a: _none())

    async def go():
        brain = ScriptedBrain(lambda m: TurnResult("done", changes=["ran: build"]))
        orch, speaker, _, _ = make(cfg, brain=brain)
        await started(orch)
        await orch.handle_text("run the test suite again")
        await orch._turn_task
        assert any(n == "activity" and "Inspector skipped" in a[0] for n, a in orch.ui.events)
        assert not any("show_code" == n for n, _ in orch.ui.events)
    asyncio.run(go())


async def _none():
    return None


def test_local_reply_shows_speaking_too(cfg):
    async def go():
        orch, speaker, _, _ = make(cfg)
        await started(orch)
        speaker.speaking = True
        await orch.handle_text("suggest only mode")
        await asyncio.sleep(0.15)
        assert statuses(orch)[-1] == "speaking"
        speaker.speaking = False
        await asyncio.sleep(0.25)
        assert statuses(orch)[-1] == "idle" and not any(n == "turn_finished" for n, _ in orch.ui.events)
    asyncio.run(go())
