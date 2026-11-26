"""The "pull her project" flow: inbox copy -> workspace snapshot in git.

1. You say "pull her project". The assistant shows a robocopy command to
   paste into the remote laptop's terminal. It copies the project into this
   PC's inbox over Remote Desktop drive redirection (\\\\tsclient\\C\\...).
2. This module notices the copy once it stops changing, replaces
   workspaces/<project>/ with it, and commits a dated snapshot. The commit's
   diff is the "what changed since last time" summary.

It is a read copy. Real changes still happen on the remote laptop.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath

from .config import Config

log = logging.getLogger(__name__)

_GIT_ID = ["-c", "user.name=pc-assistant", "-c", "user.email=pc-assistant@localhost", "-c", "core.autocrlf=false"]


def tsclient_path(local: Path | str) -> str:
    r"""C:\Users\me\assistant\inbox -> \\tsclient\C\Users\me\assistant\inbox (as seen from the RDP session)."""
    p = PureWindowsPath(str(local))
    if not p.drive or ":" not in p.drive:
        raise ValueError(f"{local} is not a Windows drive path")
    return "\\\\tsclient\\" + p.drive.rstrip(":") + "\\" + "\\".join(p.parts[1:])


def robocopy_command(cfg: Config) -> str:
    rc = cfg.remote
    dest = tsclient_path(cfg.inbox_dir / rc.project_name)
    xd = " ".join(f'"{d}"' for d in rc.robocopy_exclude_dirs)
    xf = " ".join(f'"{f}"' for f in rc.robocopy_exclude_files)
    # /MIR mirrors into the destination folder only (the inbox on THIS PC);
    # the source on the laptop is only read.
    return f'robocopy "{rc.remote_project_path}" "{dest}" /MIR /XD {xd} /XF {xf} /R:1 /W:1 /NP /NFL /NDL'


def _newest_mtime(path: Path) -> float:
    newest = path.stat().st_mtime
    if path.is_dir():
        for p in path.rglob("*"):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                continue
    return newest


@dataclass(frozen=True)
class Snapshot:
    project: str
    workspace: Path
    changed: bool
    summary: str


class InboxSnapshotter:
    def __init__(self, cfg: Config, clock=time.time) -> None:  # type: ignore[no-untyped-def]
        self.cfg = cfg
        self.clock = clock

    def ready_items(self) -> list[Path]:
        """Inbox entries (folders or .zip) unchanged for inbox_settle_s."""
        inbox = self.cfg.inbox_dir
        if not inbox.is_dir():
            return []
        out = []
        for item in sorted(inbox.iterdir()):
            if item.name.startswith(".") or item.name.lower() == "readme.md":
                continue
            if item.is_file() and item.suffix.lower() != ".zip":
                continue
            try:
                if self.clock() - _newest_mtime(item) >= self.cfg.remote.inbox_settle_s:
                    out.append(item)
            except OSError:
                continue
        return out

    def snapshot(self, item: Path) -> Snapshot:
        name = item.stem if item.is_file() else item.name
        workspace = self.cfg.workspaces_dir / name
        with tempfile.TemporaryDirectory() as tmp:
            source = item
            if item.is_file():
                with zipfile.ZipFile(item) as zf:
                    _safe_extract(zf, Path(tmp))
                entries = list(Path(tmp).iterdir())
                source = entries[0] if len(entries) == 1 and entries[0].is_dir() else Path(tmp)
            self._replace_tree(source, workspace)
        changed, summary = self._commit(workspace)
        # The data now lives in git; drop the inbox copy so it is not re-imported.
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        return Snapshot(name, workspace, changed, summary)

    @staticmethod
    def _replace_tree(source: Path, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        for child in workspace.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in source.iterdir():
            if child.name == ".git":
                continue  # never import someone else's git internals over ours
            if child.is_dir():
                shutil.copytree(child, workspace / child.name, symlinks=True)
            else:
                shutil.copy2(child, workspace / child.name)

    def _git(self, workspace: Path, *args: str) -> str:
        res = subprocess.run(["git", *_GIT_ID, *args], cwd=workspace, capture_output=True, text=True, timeout=120)
        if res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
        return res.stdout

    def _commit(self, workspace: Path) -> tuple[bool, str]:
        first = not (workspace / ".git").exists()
        if first:
            self._git(workspace, "init", "-q")
        else:
            # Rebuild the index from scratch so every file is re-hashed. Copies
            # keep the source mtimes, and git's stat cache can miss a same-size
            # edit made within the same second; a snapshot must never miss one.
            (workspace / ".git" / "index").unlink(missing_ok=True)
        self._git(workspace, "add", "-A")
        if not self._git(workspace, "status", "--porcelain").strip():
            return False, "No changes since the last snapshot."
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._git(workspace, "commit", "-q", "-m", f"Snapshot {stamp} (pulled from remote laptop)")
        if first:
            count = len(self._git(workspace, "ls-files").splitlines())
            return True, f"First snapshot: {count} files."
        stat = self._git(workspace, "diff", "--shortstat", "HEAD~1", "HEAD").strip()
        files = self._git(workspace, "diff", "--name-status", "HEAD~1", "HEAD").strip().splitlines()
        listing = "\n".join(files[:30]) + (f"\n... and {len(files) - 30} more" if len(files) > 30 else "")
        return True, f"{stat}\n{listing}"


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract refusing absolute paths and ../ escapes (zip-slip)."""
    root = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"unsafe path in zip: {member.filename}")
    zf.extractall(dest)
