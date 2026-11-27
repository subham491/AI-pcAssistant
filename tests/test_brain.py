import asyncio

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from assistant.audit import AuditLog
from assistant.brain import (AuthError, Brain, SessionStore, Turn, build_user_message, prepare_auth_env)
from assistant.config import AuthMode, EditMode
from assistant.desktop.capture import Shot
from assistant.desktop.win32 import Rect
from assistant.diffs import UndoJournal
from assistant.policy import Policy
from assistant.ui.sink import NullSink


def test_subscription_mode_strips_api_key():
    env = {"ANTHROPIC_API_KEY": "sk-x", "PATH": "p"}
    prepare_auth_env(AuthMode.SUBSCRIPTION, env)
    assert "ANTHROPIC_API_KEY" not in env and env["PATH"] == "p"


def test_api_key_mode_requires_key():
    with pytest.raises(AuthError):
        prepare_auth_env(AuthMode.API_KEY, {})
    prepare_auth_env(AuthMode.API_KEY, {"ANTHROPIC_API_KEY": "sk"})


def test_user_message_has_images_then_text():
    shot = Shot("s3", "focused window: Code.exe", b"\x89PNG", Rect(0, 0, 10, 10), 1.0, ["KeePassXC.exe"], 10, 10)
    msg = build_user_message(Turn("why does this fail", [shot]), "[hdr]")
    content = msg["message"]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert content[-1]["type"] == "text"
    assert "[hdr]" in content[-1]["text"] and "s3" in content[-1]["text"] and "KeePassXC" in content[-1]["text"]
    assert content[-1]["text"].endswith("why does this fail")


def test_session_store_roundtrip(tmp_path):
    s = SessionStore(tmp_path / "st" / "sessions.json")
    assert s.get("local") is None
    s.set("local", "abc")
    s.set("remote", "def")
    s.set("remote", None)
    assert s.get("local") == "abc" and s.get("remote") is None


def make_brain(cfg, approve_result=True):
    policy = Policy(cfg, git_probe=lambda p: True)
    asked = []

    async def approve(req):
        asked.append(req)
        return approve_result

    brain = Brain(cfg, "local", policy, approve, NullSink(), AuditLog(cfg.logs_dir / "audit.jsonl"),
                  UndoJournal(), desktop_server=None, sessions=SessionStore(cfg.state_dir / "s.json"))
    return brain, policy, asked


def ctx(tool_use_id="t1"):
    return ToolPermissionContext(tool_use_id=tool_use_id)


def test_hook_decisions(cfg):
    brain, policy, _ = make_brain(cfg)

    async def go(name, inp):
        out = await brain._pre_tool_use({"tool_name": name, "tool_input": inp}, "t1", None)
        return out["hookSpecificOutput"]["permissionDecision"]

    assert asyncio.run(go("Read", {"file_path": "a"})) == "allow"
    assert asyncio.run(go("Bash", {"command": "npm i"})) == "ask"
    assert asyncio.run(go("Bash", {"command": "format C:"})) == "deny"
    assert (cfg.logs_dir / "audit.jsonl").read_text().count("tool_decision") == 3


def test_can_use_tool_shows_diff_and_records_undo(cfg):
    brain, policy, asked = make_brain(cfg)
    f = cfg.home / "a.py"
    f.write_text("x = 1\n")
    res = asyncio.run(brain._can_use_tool("Edit", {"file_path": str(f), "old_string": "1", "new_string": "2"}, ctx()))
    assert isinstance(res, PermissionResultAllow)
    assert asked[0].body_kind == "diff" and "+x = 2" in asked[0].body
    assert len(brain.undo) == 1


def test_can_use_tool_rejection_and_mode_change(cfg):
    brain, policy, asked = make_brain(cfg, approve_result=False)
    res = asyncio.run(brain._can_use_tool("Bash", {"command": "npm test"}, ctx()))
    assert isinstance(res, PermissionResultDeny) and "declined" in res.message
    policy.edit_mode = EditMode.SUGGEST  # switched by voice mid-turn
    res = asyncio.run(brain._can_use_tool("Write", {"file_path": str(cfg.home / "b.py"), "content": ""}, ctx()))
    assert isinstance(res, PermissionResultDeny) and "Suggest-only" in res.message


def test_remote_profile_has_no_builtin_tools(cfg):
    brain, _, _ = make_brain(cfg)
    brain.profile = "remote"
    opts = brain._options(None)
    assert opts.tools == [] and "Bash" in opts.disallowed_tools
    assert "REMOTE MODE" in opts.system_prompt["append"]
    assert opts.cwd == str(cfg.home)


class StepSink(NullSink):
    def __init__(self):
        self.events = []

    def step_started(self, step):
        self.events.append(("start", step.id, step.kind, step.label))

    def step_finished(self, step_id, ok, note):
        self.events.append(("finish", step_id, ok, note))


def test_steps_reach_the_ui_once_with_their_outcome(cfg):
    brain, _, _ = make_brain(cfg)
    brain.ui = sink = StepSink()

    async def go():
        read = {"tool_name": "Read", "tool_input": {"file_path": "notes/todo.md"}}
        brain._step_started("t1", "Read", read["tool_input"])  # the message stream saw it first
        await brain._pre_tool_use(read, "t1", None)  # ...then the hook: no second puff
        await brain._post_tool_use({**read, "tool_response": "ok"}, "t1", None)
        bash = {"tool_name": "Bash", "tool_input": {"command": "git log"}}
        await brain._pre_tool_use(bash, "t2", None)
        await brain._post_tool_failure({**bash, "error": "fatal: not a git repository\nhint: ..."}, "t2", None)
        await brain._pre_tool_use({"tool_name": "Bash", "tool_input": {"command": "format C:"}}, "t3", None)
        await brain._pre_tool_use({**read, "agent_id": "sub1"}, "t4", None)  # a helper agent's call: not shown
        await brain._post_tool_use(read, "t4", None)

    asyncio.run(go())
    assert sink.events == [
        ("start", "t1", "read", "todo.md"), ("finish", "t1", True, ""),
        ("start", "t2", "run", "git log"), ("finish", "t2", False, "fatal: not a git repository"),
        ("start", "t3", "run", "format C:"), ("finish", "t3", False, "blocked"),
    ]
    assert "tool_failed" in (cfg.logs_dir / "audit.jsonl").read_text()


def test_declined_approval_ends_its_step(cfg):
    brain, _, _ = make_brain(cfg, approve_result=False)
    brain.ui = sink = StepSink()

    async def go():
        await brain._pre_tool_use({"tool_name": "Bash", "tool_input": {"command": "npm test"}}, "t1", None)
        await brain._can_use_tool("Bash", {"command": "npm test"}, ctx("t1"))

    asyncio.run(go())
    assert sink.events[-1] == ("finish", "t1", False, "not approved")


def test_failure_hook_is_registered(cfg):
    brain, _, _ = make_brain(cfg)
    assert {"PreToolUse", "PostToolUse", "PostToolUseFailure"} <= set(brain._options(None).hooks)
