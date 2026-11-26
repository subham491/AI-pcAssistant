"""Turn streamed markdown into speakable sentences.

Claude's reply streams in as small text deltas. To start talking early we
cut it into sentences as they complete, and we skip what should not be read
aloud: fenced code blocks, markdown symbols, raw URLs. Pure logic, tested.
"""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n{2,}|(?<=:)\n")
_URL = re.compile(r"https?://\S+")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_MD_SYMBOLS = re.compile(r"(\*\*|__|\*|#{1,6}\s|^\s*[-*+]\s|^\s*\d+\.\s|>\s)", re.M)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)


def clean_for_speech(text: str) -> str:
    text = _TABLE_ROW.sub(" ", text)
    text = _URL.sub("a link", text)
    text = _INLINE_CODE.sub(lambda m: m.group(1) if len(m.group(1)) <= 30 else "that code", text)
    text = _MD_SYMBOLS.sub(" ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # [label](link) -> label
    return re.sub(r"\s+", " ", text).strip()


class SpeechChunker:
    """feed() deltas, get back whole sentences to speak; flush() at the end."""

    def __init__(self, min_chars: int = 25) -> None:
        self._buf = ""
        self._in_fence = False
        self._announced_code = False
        self._pending_ticks = ""  # a partial ``` split across deltas
        self.min_chars = min_chars

    def feed(self, delta: str) -> list[str]:
        out: list[str] = []
        text = self._pending_ticks + delta
        self._pending_ticks = ""
        # Hold back trailing backticks: they may be the start of a fence.
        m = re.search(r"`+$", text)
        if m and len(m.group(0)) < 3:
            self._pending_ticks = m.group(0)
            text = text[: m.start()]
        while text:
            idx = text.find("```")
            if idx == -1:
                if not self._in_fence:
                    self._buf += text
                break
            if not self._in_fence:
                self._buf += text[:idx]
                out.extend(self._drain(final=True))
                if not self._announced_code:
                    out.append("I've put the code on screen.")
                    self._announced_code = True
            self._in_fence = not self._in_fence
            text = text[idx + 3:]
        out.extend(self._drain(final=False))
        return out

    def flush(self) -> list[str]:
        if not self._in_fence:
            self._buf += self._pending_ticks
        self._pending_ticks = ""
        return self._drain(final=True)

    def _drain(self, final: bool) -> list[str]:
        out: list[str] = []
        chunk_start = 0
        for m in _SENTENCE_END.finditer(self._buf):
            piece = self._buf[chunk_start: m.start()]
            # Merge tiny fragments ("1." "Ok.") with what follows.
            if len(piece.strip()) >= self.min_chars:
                spoken = clean_for_speech(piece)
                if spoken:
                    out.append(spoken)
                chunk_start = m.end()
        self._buf = self._buf[chunk_start:]
        if final:
            spoken = clean_for_speech(self._buf)
            self._buf = ""
            if spoken:
                out.append(spoken)
        return out
