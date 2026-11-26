"""End-of-speech detection: when did you stop talking?

An adaptive energy endpointer: it learns the room's noise floor while you
are quiet and calls a frame speech when it is clearly above it. Pure numpy,
deterministic, unit-tested. faster-whisper's built-in Silero VAD then trims
any leftover silence/noise inside the captured audio before transcription,
so this only needs to be good at *where to stop*, not at perfect labelling.

Two things keep it from cutting you off mid-sentence: once you are speaking,
a quieter level still counts as speech (`keep_ratio`, so trailing-off words are
not read as silence), and `stop_on_silence=False` makes it wait for `finish()`
(click-to-stop) instead of any pause.

Swap-in point: replace `Endpointer` with a streaming Silero VAD if a noisy
room still confuses it (see README "Customising").
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

import numpy as np

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1_280  # 80 ms; also what openWakeWord expects


class EndState(str, Enum):
    WAITING = "waiting"  # no speech yet
    SPEAKING = "speaking"
    DONE = "done"  # speech then enough silence
    NO_SPEECH = "no_speech"  # timed out without hearing anything
    TOO_LONG = "too_long"  # hit max length; audio is still usable


def rms(frame: np.ndarray) -> float:
    if frame.size == 0:
        return 0.0
    f = frame.astype(np.float32)
    return float(np.sqrt(np.mean(f * f)))


@dataclass
class EndpointerConfig:
    end_silence_s: float = 0.7
    max_utterance_s: float = 30.0
    no_speech_timeout_s: float = 5.0
    preroll_s: float = 0.3
    ratio: float = 3.0  # speech must be this many times the noise floor
    abs_min_rms: float = 300.0  # int16 units; below this is never speech
    min_speech_s: float = 0.25  # ignore clicks/bumps shorter than this
    keep_ratio: float = 1.6  # once speaking, this many times the noise floor still counts as speech
    stop_on_silence: bool = True  # False: only finish() or max_utterance_s ends the capture


class Endpointer:
    def __init__(self, cfg: EndpointerConfig, noise_floor: float = 200.0) -> None:
        self.cfg = cfg
        self.noise = noise_floor
        frame_s = FRAME_SAMPLES / SAMPLE_RATE
        self._frame_s = frame_s
        # Room for the frames that confirm speech (min_speech_s) plus preroll_s from before them.
        # Sized for preroll_s alone, the confirming frames push all the earlier audio out, and a
        # soft first sound ("h") is lost: Whisper then drops or garbles the first word.
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, round((cfg.preroll_s + cfg.min_speech_s) / frame_s)))
        self.reset()

    def reset(self) -> None:
        self.state = EndState.WAITING
        self._frames: list[np.ndarray] = []
        self._elapsed = 0.0
        self._speech_s = 0.0
        self._silence_s = 0.0
        self.level = 0.0  # RMS of the last frame, for the on-screen voice meter
        self._preroll.clear()

    def is_speech(self, level: float) -> bool:
        return level > max(self.noise * self.cfg.ratio, self.cfg.abs_min_rms)

    def still_speech(self, level: float) -> bool:
        """Lower bar used mid-utterance: soft word endings and quiet speakers stay 'speech'."""
        scale = self.cfg.keep_ratio / self.cfg.ratio
        return level > max(self.noise * self.cfg.keep_ratio, self.cfg.abs_min_rms * scale)

    def finish(self) -> EndState:
        """The user said "done" (second click / hotkey). Whatever was captured is the utterance."""
        if self.state is EndState.SPEAKING:
            self.state = EndState.DONE
        elif self.state is EndState.WAITING:
            self.state = EndState.NO_SPEECH
        return self.state

    def feed(self, frame: np.ndarray) -> EndState:
        if self.state in (EndState.DONE, EndState.NO_SPEECH, EndState.TOO_LONG):
            return self.state
        level = rms(frame)
        self.level = level
        speech = self.still_speech(level) if self.state is EndState.SPEAKING else self.is_speech(level)
        self._elapsed += self._frame_s

        if self.state is EndState.WAITING:
            if speech:
                self._speech_s += self._frame_s
                self._preroll.append(frame)
                if self._speech_s >= self.cfg.min_speech_s:
                    self.state = EndState.SPEAKING
                    self._frames = list(self._preroll)
            else:
                self._speech_s = 0.0
                # Learn the noise floor only from non-speech frames.
                self.noise = 0.95 * self.noise + 0.05 * level
                self._preroll.append(frame)
                if self._elapsed >= self.cfg.no_speech_timeout_s:
                    self.state = EndState.NO_SPEECH
            return self.state

        # SPEAKING
        self._frames.append(frame)
        if speech:
            self._silence_s = 0.0
        else:
            self._silence_s += self._frame_s
            if self.cfg.stop_on_silence and self._silence_s >= self.cfg.end_silence_s:
                self.state = EndState.DONE
        if self.state is EndState.SPEAKING and len(self._frames) * self._frame_s >= self.cfg.max_utterance_s:
            self.state = EndState.TOO_LONG
        return self.state

    def audio(self) -> np.ndarray:
        """Captured utterance as float32 in [-1, 1], what Whisper wants."""
        if not self._frames:
            return np.zeros(0, dtype=np.float32)
        return (np.concatenate(self._frames).astype(np.float32) / 32768.0).clip(-1.0, 1.0)
