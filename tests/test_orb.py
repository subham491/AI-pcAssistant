import numpy as np
import pytest

pytest.importorskip("PySide6")

from assistant.ui.orb import CX, CY, LOOKS, R, SIZE, blobs_for, render_smoke  # noqa: E402


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
