"""Follow-up window, like Claude's hands-free voice mode: after a reply, keep listening without the wake phrase."""

from __future__ import annotations

import time
from typing import Callable

HOLDOFF_S = 0.7  # the room still rings after the speaker stops; do not hear our own tail as a follow-up


class Conversation:
    def __init__(self, follow_up_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.follow_up_s, self.clock = follow_up_s, clock
        self.last = self.last_speech = -1e9

    def speaking(self, is_speaking: bool) -> bool:
        """Wrap the speaker's flag: the listener polls it every frame, so this tracks when the reply ended."""
        now = self.clock()
        if is_speaking:
            self.last = self.last_speech = now
        return is_speaking or (self.follow_up_s > 0 and now - self.last_speech < HOLDOFF_S)

    def touch(self) -> None:
        """A voice turn was just sent: the conversation is alive."""
        self.last = self.clock()

    def is_open(self) -> bool:
        return self.clock() - self.last < self.follow_up_s  # follow_up_s = 0 is never open
