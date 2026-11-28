"""Watchdog: relaunch the app if it dies, and ask Gemini why when relaunching does not help.

Runs as its own process (`python -m assistant.watchdog`), so it survives the app being restarted or crashing.
Plain code does the relaunch (free, instant); Gemini is only called once, to explain a failure that keeps
repeating. A deliberate Quit from the tray leaves `.state/quit.flag`, and the watchdog respects it.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import psutil

from .config import Config, load_config
from .gemini import GeminiError, ask

GRACE_S = 45  # restart-self leaves a gap of about 30 s; do not race it
SETTLE_S = 30  # how long a relaunch gets to show up before trying again
MAX_TRIES = 2
POLL_S = 5


def app_running() -> bool:
    return any(" ".join(p.info["cmdline"] or []).endswith("-m assistant run")
               for p in psutil.process_iter(["cmdline"]))


def launch_app() -> None:
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    subprocess.Popen([sys.executable, "-m", "assistant", "run"], cwd=Path(__file__).resolve().parents[2],
                     creationflags=flags, close_fds=True)


class Watchdog:
    def __init__(self, running: Callable[[], bool], quit_flag: Callable[[], bool],
                 launch: Callable[[], None], diagnose: Callable[[], None]) -> None:
        self.running, self.quit_flag, self.launch, self.diagnose = running, quit_flag, launch, diagnose
        self.down_since: float | None = None
        self.retry_at = 0.0
        self.tries = 0

    def step(self, now: float) -> str:
        if self.running():
            self.down_since, self.tries = None, 0
            return "up"
        if self.quit_flag():
            self.down_since, self.tries = None, 0
            return "quit"
        if self.down_since is None:
            self.down_since = now
        if now < max(self.down_since + GRACE_S, self.retry_at):
            return "waiting"
        if self.tries >= MAX_TRIES:
            self.diagnose()
            return "gave up"
        self.launch()
        self.tries += 1
        self.retry_at = now + SETTLE_S
        return "relaunched"


def diagnose(cfg: Config) -> None:
    """Ask Gemini what the log says, and leave the answer in notes/watchdog-report.md."""
    log_path = cfg.logs_dir / "assistant.log"
    tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]) if log_path.exists() else "(no log)"
    answer = "(no Gemini key configured: set [brain].gemini_key_file in config.toml)"
    key_file = Path(cfg.brain.gemini_key_file) if cfg.brain.gemini_key_file else None
    if key_file and key_file.is_file():
        prompt = ("A Windows voice-assistant app (Python, PySide6, Claude Agent SDK) will not stay running. "
                  "Last log lines follow. Give the most likely cause and the single fix, in five lines or fewer.\n\n" + tail)
        try:
            answer = ask(prompt, key_file, timeout=60)
        except GeminiError as exc:  # the report must still be written
            answer = f"(Gemini did not answer: {exc})"
    report = cfg.home / "notes" / "watchdog-report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(f"# Watchdog report {datetime.now():%Y-%m-%d %H:%M}\n\n"
                      f"Relaunched {MAX_TRIES} times and the app did not stay up.\n\n## Gemini says\n{answer}\n\n"
                      f"## Last log lines\n```\n{tail}\n```\n", encoding="utf-8")


def main() -> int:
    cfg = load_config()
    wd = Watchdog(app_running, lambda: (cfg.state_dir / "quit.flag").exists(), launch_app, lambda: diagnose(cfg))
    while wd.step(time.monotonic()) != "gave up":
        time.sleep(POLL_S)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
