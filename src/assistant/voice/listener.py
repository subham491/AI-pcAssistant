"""Microphone loop: click / hotkey (or optional wake word) -> capture one utterance -> callback.

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


def voice_level(rms_int16: float) -> float:
    """Map microphone RMS to 0..1 for the on-screen meter (square root: quiet speech still shows)."""
    return float(min(1.0, max(0.0, rms_int16 / 6000.0)) ** 0.5)


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
        on_utterance: Callable[[np.ndarray, bool], None],  # (audio, needs_wake_phrase)
        on_state: Callable[[str], None],
        is_speaking: Callable[[], bool],
        wake_muted: Callable[[], bool],
        on_level: Callable[[float], None] = lambda _level: None,  # 0..1 while you talk, for the orb
    ) -> None:
        self.cfg = cfg
        self.on_utterance = on_utterance
        self.on_state = on_state
        self.is_speaking = is_speaking
        self.wake_muted = wake_muted
        self.on_level = on_level
        self._frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._trigger = threading.Event()
        self._cancel = threading.Event()
        self._closing = threading.Event()
        self._wake: WakeWord | None = None
        self._stream = None
        self._thread = threading.Thread(target=self._run, name="listener", daemon=True)

    def start(self) -> None:
        import sounddevice as sd

        if self.cfg.wake_word_enabled and not self.cfg.wake_phrase:
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
        """Click / hotkey to talk: start capturing now; while capturing, finish and send."""
        self._trigger.set()

    def cancel(self) -> None:
        """Stop listening and throw away what was heard (kill switch)."""
        self._cancel.set()

    def _on_audio(self, indata, _frames, _time, status) -> None:  # type: ignore[no-untyped-def]
        if status:
            log.debug("audio status: %s", status)
        try:
            self._frames.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # we are behind; dropping audio beats unbounded memory

    def _run(self) -> None:
        v = self.cfg
        # After a click / hotkey you get a patient endpointer (long pauses are fine, or no
        # silence stop at all). Room speech in wake-phrase mode keeps a short one.
        manual_ep = Endpointer(EndpointerConfig(
            end_silence_s=v.end_silence_s, max_utterance_s=v.max_utterance_s,
            no_speech_timeout_s=v.no_speech_timeout_s, stop_on_silence=v.stop_on_silence))
        ambient_ep = Endpointer(EndpointerConfig(
            end_silence_s=v.wake_end_silence_s, max_utterance_s=30.0, no_speech_timeout_s=5.0))
        endpointer = ambient_ep
        phrase_mode = bool(v.wake_word_enabled and v.wake_phrase)
        capturing = False
        # Wake-phrase mode: room speech is captured quietly and handed to STT, which keeps it
        # only if it starts with the phrase. `ambient` marks such a capture (no beep, no status).
        # shortcut: continuous talk (TV, music) is transcribed in 30 s chunks and
        # dropped; add a cheaper pre-filter if that GPU use ever matters.
        ambient = False
        while not self._closing.is_set():
            try:
                frame = self._frames.get(timeout=0.2)
            except queue.Empty:
                frame = None

            if self._cancel.is_set():
                self._cancel.clear()
                self._trigger.clear()
                if capturing and not ambient:
                    self.on_level(0.0)
                    self.on_state("idle")
                capturing = ambient = False
                continue

            if not capturing or ambient:
                woke = self._trigger.is_set()
                may_listen = frame is not None and not woke and not self.is_speaking() and not self.wake_muted()
                if may_listen and self._wake:
                    woke = self._wake.detect(frame)
                elif may_listen and phrase_mode:
                    if not ambient:
                        endpointer = ambient_ep
                        endpointer.reset()
                    capturing = ambient = True
                elif frame is not None and not woke:
                    capturing = ambient = False  # speaking or on a call: drop a half-heard capture
                    ambient_ep.noise = 0.95 * ambient_ep.noise + 0.05 * float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
                if woke:
                    self._trigger.clear()
                    self._drain()  # drop audio queued before the wake
                    # what the room sounded like until now
                    manual_ep.noise, manual_ep.room = ambient_ep.noise, ambient_ep.room
                    endpointer = manual_ep
                    endpointer.reset()
                    capturing, ambient = True, False
                    beep()
                    self.on_state("listening")
                    continue
                if not ambient:
                    continue

            if not ambient and self._trigger.is_set():  # second click / hotkey: "I'm done"
                self._trigger.clear()
                state = endpointer.finish()
            elif frame is None:
                continue
            else:
                state = endpointer.feed(frame)
                if not ambient:
                    self.on_level(voice_level(endpointer.level))
            if state in (EndState.DONE, EndState.TOO_LONG):
                capturing = False
                if not ambient:
                    beep(660, 70)
                    self.on_level(0.0)
                    self.on_state("transcribing")
                self.on_utterance(endpointer.audio(), ambient)
                ambient = False
            elif state is EndState.NO_SPEECH:
                capturing = False
                if not ambient:
                    self.on_level(0.0)
                    self.on_state("idle")
                ambient = False

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
    # Windows leaves "in use" records behind when an app crashes or updates while holding the
    # mic (old Discord versions, games). Without this check they mute the wake word forever.
    # shortcut: only desktop apps (exe-path keys) are checked; packaged apps are trusted as-is,
    # upgrade if a Store app ever leaves a stale record.
    if any("#" in name for name in found):
        try:
            running = _running_exes()
        except Exception:  # noqa: BLE001 - same contract as above: failures keep the wake word on
            return []
        found = [name for name in found if "#" not in name or exe_is_running(name, running)]
    return found


def exe_is_running(key_name: str, running: set[str]) -> bool:
    r"""Desktop apps are recorded by exe path with '#' in place of '\'."""
    return key_name.replace("#", "\\").lower() in running


def _running_exes() -> set[str]:
    import psutil

    return {p.info["exe"].lower() for p in psutil.process_iter(["exe"]) if p.info["exe"]}


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
