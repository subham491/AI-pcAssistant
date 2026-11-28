from assistant.watchdog import GRACE_S, MAX_TRIES, SETTLE_S, Watchdog


def make(running=False, quit_flag=False):
    calls = []
    wd = Watchdog(lambda: running, lambda: quit_flag, lambda: calls.append("launch"), lambda: calls.append("diagnose"))
    return wd, calls


def test_waits_out_the_grace_then_relaunches():
    wd, calls = make()
    assert wd.step(0) == "waiting" and wd.step(GRACE_S - 1) == "waiting"
    assert wd.step(GRACE_S) == "relaunched" and calls == ["launch"]


def test_deliberate_quit_is_left_alone():
    wd, calls = make(quit_flag=True)
    assert wd.step(0) == "quit" and wd.step(GRACE_S * 3) == "quit" and calls == []


def test_gives_up_and_asks_gemini_after_repeated_failures():
    wd, calls = make()
    wd.step(0)
    t = GRACE_S
    for _ in range(MAX_TRIES):
        assert wd.step(t) == "relaunched"
        assert wd.step(t + 1) == "waiting"
        t += SETTLE_S
    assert wd.step(t) == "gave up" and calls == ["launch"] * MAX_TRIES + ["diagnose"]


def test_running_app_resets_the_count():
    wd, _ = make()
    wd.step(0), wd.step(GRACE_S)
    wd.running = lambda: True
    assert wd.step(GRACE_S + 1) == "up" and wd.tries == 0 and wd.down_since is None
