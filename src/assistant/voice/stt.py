"""Local speech-to-text with faster-whisper. Audio never leaves this PC."""

from __future__ import annotations

import logging
import os
import site
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import VoiceConfig

log = logging.getLogger(__name__)


def add_nvidia_dll_dirs() -> list[str]:
    """Make pip-installed CUDA DLLs (nvidia-cublas-cu12, nvidia-cudnn-cu12) loadable.

    On Windows those wheels put DLLs in site-packages/nvidia/*/bin, which is not
    on the DLL search path, so CTranslate2 fails with "cublas64_12.dll not found".
    """
    if sys.platform != "win32":
        return []
    added = []
    for base in {*site.getsitepackages(), site.getusersitepackages()}:
        for bin_dir in Path(base, "nvidia").glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
                added.append(str(bin_dir))
            except OSError:
                continue
    return added


def pick_device(requested: str, compute_type: str) -> tuple[str, str]:
    """Resolve "auto" to cuda/float16 when an NVIDIA GPU is usable, else cpu/int8."""
    device = requested
    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:  # noqa: BLE001 - no CUDA runtime at all
            device = "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


DEFAULT_MODEL = "large-v3-turbo"
SLOW_FACTOR = 2.0  # transcription taking longer than this many times the audio length is "slow"


def resolve_model(name: str) -> str:
    """"auto" -> large-v3-turbo. The small models are the main cause of misheard words with
    non-American accents; turbo keeps large-v3's encoder (where accents are handled) with a
    4-layer decoder, so it stays quick. Anything else is passed to faster-whisper unchanged:
    a size name, a Hugging Face repo id of a CTranslate2 model, or a local folder."""
    return DEFAULT_MODEL if name.strip().lower() in ("", "auto") else name


def build_prompt(name: str, vocabulary: list[str]) -> str | None:
    """Words Whisper should expect, as a short glossary. The assistant's own name goes first
    so "Hellbot" is not heard as "hell bought"."""
    words = list(dict.fromkeys(w.strip() for w in [name, *vocabulary] if w and w.strip()))
    return ("Glossary: " + ", ".join(words) + ".") if words else None


class SpeechToText:
    def __init__(self, cfg: VoiceConfig, name: str = "", on_slow: Callable[[str], None] | None = None) -> None:
        self.cfg = cfg
        self.model_name = resolve_model(cfg.stt_model)
        self.prompt = build_prompt(name, cfg.vocabulary)
        self._on_slow = on_slow
        self._warned_slow = False
        self._model = None
        self._lock = threading.Lock()  # faster-whisper models are not re-entrant
        self.device = ""

    def load(self) -> None:
        from faster_whisper import WhisperModel

        add_nvidia_dll_dirs()
        device, compute = pick_device(self.cfg.stt_device, self.cfg.stt_compute_type)
        try:
            self._model = WhisperModel(self.model_name, device=device, compute_type=compute)
            self.device = device
        except Exception:
            if device == "cpu":
                raise
            log.warning("could not load Whisper on %s; falling back to CPU", device, exc_info=True)
            self._load_cpu()
        log.info("speech-to-text: %s on %s", self.model_name, self.device)

    def _load_cpu(self) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        self.device = "cpu"

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < 1600:  # < 0.1 s
            return ""
        with self._lock:
            if self._model is None:
                self.load()
            try:
                return self._run(audio)
            except RuntimeError as exc:
                # Missing cuBLAS/cuDNN DLLs only surface on first use on Windows.
                if self.device != "cuda":
                    raise
                log.warning("GPU transcription failed (%s); switching to CPU", exc)
                self._load_cpu()
                return self._run(audio)

    def _run(self, audio: np.ndarray) -> str:
        assert self._model is not None
        beam = self.cfg.stt_beam_size or (5 if self.device == "cuda" else 1)
        started = time.monotonic()
        segments, _info = self._model.transcribe(
            audio,
            language=self.cfg.language,
            initial_prompt=self.prompt,
            vad_filter=True,
            beam_size=beam,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()  # segments is lazy: work happens here
        took, length = time.monotonic() - started, audio.size / 16_000
        log.info("transcribed %.1fs of audio in %.1fs (%s, %s)", length, took, self.model_name, self.device)
        if took > max(3.0, SLOW_FACTOR * length) and not self._warned_slow and self._on_slow:
            self._warned_slow = True
            self._on_slow(
                f"Speech recognition took {took:.0f}s for {length:.0f}s of speech on this PC's {self.device.upper()}. "
                f'For speed over accuracy set stt_model = "small.en" under [voice] in config.toml.')
        return text
