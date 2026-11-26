import os
import shutil
import subprocess
import time
import zipfile

import pytest

from assistant.inbox import InboxSnapshotter, robocopy_command, tsclient_path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def old(path, seconds=100):
    t = time.time() - seconds
    for p in [path, *path.rglob("*")]:
        os.utime(p, (t, t))


def test_tsclient_path():
    assert tsclient_path(r"C:\Users\me\assistant\inbox\proj") == r"\\tsclient\C\Users\me\assistant\inbox\proj"
    with pytest.raises(ValueError):
        tsclient_path("relative/path")


def test_robocopy_command_reads_remote_writes_inbox(cfg):
    cfg.home = __import__("pathlib").PureWindowsPath(r"C:\Users\me\assistant")  # type: ignore[assignment]
    cmd = robocopy_command(cfg)
    assert cmd.startswith(f'robocopy "{cfg.remote.remote_project_path}" "\\\\tsclient\\C\\Users\\me\\assistant\\inbox\\')
    assert "/MIR" in cmd and '"node_modules"' in cmd and '".env"' in cmd


def test_settling_then_snapshot_and_change_summary(cfg):
    snap = InboxSnapshotter(cfg)
    copy = cfg.inbox_dir / "proj"
    (copy / "src").mkdir(parents=True)
    (copy / "src" / "a.py").write_text("print(1)\n")
    (copy / ".git").mkdir()  # foreign git dir must not be imported
    (copy / ".git" / "HEAD").write_text("x")
    assert snap.ready_items() == []  # still "being copied"
    old(copy)
    [item] = snap.ready_items()
    first = snap.snapshot(item)
    assert first.changed and "First snapshot" in first.summary
    assert not copy.exists() and (cfg.workspaces_dir / "proj" / "src" / "a.py").exists()

    # Second pull: one file changed, one deleted, one added.
    (copy / "src").mkdir(parents=True)
    (copy / "src" / "a.py").write_text("print(2)\n")
    (copy / "b.txt").write_text("new\n")
    old(copy)
    second = snap.snapshot(snap.ready_items()[0])
    assert second.changed and "M\tsrc/a.py" in second.summary and "A\tb.txt" in second.summary
    log = subprocess.run(["git", "log", "--oneline"], cwd=cfg.workspaces_dir / "proj", capture_output=True, text=True)
    assert len(log.stdout.splitlines()) == 2

    # Third pull, identical: no new commit.
    (copy / "src").mkdir(parents=True)
    (copy / "src" / "a.py").write_text("print(2)\n")
    (copy / "b.txt").write_text("new\n")
    old(copy)
    assert not snap.snapshot(snap.ready_items()[0]).changed


def test_zip_with_single_top_folder(cfg):
    cfg.inbox_dir.mkdir()
    z = cfg.inbox_dir / "proj2.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("proj2/readme.md", "hi")
    old(cfg.inbox_dir)
    snap = InboxSnapshotter(cfg)
    result = snap.snapshot(snap.ready_items()[0])
    assert (cfg.workspaces_dir / "proj2" / "readme.md").read_text() == "hi"
    assert result.changed and not z.exists()


def test_zip_slip_rejected(cfg):
    cfg.inbox_dir.mkdir()
    z = cfg.inbox_dir / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../escape.txt", "x")
    with pytest.raises(ValueError, match="unsafe"):
        InboxSnapshotter(cfg).snapshot(z)


def test_readme_and_loose_files_ignored(cfg):
    cfg.inbox_dir.mkdir()
    (cfg.inbox_dir / "README.md").write_text("x")
    (cfg.inbox_dir / "notes.txt").write_text("x")
    old(cfg.inbox_dir)
    assert InboxSnapshotter(cfg).ready_items() == []
