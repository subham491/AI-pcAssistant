"""Speech out, interruptible: Windows built-in voices (SAPI) or Pocket TTS.

SAPI is local, already installed, and can be cut off mid-word, which the kill
switch and barge-in need. `tts_engine = "pocket"` uses Kyutai Pocket TTS for a
natural (optionally cloned) voice on the CPU; if it cannot load, SAPI takes
over. Off Windows SAPI just logs the text.
"""

from __future__ import annotations

import dataclasses
import logging
import queue
import sys
import threading

from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..config import VoiceConfig

log = logging.getLogger(__name__)

_SVSF_ASYNC = 1
_SVSF_PURGE = 2


class PocketVoice:
    """One Pocket TTS voice, played on whatever is the Windows default speaker right now."""

    def __init__(self, model: Any, state: Any, open_stream: Callable[[], Any]) -> None:
        self.model, self.state, self.open_stream = model, state, open_stream

    @classmethod
    def load(cls, voice: str | None) -> PocketVoice:
        import sounddevice as sd
        import torch
        from pocket_tts import TTSModel

        torch.set_num_threads(2)  # Pocket TTS needs two cores; leave the rest to Whisper and the UI
        model = TTSModel.load_model()
        # A voice file (.safetensors from `pocket-tts export-voice`, or a .wav) or a built-in name.
        path = Path(voice).expanduser() if voice else None
        state = model.get_state_for_audio_prompt(path if path and path.is_file() else voice or "alba")
        # PortAudio fixes "the default device" when it starts, so a plain default stream stays on
        # the speaker that was default at launch. The Sound Mapper resolves the default each open.
        mapper = next((i for i, d in enumerate(sd.query_devices())
                       if d["name"] == "Microsoft Sound Mapper - Output"), None)
        return cls(model, state, lambda: sd.OutputStream(samplerate=model.sample_rate, channels=1,
                                                         dtype="float32", device=mapper))

    def speak(self, text: str, stop: threading.Event) -> None:
        # A fresh stream per sentence follows the Windows default speaker if you switch it.
        with self.open_stream() as stream:
            for chunk in self.model.generate_audio_stream(self.state, text, stop=stop):
                if stop.is_set():
                    break
                stream.write(np.asarray(chunk, dtype="float32").reshape(-1, 1))
            if stop.is_set():  # drop what is still buffered so the cut-off is immediate
                stream.abort()


def reading_time(text: str) -> float:
    """Seconds a sentence stays up when it is shown but not spoken."""
    return min(7.0, 0.8 + 0.3 * len(text.split()))


class Speaker:
    def __init__(self, cfg: VoiceConfig) -> None:
        self.cfg = cfg
        self._q: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._speaking = threading.Event()
        # Called from the speech thread with each sentence as it starts, and None when it goes quiet.
        self.on_sentence: Callable[[str | None], None] | None = None
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set() or not self._q.empty()

    def say(self, text: str) -> None:
        # With the voice off the sentence is still queued: the bubble beside the orb shows it
        # for about as long as it takes to read, so a muted Hellbot still answers.
        if text.strip():
            self._q.put(text)

    def _notify(self, sentence: str | None) -> None:
        if self.on_sentence is not None:
            try:
                self.on_sentence(sentence)
            except Exception:  # noqa: BLE001 - a UI hiccup must not stop the voice
                log.debug("on_sentence failed", exc_info=True)

    def stop(self) -> None:
        """Cut off the current sentence and drop everything queued."""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self._stop.set()

    def close(self) -> None:
        self.stop()
        self._q.put(None)

    def _run(self) -> None:
        quiet = not self.cfg.tts_enabled
        pocket = self._init_pocket() if not quiet and self.cfg.tts_engine == "pocket" else None
        voice = None if pocket or quiet else self._init_sapi()
        while True:
            text = self._q.get()
            if text is None:
                return
            self._stop.clear()
            self._speaking.set()
            self._notify(text)
            try:
                if pocket is not None:
                    pocket.speak(text, self._stop)
                    continue
                if voice is None:
                    # Voice off (or no engine here): keep the sentence on screen for about a read.
                    if not quiet:
                        log.info("[tts] %s", text)
                    self._stop.wait(reading_time(text))
                    continue
                voice.Speak(text, _SVSF_ASYNC)
                while not voice.WaitUntilDone(50):
                    if self._stop.is_set():
                        voice.Speak("", _SVSF_ASYNC | _SVSF_PURGE)
                        break
            except Exception:  # noqa: BLE001 - never let TTS kill the app
                log.exception("speech failed")
            finally:
                self._speaking.clear()
                if self._q.empty():
                    self._notify(None)

    def _init_pocket(self) -> PocketVoice | None:
        try:
            return PocketVoice.load(self.cfg.tts_voice)
        except Exception:  # noqa: BLE001
            log.exception("Pocket TTS unavailable; falling back to the Windows voice")
            self.cfg = dataclasses.replace(self.cfg, tts_voice=None)  # a Pocket voice is not a SAPI name
            return None

    def _init_sapi(self):  # type: ignore[no-untyped-def]
        if sys.platform != "win32":
            return None
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            # SAPI rate is -10..10; map words-per-minute roughly (180 wpm ~ 0).
            voice.Rate = max(-10, min(10, round((self.cfg.tts_rate - 180) / 20)))
            if self.cfg.tts_voice:
                for v in voice.GetVoices():
                    if self.cfg.tts_voice.lower() in v.GetDescription().lower():
                        voice.Voice = v
                        break
            return voice
        except Exception:  # noqa: BLE001
            log.exception("SAPI unavailable; replies will be text only")
            return None
