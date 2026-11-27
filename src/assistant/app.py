"""Wiring for the full desktop app: Qt on the main thread, asyncio core on a
worker thread, the mic listener and hotkeys on their own threads."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .config import Config
from .desktop.capture import ScreenCapturer
from .desktop.win32 import set_dpi_awareness
from .orchestrator import Orchestrator

log = logging.getLogger(__name__)


class CoreThread:
    """Owns the asyncio loop the orchestrator lives on."""

    def __init__(self, orch: Orchestrator) -> None:
        self.orch = orch
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="core", daemon=True)

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=30):
            raise RuntimeError("core loop did not start")

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)

        async def boot() -> None:
            await self.orch.start()
            asyncio.create_task(self.orch.watch_inbox())
            self._ready.set()

        self.loop.run_until_complete(boot())
        self.loop.run_forever()

    def stop(self) -> None:
        fut = asyncio.run_coroutine_threadsafe(self.orch.shutdown(), self.loop)
        try:
            fut.result(timeout=8)
        except Exception:  # noqa: BLE001 - quitting anyway
            log.debug("shutdown incomplete", exc_info=True)
        self.loop.call_soon_threadsafe(self.loop.stop)


def run_gui(cfg: Config) -> int:
    set_dpi_awareness()  # before any Qt or window call

    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication

    from .ui.orb import Orb
    from .ui.overlay import Overlay, QtSink, Tray
    from .ui.trail import TrailWindow
    from .voice.tts import Speaker

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    sink = QtSink()
    speaker = Speaker(cfg.voice)
    capturer = ScreenCapturer(cfg.capture, cfg.logs_dir / "screenshots")
    orch = Orchestrator(cfg, sink, speaker, capturer)
    core = CoreThread(orch)

    # Build the UI before the core starts: the core's first status and mode updates are
    # signals, and a signal sent before anything is connected is lost ("Starting…" forever).
    name = cfg.ui.name
    listener = None

    def talk() -> None:
        """Orb click, tray menu or talk hotkey: start listening, or finish if already listening."""
        speaker.stop()  # barge-in: talking cuts off the current reply
        if listener is not None:
            listener.trigger()
        else:
            sink.activity("No microphone: type in the chat instead.")
            bridge.toggle.emit()

    def stop_all() -> None:
        if listener is not None:
            listener.cancel()
        orch.stop_threadsafe()

    class Bridge(QObject):
        toggle = Signal()

    bridge = Bridge()
    overlay = Overlay(orch, sink, cfg.home, name=name, hide_after_s=cfg.ui.answer_hide_s)
    tray = Tray(orch, overlay, sink, cfg.home, lambda: quit_app(), name=name, on_talk=talk)
    orb = Orb(name, _pretty_hotkey(cfg.hotkeys.talk), cfg.state_dir / "orb.json")
    orb.place(cfg.ui.corner, cfg.ui.monitor)
    overlay.follow(orb)
    orb.clicked.connect(talk)
    orb.menu_requested.connect(tray.popup)
    orb.moved.connect(overlay.orb_moved)
    sink.sig_status.connect(orb.set_status)
    sink.sig_level.connect(orb.set_level)
    if cfg.ui.trail:
        trail = TrailWindow(orb)
        sink.sig_status.connect(trail.on_status)
        sink.sig_step_started.connect(trail.on_step_started)
        sink.sig_step_finished.connect(trail.on_step_finished)
        sink.sig_turn_finished.connect(trail.on_turn_finished)
        orb.moved.connect(trail.reposition)
    else:
        sink.sig_turn_finished.connect(orb.turn_finished)
    bridge.toggle.connect(overlay.toggle)
    core.start()

    stt_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
    if cfg.voice.enabled:
        listener = _start_voice(cfg, orch, sink, speaker, stt_pool)

    hotkeys = _start_hotkeys(cfg, talk, stop_all, bridge.toggle.emit)

    def quit_app() -> None:
        if hotkeys is not None:
            hotkeys.stop()
        if listener is not None:
            listener.close()
        speaker.close()
        core.stop()
        stt_pool.shutdown(wait=False, cancel_futures=True)
        app.quit()

    tray.show()
    orb.show()
    tray.showMessage(name, _hint(cfg), msecs=6000)
    return app.exec()


def _pretty_hotkey(combo: str) -> str:
    """"<ctrl>+<alt>+<space>" -> "Ctrl+Alt+Space"."""
    return "+".join(part.strip("<>").capitalize() for part in combo.split("+"))


def _hint(cfg: Config) -> str:
    word = cfg.voice.wake_phrase or cfg.voice.wake_word_model.replace("_", " ")
    wake = f", or say “{word}”" if cfg.voice.enabled and cfg.voice.wake_word_enabled else ""
    return (f"Asleep, mic off. Click the orb or press {_pretty_hotkey(cfg.hotkeys.talk)} to talk{wake}; "
            f"click again when you are done. {_pretty_hotkey(cfg.hotkeys.stop)} stops everything.")


def _start_voice(cfg: Config, orch: Orchestrator, sink, speaker, pool: ThreadPoolExecutor):  # type: ignore[no-untyped-def]
    from .intents import strip_wake_phrase
    from .voice.listener import CallDetector, Listener, beep
    from .voice.stt import SpeechToText

    stt = SpeechToText(cfg.voice, name=cfg.ui.name, on_slow=sink.activity)

    def warm() -> None:  # first run downloads the model; say so instead of looking stuck
        sink.activity(f"Loading speech model {stt.model_name} (first run downloads it, about 1.6 GB)…")
        try:
            stt.load()
            sink.activity(f"Speech model ready on {stt.device.upper()}.")
        except Exception as exc:  # noqa: BLE001
            log.exception("speech model failed to load")
            sink.status("error", f"Speech model failed to load: {exc}")

    pool.submit(warm)

    def transcribe_and_submit(audio: np.ndarray, needs_wake: bool) -> None:
        try:
            text = stt.transcribe(audio)
        except Exception as exc:  # noqa: BLE001
            log.exception("transcription failed")
            sink.status("error", f"Speech-to-text failed: {exc}")
            return
        if needs_wake:  # room speech: ours only if it starts or ends with the wake phrase
            rest = strip_wake_phrase(text, cfg.voice.wake_phrase)
            if rest is None:
                return
            if not rest:
                listener.trigger()  # just the wake phrase: beep and take the command
                return
            beep(660, 70)  # heard you
            text = rest
        if text:
            orch.submit_text_threadsafe(text, "voice")
        else:
            sink.status("idle", "Didn't catch that")

    listening = "Listening…" if cfg.voice.stop_on_silence else "Listening… click again when you are done"
    states = {"listening": listening, "transcribing": "Transcribing…", "idle": "Asleep (mic off)"}
    listener = Listener(
        cfg.voice,
        on_utterance=lambda audio, needs_wake: pool.submit(transcribe_and_submit, audio, needs_wake),
        on_state=lambda s: sink.status(s, states.get(s, s)),
        is_speaking=lambda: speaker.speaking,
        wake_muted=CallDetector(cfg.voice.mute_wake_when_mic_used_by),
        on_level=sink.sig_level.emit,
    )
    try:
        listener.start()
    except Exception as exc:  # noqa: BLE001 - no mic should not stop the app
        log.exception("microphone unavailable")
        sink.activity(f"Microphone unavailable ({exc}); type in the overlay instead.")
        return None
    return listener


def _start_hotkeys(cfg: Config, talk, stop_all, toggle_overlay):  # type: ignore[no-untyped-def]
    try:
        from pynput import keyboard
    except ImportError:
        return None

    try:
        hk = keyboard.GlobalHotKeys({cfg.hotkeys.talk: talk, cfg.hotkeys.stop: stop_all,
                                     cfg.hotkeys.toggle_overlay: toggle_overlay})
        hk.start()
        return hk
    except Exception:  # noqa: BLE001 - bad hotkey syntax in config
        log.exception("could not register hotkeys; check [hotkeys] in config.toml")
        return None
