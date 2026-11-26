"""Split a markdown reply into prose and fenced code blocks (for Copy buttons)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE = re.compile(r"^```[ \t]*([\w+#.-]*)[^\n]*\n(.*?)^```[ \t]*$", re.M | re.S)


@dataclass(frozen=True)
class Segment:
    kind: str  # "prose" | "code"
    text: str
    language: str = ""


def split_code_blocks(markdown: str) -> list[Segment]:
    out: list[Segment] = []
    pos = 0
    for m in _FENCE.finditer(markdown):
        prose = markdown[pos: m.start()].strip()
        if prose:
            out.append(Segment("prose", prose))
        out.append(Segment("code", m.group(2).rstrip("\n"), m.group(1)))
        pos = m.end()
    tail = markdown[pos:].strip()
    if tail:
        out.append(Segment("prose", tail))
    return out
