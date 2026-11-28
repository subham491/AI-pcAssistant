"""Command line: `pc-assistant [run|text|doctor|login|init]`.

    run     the full app: orb, tray, voice, hotkeys (default)
    text    type to it in the terminal; no mic, no GUI. Best first test.
    doctor  check every dependency and print what is missing
    login   open Claude Code to sign in with your Claude subscription
    init    create/refresh the home folder from the template (never overwrites)
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import AuthMode, Config, ConfigError, default_home, load_config
from .ui.sink import NullSink

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "home_template"


def setup_logging(cfg: Config, verbose: bool) -> None:
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(cfg.logs_dir / "assistant.log", maxBytes=2_000_000, backupCount=3,
                                             encoding="utf-8"),
    ]
    if sys.stderr is not None:  # None under pythonw.exe (Start Menu shortcut)
        stream = logging.StreamHandler()
        stream.setLevel(logging.DEBUG if verbose else logging.WARNING)
        handlers.append(stream)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def init_home(home: Path, template: Path = TEMPLATE_DIR) -> list[Path]:
    """Copy template files that do not exist yet. Your edits are never overwritten."""
    created: list[Path] = []
    for src in sorted(template.rglob("*")):
        rel = src.relative_to(template)
        dst = home / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            created.append(dst)
    return created


def bundled_claude() -> Path | None:
    import claude_agent_sdk

    base = Path(claude_agent_sdk.__file__).parent / "_bundled"
    for name in ("claude.exe", "claude"):
        if (base / name).exists():
            return base / name
    found = shutil.which("claude")
    return Path(found) if found else None


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def doctor(cfg: Config) -> int:
    ok = True

    def line(good: bool | None, label: str, detail: str = "") -> None:
        nonlocal ok
        mark = {True: "OK  ", False: "FAIL", None: "WARN"}[good]
        if good is False:
            ok = False
        print(f"[{mark}] {label}{': ' + detail if detail else ''}")

    line(sys.version_info >= (3, 11), "Python", sys.version.split()[0])
    line(sys.platform == "win32" or None, "Windows", sys.platform + ("" if sys.platform == "win32" else
                                                                     " (voice/screen features need Windows)"))
    line(cfg.home.is_dir(), "Home folder", str(cfg.home))
    line((cfg.home / "CLAUDE.md").exists() or None, "CLAUDE.md in home folder")
    line(shutil.which("git") is not None, "Git", shutil.which("git") or "install Git for Windows")

    cli = bundled_claude()
    if cli:
        try:
            ver = subprocess.run([str(cli), "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
            line(True, "Claude Code", f"{ver} ({cli})")
        except Exception as exc:  # noqa: BLE001
            line(False, "Claude Code", f"{cli} did not run: {exc}")
    else:
        line(False, "Claude Code", "bundled CLI missing; reinstall claude-agent-sdk")

    if cfg.brain.auth_mode is AuthMode.API_KEY:
        line(bool(os.environ.get("ANTHROPIC_API_KEY")), "Auth (api_key)", "ANTHROPIC_API_KEY set"
             if os.environ.get("ANTHROPIC_API_KEY") else "ANTHROPIC_API_KEY not set")
    else:
        creds = Path.home() / ".claude" / ".credentials.json"
        line(creds.exists() or None, "Auth (subscription)",
             "signed in" if creds.exists() else "no saved login found; run `pc-assistant login` (on some setups the "
                                                 "login is stored elsewhere, so `pc-assistant text` is the real test)")
        if os.environ.get("ANTHROPIC_API_KEY"):
            line(None, "ANTHROPIC_API_KEY", "is set but will be ignored in subscription mode")

    for mod, why in [("faster_whisper", "speech-to-text"), ("openwakeword", "wake word"), ("sounddevice", "microphone"),
                     ("mss", "screenshots"), ("pynput", "hotkeys/input"), ("psutil", "kill switch"),
                     ("PySide6", "the orb and tray"), ("win32com.client", "spoken replies (SAPI)"),
                     ("uiautomation", "UI Automation")]:
        try:
            importlib.import_module(mod)
            line(True, mod, why)
        except Exception as exc:  # noqa: BLE001
            line(False, mod, f"{why}: {exc}")

    try:
        import sounddevice as sd

        dev = sd.query_devices(kind="input")
        line(True, "Default microphone", str(dev.get("name")))
    except Exception as exc:  # noqa: BLE001
        line(False, "Default microphone", str(exc))
    try:
        import ctranslate2

        n = ctranslate2.get_cuda_device_count()
        line(True if n else None, "NVIDIA GPU for Whisper", f"{n} CUDA device(s)" if n else "none, using CPU (slower)")
    except Exception:  # noqa: BLE001
        pass
    try:
        from .desktop.capture import ScreenCapturer
        from .desktop.win32 import set_dpi_awareness

        set_dpi_awareness()
        mons = ScreenCapturer(cfg.capture).monitors()
        line(bool(mons), "Monitors", ", ".join(f"{m.index}: {m.rect.width}x{m.rect.height}"
                                               f"{' primary' if m.primary else ''}" for m in mons))
    except Exception as exc:  # noqa: BLE001
        line(False, "Monitors", str(exc))

    print("\nAll good." if ok else "\nFix the FAIL lines above (README > Troubleshooting).")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# text mode
# ---------------------------------------------------------------------------


class _PrintSink(NullSink):
    """Prints what the UI would show. Inherits NullSink so a new UiSink method is a silent no-op here
    instead of an AttributeError mid-turn (tests/test_sinks.py keeps every sink complete)."""

    def status(self, state: str, text: str) -> None:
        if state in ("error", "waiting"):
            print(f"  [{state}] {text}")

    def transcript(self, text: str) -> None: ...

    def assistant_text(self, text: str) -> None:
        print(f"\nClaude: {text}\n")

    def activity(self, text: str) -> None:
        print(f"  · {text}")

    # Tool steps are already printed through activity() ("→ Read app.py", "✗ Read failed: ...").
    def step_started(self, step) -> None: ...  # type: ignore[no-untyped-def]

    def step_finished(self, step_id: str, ok: bool, note: str) -> None: ...

    def turn_finished(self, ok: bool) -> None: ...

    def show_code(self, title: str, code: str, language: str) -> None:
        print(f"\n--- {title} ---\n{code}\n---\n")

    def request_approval(self, req) -> None:  # type: ignore[no-untyped-def]
        print(f"\n=== APPROVE? {req.title} (T{req.tier}: {req.reason}) ===\n{req.body}\n"
              "=== type 'apply' or 'reject' ===")

    def approval_closed(self, req_id: str, outcome: str) -> None:
        print(f"  · {outcome}")

    def modes(self, edit_mode: str, remote: bool) -> None:
        print(f"  · mode: {edit_mode}{' | REMOTE' if remote else ''}")


class _QuietSpeaker:
    speaking = False

    def say(self, text: str) -> None: ...

    def stop(self) -> None: ...


async def _text_loop(cfg: Config, speak: bool) -> None:
    from .desktop.capture import ScreenCapturer
    from .desktop.win32 import set_dpi_awareness
    from .orchestrator import Orchestrator

    set_dpi_awareness()
    speaker = _QuietSpeaker()
    if speak:
        from .voice.tts import Speaker

        speaker = Speaker(cfg.voice)  # type: ignore[assignment]
    orch = Orchestrator(cfg, _PrintSink(), speaker, ScreenCapturer(cfg.capture, cfg.logs_dir / "screenshots"))
    await orch.start()
    print("Type to talk to it. 'stop' cancels, 'apply'/'reject' answer approvals, 'exit' quits.\n")
    try:
        while True:
            text = await asyncio.to_thread(input, "> ")
            if text.strip().lower() in {"exit", "quit"}:
                break
            await orch.handle_text(text, source="typed")
            # Wait for the reply unless an approval is pending (you must answer it).
            while orch.busy and not orch._approvals:  # noqa: SLF001 - CLI convenience
                await asyncio.sleep(0.1)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pc-assistant", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="run", choices=["run", "text", "doctor", "login", "init"])
    parser.add_argument("--home", type=Path, default=None, help=f"home folder (default {default_home()})")
    parser.add_argument("--speak", action="store_true", help="text mode: also speak replies")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    home = (args.home or default_home()).expanduser().resolve()
    if args.command == "init":
        created = init_home(home)
        print(f"Home folder: {home}\n" + ("\n".join(f"  + {p.relative_to(home)}" for p in created) or
                                          "  (nothing new; existing files kept)"))
        return 0

    try:
        cfg = load_config(home)
    except ConfigError as exc:
        print(f"config.toml problem: {exc}", file=sys.stderr)
        return 2
    setup_logging(cfg, args.verbose)

    if args.command == "doctor":
        return doctor(cfg)
    if args.command == "login":
        cli = bundled_claude()
        if cli is None:
            print("Claude Code not found. Re-run setup.", file=sys.stderr)
            return 1
        print("Claude Code will open. Type /login, finish signing in in the browser, then type /exit.\n")
        return subprocess.call([str(cli)], cwd=str(home))
    if args.command == "text":
        asyncio.run(_text_loop(cfg, args.speak))
        return 0

    from .app import run_gui

    return run_gui(cfg)


if __name__ == "__main__":
    sys.exit(main())
