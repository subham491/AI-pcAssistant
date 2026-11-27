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

    async def connect(self):
        pass

    async def run_turn(self, turn, on_delta):
        self.turns.append(turn)
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
