"""Microphone loop: wake word or hotkey -> capture one utterance -> callback.

Runs on its own thread. The model never hears raw audio; only the final
transcript leaves this module (through on_utterance -> STT -> orchestrator).
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from typing import Callable

import numpy as np

from ..config import VoiceConfig
from .endpoint import FRAME_SAMPLES, SAMPLE_RATE, Endpointer, EndpointerConfig, EndState

log = logging.getLogger(__name__)


def beep(freq: int = 880, ms: int = 90) -> None:
    if sys.platform == "win32":
        import winsound

        threading.Thread(target=winsound.Beep, args=(freq, ms), daemon=True).start()


class WakeWord:
    def __init__(self, cfg: VoiceConfig) -> None:
        from openwakeword.model import Model

        self.threshold = cfg.wake_word_threshold
        self._model = Model(wakeword_models=[cfg.wake_word_model], inference_framework="onnx")

    def detect(self, frame: np.ndarray) -> bool:
        scores = self._model.predict(frame)
        if max(scores.values(), default=0.0) >= self.threshold:
            self._model.reset()  # avoid re-triggering on the same audio
            return True
        return False


class Listener:
    def __init__(
        self,
        cfg: VoiceConfig,
        on_utterance: Callable[[np.ndarray], None],
        on_state: Callable[[str], None],
        is_speaking: Callable[[], bool],
        wake_muted: Callable[[], bool],
    ) -> None:
        self.cfg = cfg
        self.on_utterance = on_utterance
        self.on_state = on_state
        self.is_speaking = is_speaking
        self.wake_muted = wake_muted
        self._frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._trigger = threading.Event()
        self._closing = threading.Event()
        self._wake: WakeWord | None = None
        self._stream = None
        self._thread = threading.Thread(target=self._run, name="listener", daemon=True)

    def start(self) -> None:
        import sounddevice as sd

        if self.cfg.wake_word_enabled:
            try:
                self._wake = WakeWord(self.cfg)
            except Exception:  # noqa: BLE001
                log.exception("wake word unavailable; use the talk hotkey")
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES,
            device=self.cfg.input_device, callback=self._on_audio,
        )
        self._stream.start()
        self._thread.start()

    def close(self) -> None:
        self._closing.set()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()

    def trigger(self) -> None:
        """Push-to-talk: start capturing now, no wake word needed."""
        self._trigger.set()

    def _on_audio(self, indata, _frames, _time, status) -> None:  # type: ignore[no-untyped-def]
        if status:
            log.debug("audio status: %s", status)
        try:
            self._frames.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # we are behind; dropping audio beats unbounded memory

    def _run(self) -> None:
        ep_cfg = EndpointerConfig(end_silence_s=self.cfg.end_silence_s, max_utterance_s=self.cfg.max_utterance_s,
                                  no_speech_timeout_s=self.cfg.no_speech_timeout_s)
        endpointer = Endpointer(ep_cfg)
        capturing = False
        while not self._closing.is_set():
            try:
                frame = self._frames.get(timeout=0.2)
            except queue.Empty:
                frame = None

            if not capturing:
                woke = self._trigger.is_set()
                if frame is not None and not woke and self._wake and not self.is_speaking() and not self.wake_muted():
                    woke = self._wake.detect(frame)
                elif frame is not None and not woke:
                    endpointer.noise = 0.95 * endpointer.noise + 0.05 * float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
                if woke:
                    self._trigger.clear()
                    self._drain()  # drop audio queued before the wake
                    endpointer.reset()
                    capturing = True
                    beep()
                    self.on_state("listening")
                continue

            if frame is None:
                continue
            state = endpointer.feed(frame)
            if state in (EndState.DONE, EndState.TOO_LONG):
                capturing = False
                beep(660, 70)
                self.on_state("transcribing")
                self.on_utterance(endpointer.audio())
            elif state is EndState.NO_SPEECH:
                capturing = False
                self.on_state("idle")

    def _drain(self) -> None:
        try:
            while True:
                self._frames.get_nowait()
        except queue.Empty:
            pass
        time.sleep(0)


def apps_using_microphone() -> list[str]:
    """Apps Windows says are using the mic right now (privacy indicator data).

    Windows records mic use per app under CapabilityAccessManager; an entry
    whose LastUsedTimeStop is 0 is in use now. Documented by behaviour, not by
    an official API, so failures return [] (wake word stays on).
    """
    if sys.platform != "win32":
        return []
    import winreg

    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    found: list[str] = []

    def scan(path: str) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    if name == "NonPackaged":
                        scan(path + "\\" + name)
                        continue
                    try:
                        with winreg.OpenKey(key, name) as app:
                            start, _ = winreg.QueryValueEx(app, "LastUsedTimeStart")
                            stop, _ = winreg.QueryValueEx(app, "LastUsedTimeStop")
                            if start and not stop:
                                found.append(name)
                    except OSError:
                        continue
        except OSError:
            return

    scan(base)
    return found


class CallDetector:
    """True while a call app holds the microphone (checked at most every 3 s)."""

    def __init__(self, names: list[str], probe: Callable[[], list[str]] = apps_using_microphone) -> None:
        self.names = [n.lower() for n in names]
        self.probe = probe
        self._last = -1e9
        self._value = False

    def __call__(self) -> bool:
        now = time.monotonic()
        if now - self._last > 3:
            self._last = now
            users = [u.lower() for u in self.probe()]
            self._value = any(n in u for n in self.names for u in users)
        return self._value
