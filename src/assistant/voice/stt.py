"""Local speech-to-text with faster-whisper. Audio never leaves this PC."""

from __future__ import annotations

import logging
import os
import site
import sys
import threading
from pathlib import Path

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


class SpeechToText:
    def __init__(self, cfg: VoiceConfig) -> None:
        self.cfg = cfg
        self._model = None
        self._lock = threading.Lock()  # faster-whisper models are not re-entrant
        self.device = ""

    def load(self) -> None:
        from faster_whisper import WhisperModel

        add_nvidia_dll_dirs()
        device, compute = pick_device(self.cfg.stt_device, self.cfg.stt_compute_type)
        try:
            self._model = WhisperModel(self.cfg.stt_model, device=device, compute_type=compute)
            self.device = device
        except Exception:
            if device == "cpu":
                raise
            log.warning("could not load Whisper on %s; falling back to CPU", device, exc_info=True)
            self._load_cpu()
        log.info("speech-to-text: %s on %s", self.cfg.stt_model, self.device)

    def _load_cpu(self) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(self.cfg.stt_model, device="cpu", compute_type="int8")
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
        prompt = ", ".join(self.cfg.vocabulary) or None
        segments, _info = self._model.transcribe(
            audio,
            language=self.cfg.language,
            initial_prompt=prompt,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()
