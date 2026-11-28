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


def _grab_fake_screen(monkeypatch, region, remote=False):
    """Run the real ScreenCapturer._grab over a fake `region`-sized screen."""
    import sys
    import types

    from assistant.config import CaptureConfig
    from assistant.desktop import capture, win32

    class FakeSct:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def grab(self, box):
            size = (box["width"], box["height"])
            return types.SimpleNamespace(size=size, bgra=bytes(4 * size[0] * size[1]))

    monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(mss=FakeSct))
    monkeypatch.setattr(win32, "list_windows", lambda: [])
    capturer = capture.ScreenCapturer(CaptureConfig())
    capturer.remote = remote
    return capturer._grab(region, "test")


def test_downscaled_shot_clicks_the_right_screen_pixel(monkeypatch):
    import sys
    import types

    from assistant.desktop import control

    # A 2560x1440 monitor to the left of the primary one: the default cap halves it.
    region = Rect(-2560, 100, 0, 1540)
    shot = _grab_fake_screen(monkeypatch, region)
    assert (shot.width, shot.height, shot.scale) == (1280, 720, 2.0)

    clicks = []

    class FakeMouse:
        position = None

        def click(self, button, count):
            clicks.append((self.position, count))

    monkeypatch.setitem(sys.modules, "pynput", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pynput.mouse",
                        types.SimpleNamespace(Button=types.SimpleNamespace(left="l", right="r"), Controller=FakeMouse))
    monkeypatch.setattr(control.time, "sleep", lambda _s: None)

    assert control.click(shot, 640, 360) == (-1280, 820)  # image centre -> screen centre
    assert control.click(shot, 0, 0) == (-2560, 100)
    assert control.click(shot, 1280, 720) == (0, 1540)
    assert control.click(shot, 100.5, 50) == (-2359, 200)
    assert clicks[0] == ((-1280, 820), 1)
    with pytest.raises(control.ControlError):
        control.click(shot, 1281, 10)  # outside the downscaled image, even though inside the real screen


def test_remote_mode_keeps_more_detail_and_still_maps_clicks(monkeypatch):
    region = Rect(0, 0, 2560, 1440)
    shot = _grab_fake_screen(monkeypatch, region, remote=True)
    assert (shot.width, shot.height) == (1568, 882)
    assert shot.to_screen(784, 441) == (1280, 720)
    small = _grab_fake_screen(monkeypatch, Rect(0, 0, 1000, 700))  # under the cap: untouched, 1:1
    assert (small.width, small.height, small.scale) == (1000, 700, 1.0)


def test_redaction_relative_to_region():
    region = Rect(0, 0, 2560, 1440)
    boxes = redaction_boxes(region, [win(Rect(100, 100, 500, 400), "KeePassXC.exe"), win(Rect(0, 0, 10, 10))],
                            ["keepassxc.exe"])
    assert boxes == [(Rect(100, 100, 500, 400), "KeePassXC.exe")]
