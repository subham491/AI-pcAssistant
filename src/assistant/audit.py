"""Append-only audit log: one JSON line per tool decision and result.

Kept deliberately dumb (open, append, close) so it survives crashes and can
be read with any text editor or `Get-Content -Wait`.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MAX_FIELD = 2000  # keep the log readable; tool inputs can be whole files


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_FIELD:
        return value[:_MAX_FIELD] + f"... [{len(value) - _MAX_FIELD} chars clipped]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value[:50]]
    return value


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields: Any) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event}
        record.update(_clip(fields))
        line = json.dumps(record, ensure_ascii=False, default=str)
        try:
            with self._lock, self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            # Never let logging failures take down a turn; report and move on.
            log.exception("audit log write failed")
