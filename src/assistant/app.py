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

    from .ui.overlay import Overlay, QtSink, Tray
    from .voice.tts import Speaker

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    sink = QtSink()
    speaker = Speaker(cfg.voice)
    capturer = ScreenCapturer(cfg.capture, cfg.logs_dir / "screenshots")
    orch = Orchestrator(cfg, sink, speaker, capturer)
    core = CoreThread(orch)
    core.start()

    overlay = Overlay(orch, sink, cfg.home)

    class Bridge(QObject):
        toggle = Signal()

    bridge = Bridge()
    bridge.toggle.connect(overlay.toggle)

    listener = None
    stt_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
    if cfg.voice.enabled:
        listener = _start_voice(cfg, orch, sink, speaker, stt_pool)

    hotkeys = _start_hotkeys(cfg, orch, speaker, listener, bridge.toggle.emit)

    def quit_app() -> None:
        if hotkeys is not None:
            hotkeys.stop()
        if listener is not None:
            listener.close()
        speaker.close()
        core.stop()
        stt_pool.shutdown(wait=False, cancel_futures=True)
        app.quit()

    tray = Tray(orch, overlay, sink, cfg.home, quit_app)
    tray.show()
    overlay.show()
    tray.showMessage("PC co-worker", _hint(cfg), msecs=6000)
    return app.exec()


def _hint(cfg: Config) -> str:
    wake = f"say “{cfg.voice.wake_word_model.replace('_', ' ')}” or " if cfg.voice.enabled and cfg.voice.wake_word_enabled else ""
    return f"Ready. {wake}press {cfg.hotkeys.talk} to talk; {cfg.hotkeys.stop} stops everything."


def _start_voice(cfg: Config, orch: Orchestrator, sink, speaker, pool: ThreadPoolExecutor):  # type: ignore[no-untyped-def]
    from .voice.listener import CallDetector, Listener
    from .voice.stt import SpeechToText

    stt = SpeechToText(cfg.voice)
    pool.submit(stt.load)  # warm the model in the background (first run downloads it)

    def transcribe_and_submit(audio: np.ndarray) -> None:
        try:
            text = stt.transcribe(audio)
        except Exception as exc:  # noqa: BLE001
            log.exception("transcription failed")
            sink.status("error", f"Speech-to-text failed: {exc}")
            return
        if text:
            orch.submit_text_threadsafe(text, "voice")
        else:
            sink.status("idle", "Didn't catch that")

    states = {"listening": "Listening…", "transcribing": "Transcribing…", "idle": "Ready"}
    listener = Listener(
        cfg.voice,
        on_utterance=lambda audio: pool.submit(transcribe_and_submit, audio),
        on_state=lambda s: sink.status(s, states.get(s, s)),
        is_speaking=lambda: speaker.speaking,
        wake_muted=CallDetector(cfg.voice.mute_wake_when_mic_used_by),
    )
    try:
        listener.start()
    except Exception as exc:  # noqa: BLE001 - no mic should not stop the app
        log.exception("microphone unavailable")
        sink.activity(f"Microphone unavailable ({exc}); type in the overlay instead.")
        return None
    return listener


def _start_hotkeys(cfg: Config, orch: Orchestrator, speaker, listener, toggle_overlay):  # type: ignore[no-untyped-def]
    try:
        from pynput import keyboard
    except ImportError:
        return None

    def talk() -> None:
        speaker.stop()  # barge-in: talking cuts off the current reply
        if listener is not None:
            listener.trigger()

    try:
        hk = keyboard.GlobalHotKeys({cfg.hotkeys.talk: talk, cfg.hotkeys.stop: orch.stop_threadsafe,
                                     cfg.hotkeys.toggle_overlay: toggle_overlay})
        hk.start()
        return hk
    except Exception:  # noqa: BLE001 - bad hotkey syntax in config
        log.exception("could not register hotkeys; check [hotkeys] in config.toml")
        return None
