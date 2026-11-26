import pytest

from assistant.desktop.capture import (CaptureError, Monitor, choose_monitors, fit_scale, monitor_of,
                                       redaction_boxes, Shot)
from assistant.desktop.win32 import Rect, WindowInfo
from assistant.intents import ScreenTarget, TargetKind

# Right monitor is primary (common: laptop on the left, big screen primary on the right).
LEFT = Monitor(2, Rect(-1920, 0, 0, 1080), primary=False)
RIGHT = Monitor(1, Rect(0, 0, 2560, 1440), primary=True)
MONS = [RIGHT, LEFT]


def win(rect, proc="Code.exe"):
    return WindowInfo(1, "t", proc, 1, rect)


def test_left_right_by_position_not_index():
    assert choose_monitors(ScreenTarget(TargetKind.MONITOR, "left"), MONS, None)[0] == [LEFT]
    assert choose_monitors(ScreenTarget(TargetKind.MONITOR, "right"), MONS, None)[0] == [RIGHT]


def test_main_uses_primary_or_configured():
    assert choose_monitors(ScreenTarget(TargetKind.MONITOR, "main"), MONS, None)[0] == [RIGHT]
    assert choose_monitors(ScreenTarget(TargetKind.MONITOR, "main"), MONS, None, main_index=2)[0] == [LEFT]


def test_other_is_relative_to_focused_window():
    fg = win(Rect(-1800, 100, -200, 900))  # on LEFT
    assert choose_monitors(ScreenTarget(TargetKind.MONITOR, "other"), MONS, fg)[0] == [RIGHT]
    assert choose_monitors(ScreenTarget(TargetKind.FOREGROUND_MONITOR), MONS, fg)[0] == [LEFT]


def test_window_straddling_picks_larger_overlap():
    assert monitor_of(MONS, Rect(-300, 0, 1500, 800)) == RIGHT


def test_app_and_all():
    teams = win(Rect(-1900, 0, -100, 1000), "ms-teams.exe")
    mons, label = choose_monitors(ScreenTarget(TargetKind.APP, "teams"), MONS, None, find_app=lambda n: teams)
    assert mons == [LEFT] and "teams" in label
    with pytest.raises(CaptureError):
        choose_monitors(ScreenTarget(TargetKind.APP, "zoom"), MONS, None)
    assert choose_monitors(ScreenTarget(TargetKind.ALL_MONITORS), MONS, None)[0] == [LEFT, RIGHT]


def test_scale_and_click_mapping():
    assert fit_scale(1280, 720, 1568) == 1.0
    s = fit_scale(2560, 1440, 1280)
    assert s == 2.0
    shot = Shot("s1", "x", b"", Rect(-1920, 0, 0, 1080), 2.0, [], 960, 540)
    assert shot.to_screen(100, 50) == (-1720, 100)


def test_redaction_relative_to_region():
    region = Rect(0, 0, 2560, 1440)
    boxes = redaction_boxes(region, [win(Rect(100, 100, 500, 400), "KeePassXC.exe"), win(Rect(0, 0, 10, 10))],
                            ["keepassxc.exe"])
    assert boxes == [(Rect(100, 100, 500, 400), "KeePassXC.exe")]
