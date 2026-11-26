"""Speech out with the Windows built-in voices (SAPI), interruptible.

SAPI is local, already installed, and can be cut off mid-word, which the kill
switch and barge-in need. Piper or Kokoro sound better; swap them in here if
you want (README "Customising"). Off Windows this just logs the text.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading

from ..config import VoiceConfig

log = logging.getLogger(__name__)

_SVSF_ASYNC = 1
_SVSF_PURGE = 2


class Speaker:
    def __init__(self, cfg: VoiceConfig) -> None:
        self.cfg = cfg
        self._q: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._speaking = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set() or not self._q.empty()

    def say(self, text: str) -> None:
        if self.cfg.tts_enabled and text.strip():
            self._q.put(text)

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
        voice = self._init_sapi()
        while True:
            text = self._q.get()
            if text is None:
                return
            self._stop.clear()
            self._speaking.set()
            try:
                if voice is None:
                    log.info("[tts] %s", text)
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
