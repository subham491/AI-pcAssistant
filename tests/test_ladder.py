from dataclasses import dataclass, field

from assistant import ladder


@dataclass
class FakeResult:
    text: str = "Done."
    tool_failed: bool = False
    interrupted: bool = False
    changes: list = field(default_factory=list)


DEFAULT_JOBS = {j: "default" for j in ladder.JOBS}


def test_job_for_maps_reasons_and_falls_back_to_general():
    assert ladder.job_for("code") == "code"
    assert ladder.job_for("risky") == "risky"
    assert ladder.job_for("steward_fallback") == "general"
    assert ladder.job_for("empty") == "general"


def test_resolve_default_treats_unknowns_as_sonnet():
    assert ladder.resolve_default(None) == "sonnet"
    assert ladder.resolve_default("opus") == "opus"
    assert ladder.resolve_default("claude-sonnet-5-2026") == "sonnet"  # a dated id is not a named rung


def test_start_rung_default_table_preserves_the_base_model():
    # An all-"default" table runs everything on [brain].model (today's behaviour).
    assert ladder.start_rung("general", DEFAULT_JOBS, None) == "sonnet"
    assert ladder.start_rung("general", DEFAULT_JOBS, "haiku") == "haiku"
    assert ladder.start_rung("code", DEFAULT_JOBS, "opus") == "opus"


def test_risky_topic_starts_one_rung_up():
    assert ladder.start_rung("risky", DEFAULT_JOBS, None) == "opus"  # sonnet + 1
    assert ladder.start_rung("risky", DEFAULT_JOBS, "haiku") == "sonnet"


def test_a_failed_fix_jumps_risky_work_to_opus():
    assert ladder.start_rung("code", DEFAULT_JOBS, "haiku", failed_fix=True) == "opus"
    # but it never drags a non-risky job up
    assert ladder.start_rung("general", DEFAULT_JOBS, "haiku", failed_fix=True) == "haiku"


def test_config_table_overrides_the_base():
    jobs = {**DEFAULT_JOBS, "simple": "haiku", "code": "opus"}
    assert ladder.start_rung("simple", jobs, None) == "haiku"
    assert ladder.start_rung("code", jobs, None) == "opus"


def test_climb_and_clamp_respect_ceiling_and_floor():
    assert ladder.climb("sonnet") == "opus"
    assert ladder.climb("fable") == "fable"  # already at the top
    assert ladder.climb("sonnet", ceiling="sonnet") == "sonnet"  # ceiling blocks the climb
    # floor wins over ceiling, so code/risky work stays at Sonnet even under budget pressure
    assert ladder.clamp("opus", ceiling="haiku", floor="sonnet") == "sonnet"
    assert ladder.clamp("fable", ceiling="sonnet", floor="haiku") == "sonnet"
    assert ladder.clamp("haiku", ceiling="fable", floor="sonnet") == "sonnet"


def test_floor_for_job():
    assert ladder.floor_for("code") == "sonnet"
    assert ladder.floor_for("risky") == "sonnet"
    assert ladder.floor_for("action") == "sonnet"
    assert ladder.floor_for("general") == "haiku"
    assert ladder.floor_for("simple") == "haiku"


def test_resolve_model_keeps_the_sdk_default_when_nothing_is_pinned():
    assert ladder.resolve_model("sonnet", None) is None  # default + unpinned: don't force a name
    assert ladder.resolve_model("opus", None) == "opus"
    assert ladder.resolve_model("sonnet", "haiku") == "sonnet"  # a pinned base still gets switched


def test_climb_triggers_and_retry_safe():
    assert ladder.climb_triggers(FakeResult(text="Here is the answer.")) is None
    assert ladder.climb_triggers(FakeResult(tool_failed=True)) == "tool_failed"
    assert ladder.climb_triggers(FakeResult(text="I'm not sure I can help with that.")) == "hedge"
    assert ladder.climb_triggers(FakeResult(tool_failed=True, interrupted=True)) is None  # a stop is not doubt
    assert ladder.retry_safe(FakeResult(changes=[])) is True
    assert ladder.retry_safe(FakeResult(changes=["Edit a.py"])) is False
