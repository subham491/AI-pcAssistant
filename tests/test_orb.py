import numpy as np
import pytest

pytest.importorskip("PySide6")

from assistant.ui.orb import CX, CY, GLANCE_S, LOOK, LOOKS, R, SIZE, blobs_for, bob, eye_pose, render_smoke  # noqa: E402


def alpha(img):
    return img[..., 3].astype(int)


def test_smoke_is_solid_in_the_head_and_clear_at_the_window_edge():
    img = render_smoke(SIZE, 1.0, blobs_for("asleep", 0.0, 0.0, 5.0), (150, 120, 190), 0.16)
    assert img.shape == (SIZE, SIZE, 4) and img.dtype == np.uint8
    assert alpha(img)[int(CY), int(CX)] == 255
    # Fully transparent corners: Windows lets clicks through there, so the orb never blocks the desktop.
    assert alpha(img)[0, 0] == 0 and alpha(img)[-1, -1] == 0 and alpha(img)[-1, 0] == 0
    assert (img[..., :3].max(axis=2) <= img[..., 3]).all()  # premultiplied: no colour brighter than alpha


def test_hidpi_scale_renders_more_pixels_of_the_same_shape():
    blobs = blobs_for("asleep", 0.0, 0.0, 5.0)
    small, big = render_smoke(SIZE, 1.0, blobs, (0, 0, 0), 0.0), render_smoke(SIZE, 2.0, blobs, (0, 0, 0), 0.0)
    assert big.shape[0] == 2 * small.shape[0]
    assert (alpha(big) > 128).sum() == pytest.approx(4 * (alpha(small) > 128).sum(), rel=0.03)


def test_orb_swells_with_your_voice_while_listening():
    def area(level):
        return (alpha(render_smoke(SIZE, 1.0, blobs_for("listening", 0.0, level, 1.0), (255, 77, 58), 0.5)) > 128).sum()

    assert area(1.0) > 1.25 * area(0.0)


def test_answer_sends_one_puff_towards_the_card_then_settles():
    leaving = blobs_for("answer", 0.0, 0.0, 0.45)
    settled = blobs_for("answer", 0.0, 0.0, 2.0)
    assert len(leaving) == len(settled) + 1
    assert leaving[-1][0] < CX - R  # the puff is to the left of the head, where the card opens


def test_every_app_status_has_a_look_and_idle_means_asleep():
    for state in ("idle", "listening", "transcribing", "thinking", "speaking", "waiting", "error"):
        assert state in LOOKS
    assert LOOKS["idle"] == "asleep"


def test_eyes_show_the_kind_of_step_after_a_glance_up():
    assert eye_pose("thinking", "read", 1.0, 5.0, GLANCE_S / 2)[2] < -4  # first a look up at the new puff
    scan = [eye_pose("thinking", "read", t / 10, 5.0, 2.0)[1] for t in range(40)]
    assert min(scan) < -3 and max(scan) > 3  # reading: side to side
    assert len({eye_pose("thinking", "search", t / 10, 5.0, 2.0)[1] for t in range(40)}) >= 4  # searching: darting
    assert eye_pose("thinking", "run", 0.0, 5.0, 2.0)[4] < 0.5  # running a command: squint
    assert eye_pose("thinking", None, 0.0, 5.0, 2.0)[2] < -4  # plain thinking: looking up
    assert eye_pose("thinking", "brand-new-kind", 0.0, 5.0, 2.0)[2] < -4


def test_each_look_has_its_eye_shape_and_done_hops_once():
    assert [eye_pose(look, "read", 0.0, 1.0, 9.0)[0] for look in ("asleep", "error", "done", "answer", "listening")] \
        == ["closed", "flat", "happy", "ember", "ember"]
    assert "done" in LOOK and bob("done", 0.25) < -6 and bob("done", 1.0) == 0 and bob("thinking", 0.25) == 0
    assert blobs_for("done", 0.0, 0.0, 0.25)[0][1] < CY  # the head is in the air mid-hop


def test_head_keeps_its_own_puffs_in_while_the_trail_shows_steps():
    assert len(blobs_for("thinking", 0.0, 0.0, 1.0, trail=True)) < len(blobs_for("thinking", 0.0, 0.0, 1.0))
    assert len(blobs_for("asleep", 0.0, 0.0, 1.0, trail=True)) == len(blobs_for("asleep", 0.0, 0.0, 1.0))
