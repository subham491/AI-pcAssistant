"""The orchestrator: the only component that knows about all the others.

Voice, capture, brain and UI never call each other directly. Everything a
user says (or types into the overlay) arrives at handle_text(); local
intents are handled here, everything else becomes a Claude turn.

Runs on one asyncio loop in a worker thread. Other threads (Qt, hotkeys,
the mic listener) talk to it only through the *_threadsafe methods.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from datetime import datetime
from typing import Any, Callable, Protocol

from .audit import AuditLog
from .brain import AuthError, Brain, SessionStore, Turn, prepare_auth_env
from .config import Config, EditMode
from .desktop import control, win32
from .desktop.capture import CaptureError, ScreenCapturer, Shot
from .diffs import UndoJournal
from .inbox import InboxSnapshotter, robocopy_command
from .intents import Kind, ScreenTarget, TargetKind, parse, screen_target
from .policy import Policy
from .ui.sink import ApprovalRequest, UiSink
from .voice.speech_text import SpeechChunker

log = logging.getLogger(__name__)

_MODE_WORDS = {EditMode.SUGGEST: "Suggest only", EditMode.REVIEW: "Review each edit", EditMode.AUTO: "Auto apply"}


class SpeakerLike(Protocol):
    @property
    def speaking(self) -> bool: ...

    def say(self, text: str) -> None: ...

    def stop(self) -> None: ...


class Orchestrator:
    def __init__(
        self,
        cfg: Config,
        ui: UiSink,
        speaker: SpeakerLike,
        capturer: ScreenCapturer,
        brain_factory: Callable[["Orchestrator", str], Brain] | None = None,
        foreground_process: Callable[[], str] = lambda: (win32.foreground_window() or _NoWin).process,
    ) -> None:
        self.cfg = cfg
        self.ui = ui
        self.speaker = speaker
        self.capturer = capturer
        self.policy = Policy(cfg)
        self.audit = AuditLog(cfg.logs_dir / "audit.jsonl")
        self.undo = UndoJournal()
        self.sessions = SessionStore(cfg.state_dir / "sessions.json")
        self.inbox = InboxSnapshotter(cfg)
        self._foreground_process = foreground_process
        self._brain_factory = brain_factory or _default_brain_factory
        self._brains: dict[str, Brain] = {}
        self._approvals: dict[str, asyncio.Future[bool]] = {}
        self._approval_ids = itertools.count(1)
        self._turn_task: asyncio.Task[None] | None = None
        self._remote_manual = False
        self._auth_error: AuthError | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.ui.modes(self.policy.edit_mode.value, self.policy.remote_mode)
        self.ui.status("idle", "Ready")
        try:
            prepare_auth_env(self.cfg.brain.auth_mode)
        except AuthError as exc:
            # Keep the app up (local commands, typing) and say what to fix.
            self._auth_error = exc
            self._report_brain_error(exc)
            return
        # Connect early so the first spoken turn does not pay the start-up cost.
        asyncio.create_task(self._warm_up())

    async def _warm_up(self) -> None:
        try:
            await self.brain("local").connect()
            self.ui.activity("Claude Code session ready")
        except Exception as exc:  # noqa: BLE001
            self._report_brain_error(exc)

    async def shutdown(self) -> None:
        for fut in self._approvals.values():
            if not fut.done():
                fut.set_result(False)
        for b in self._brains.values():
            await b.close()

    def brain(self, profile: str) -> Brain:
        if profile not in self._brains:
            self._brains[profile] = self._brain_factory(self, profile)
        return self._brains[profile]

    @property
    def busy(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    # ------------------------------------------------------------------
    # thread-safe entry points (Qt, hotkeys, listener)
    # ------------------------------------------------------------------

    def submit_text_threadsafe(self, text: str, source: str = "voice") -> None:
        assert self.loop is not None
        asyncio.run_coroutine_threadsafe(self.handle_text(text, source), self.loop)

    def stop_threadsafe(self) -> None:
        assert self.loop is not None
        asyncio.run_coroutine_threadsafe(self.stop(), self.loop)

    def resolve_approval_threadsafe(self, req_id: str, approved: bool) -> None:
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self._resolve, req_id, approved)

    def set_edit_mode_threadsafe(self, mode: EditMode) -> None:
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self._set_edit_mode, mode, False)

    def set_remote_threadsafe(self, on: bool) -> None:
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self._set_remote, on, True, False)

    # ------------------------------------------------------------------
    # the main switch
    # ------------------------------------------------------------------

    async def handle_text(self, text: str, source: str = "voice") -> None:
        text = text.strip()
        if not text:
            self.ui.status("idle", "Didn't catch that")
            return
        self.ui.transcript(text)
        intent = parse(text)
        self.audit.write("utterance", source=source, text=text, intent=intent.kind.value)

        if intent.kind is Kind.STOP:
            await self.stop()
        elif intent.kind in (Kind.APPROVE, Kind.REJECT) and self._approvals:
            self._resolve(next(iter(self._approvals)), intent.kind is Kind.APPROVE)
        elif intent.kind is Kind.SET_EDIT_MODE and intent.edit_mode:
            self._set_edit_mode(intent.edit_mode, True)
        elif intent.kind is Kind.REMOTE_ON:
            self._set_remote(True, manual=True, speak=True)
        elif intent.kind is Kind.REMOTE_OFF:
            self._set_remote(False, manual=True, speak=True)
        elif intent.kind is Kind.UNDO:
            self._undo()
        elif intent.kind is Kind.PULL_PROJECT:
            self.ui.show_code("Paste this into the remote laptop's terminal", robocopy_command(self.cfg), "powershell")
            self._say("The copy command is on screen. Paste it into her laptop's terminal. "
                      "I'll snapshot the project when it lands in the inbox.")
        elif intent.kind is Kind.REMEMBER:
            self._remember(intent.text)
        elif intent.kind is Kind.NEW_SESSION:
            if self.busy:
                await self.stop()
            for b in self._brains.values():
                await b.new_session()
            self._say("Starting a fresh conversation.")
        else:
            if self.busy:
                self._say("I'm still on the last request. Say stop to cancel it.")
                return
            self._turn_task = asyncio.create_task(self._run_turn(text))

    # ------------------------------------------------------------------
    # turns
    # ------------------------------------------------------------------

    def _auto_remote(self) -> None:
        if self._remote_manual:
            return
        proc = (self._foreground_process() or "").lower()
        is_rdp = proc in {p.lower() for p in self.cfg.capture.remote_desktop_processes}
        if is_rdp != self.policy.remote_mode:
            self._set_remote(is_rdp, manual=False, speak=False)
            self.ui.activity("Remote desktop in front: remote mode on" if is_rdp else "Remote mode off")

    async def _capture_for(self, text: str) -> list[Shot]:
        target = screen_target(text)
        if target.kind is TargetKind.NONE and self.policy.remote_mode:
            target = ScreenTarget(TargetKind.FOREGROUND_WINDOW)  # remote mode is about the remote screen
        if target.kind is TargetKind.NONE:
            return []
        try:
            shots = await asyncio.to_thread(self.capturer.capture, target)
        except CaptureError as exc:
            self.ui.activity(f"Screenshot skipped: {exc}")
            return []
        except Exception:  # noqa: BLE001 - a failed capture must not lose the turn
            log.exception("capture failed")
            return []
        self.ui.activity("Captured: " + ", ".join(s.label for s in shots))
        return shots

    async def _run_turn(self, text: str) -> None:
        if self._auth_error is not None:
            # Never fall back silently to another login than the one configured.
            self._report_brain_error(self._auth_error)
            return
        self._auto_remote()
        profile = "remote" if self.policy.remote_mode else "local"
        shots = await self._capture_for(text)
        self.ui.status("thinking", "Thinking…")
        chunker = SpeechChunker()

        def on_delta(delta: str) -> None:
            for sentence in chunker.feed(delta):
                self.speaker.say(sentence)

        try:
            result = await self.brain(profile).run_turn(Turn(text, shots), on_delta)
            for sentence in chunker.flush():
                self.speaker.say(sentence)
            if result.is_error and not result.interrupted:
                self._say("That didn't finish cleanly. The details are in the overlay.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._report_brain_error(exc)
            await self.brain(profile).close()  # reconnect (and resume) on the next turn
        finally:
            self.ui.status("idle", "Ready")

    def _report_brain_error(self, exc: BaseException) -> None:
        log.exception("brain error", exc_info=exc)
        name = type(exc).__name__
        hint = ""
        if name == "CLINotFoundError":
            hint = " Claude Code was not found; re-run setup."
        elif name == "AuthError":
            hint = f" {exc}."
        elif "auth" in str(exc).lower() or "login" in str(exc).lower():
            hint = " Check your sign-in: run 'pc-assistant login'."
        self.ui.activity(f"Claude error: {name}: {exc}")
        self.ui.status("error", "Claude unavailable")
        self._say("I couldn't reach Claude." + hint)

    # ------------------------------------------------------------------
    # approvals
    # ------------------------------------------------------------------

    async def approve(self, req: ApprovalRequest) -> bool:
        """Show the approval card and wait for a click or 'apply'/'reject'."""
        assert self.loop is not None
        req_id = f"a{next(self._approval_ids)}"
        req = ApprovalRequest(req_id, req.title, req.tier, req.reason, req.body, req.body_kind)
        fut: asyncio.Future[bool] = self.loop.create_future()
        self._approvals[req_id] = fut
        self.ui.request_approval(req)
        self.ui.status("waiting", "Waiting for your OK")
        self._say(f"I need your OK to {req.title.lower()}. Say apply or reject.")
        try:
            approved = await asyncio.wait_for(fut, timeout=self.cfg.safety.approval_timeout_s)
        except asyncio.TimeoutError:
            approved = False
            self.ui.activity("Approval timed out: rejected")
        finally:
            self._approvals.pop(req_id, None)
        self.ui.approval_closed(req_id, "approved" if approved else "rejected")
        self.ui.status("thinking", "Working…")
        return approved

    def _resolve(self, req_id: str, approved: bool) -> None:
        fut = self._approvals.get(req_id)
        if fut is not None and not fut.done():
            fut.set_result(approved)

    # ------------------------------------------------------------------
    # kill switch
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """Layer 1: stop talking, reject approvals, interrupt Claude, release input."""
        self.speaker.stop()
        for req_id in list(self._approvals):
            self._resolve(req_id, False)
        await asyncio.to_thread(control.release_all)
        for b in self._brains.values():
            await b.interrupt()
        self.audit.write("kill_switch")
        self.ui.activity("■ Stopped")
        self.ui.status("idle", "Stopped")
        task = self._turn_task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=4)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                # Layer 2: Claude did not stop in time. Kill its process tree
                # (claude.exe and every command it started) and drop the session;
                # the next turn reconnects and resumes the conversation.
                killed = await asyncio.to_thread(kill_child_processes)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                for b in self._brains.values():
                    await b.close()
                self.ui.activity(f"■ Hard stop: ended {killed} process(es)")

    # ------------------------------------------------------------------
    # local actions
    # ------------------------------------------------------------------

    def _say(self, text: str) -> None:
        self.ui.activity(f"🗣 {text}")
        self.speaker.say(text)

    def _set_edit_mode(self, mode: EditMode, speak: bool) -> None:
        self.policy.edit_mode = mode
        self.ui.modes(mode.value, self.policy.remote_mode)
        self.audit.write("edit_mode", mode=mode.value)
        if speak:
            self._say(f"{_MODE_WORDS[mode]} mode.")

    def _set_remote(self, on: bool, manual: bool, speak: bool) -> None:
        self.policy.remote_mode = on
        self._remote_manual = manual and on
        self.ui.modes(self.policy.edit_mode.value, on)
        self.audit.write("remote_mode", on=on, manual=manual)
        if speak:
            self._say("Remote mode. I'll only look and suggest." if on else "Local mode.")

    def _undo(self) -> None:
        try:
            entry = self.undo.undo_last()
        except OSError as exc:
            self._say(f"I couldn't undo that: {exc}")
            return
        if entry is None:
            self._say("There's nothing to undo.")
        else:
            what = "removed" if entry.before is None else "restored"
            self.audit.write("undo", path=str(entry.path))
            self._say(f"Undone. I {what} {entry.path.name}.")

    def _remember(self, fact: str) -> None:
        path = self.cfg.memory_dir / "about-me.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"- {fact} _(added {datetime.now():%Y-%m-%d})_\n")
        self._say("Noted.")

    # ------------------------------------------------------------------
    # inbox watcher
    # ------------------------------------------------------------------

    async def watch_inbox(self, interval_s: float = 5.0) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                for item in await asyncio.to_thread(self.inbox.ready_items):
                    snap = await asyncio.to_thread(self.inbox.snapshot, item)
                    self.audit.write("snapshot", project=snap.project, changed=snap.changed)
                    self.ui.show_code(f"Snapshot of {snap.project}", snap.summary, "text")
                    self._say(f"New snapshot of {snap.project} is in the workspace."
                              if snap.changed else f"{snap.project} has no changes since last time.")
            except Exception:  # noqa: BLE001 - keep watching
                log.exception("inbox snapshot failed")
                self.ui.activity("Inbox snapshot failed; see logs")


class _NoWin:
    process = ""


def kill_child_processes() -> int:
    """Kill every process this one started (claude.exe and its commands)."""
    try:
        import psutil
    except ImportError:
        return 0
    children = psutil.Process().children(recursive=True)
    for p in children:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(children, timeout=3)
    return len(children)


def _default_brain_factory(orch: Orchestrator, profile: str) -> Brain:
    from .desktop.tools import build_desktop_server

    server = build_desktop_server(orch.capturer, orch.ui, remote=(profile == "remote"))
    return Brain(orch.cfg, profile, orch.policy, orch.approve, orch.ui, orch.audit, orch.undo,  # type: ignore[arg-type]
                 server, orch.sessions)


__all__: list[Any] = ["Orchestrator", "kill_child_processes"]
