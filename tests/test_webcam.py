import subprocess
from types import SimpleNamespace

import pytest

from assistant.desktop import webcam


def fake_run(stdout=b"", stderr=b"", returncode=0):
    return lambda *a, **k: SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_grab_splits_the_camera_name_from_the_jpeg(monkeypatch):
    # The JPEG itself may contain newline bytes: only the first one ends the name.
    monkeypatch.setattr(subprocess, "run", fake_run(stdout=b"OBSBOT Meet 2\n\xff\xd8\n\xff\xd9"))
    assert webcam.grab() == (b"\xff\xd8\n\xff\xd9", "OBSBOT Meet 2")


def test_grab_reports_the_last_line_the_camera_process_wrote(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(stderr=b"qt noise\nno camera matches 'x'; connected: none\n",
                                                    returncode=1))
    with pytest.raises(webcam.CameraError, match="no camera matches"):
        webcam.grab("x")


def test_grab_turns_a_hung_camera_into_a_camera_error(monkeypatch):
    def hang(*a, **k):
        raise subprocess.TimeoutExpired("cam", 20)
    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(webcam.CameraError, match="did not answer"):
        webcam.grab()
