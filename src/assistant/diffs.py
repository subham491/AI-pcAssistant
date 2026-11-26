"""Preview file edits as unified diffs, and remember enough to undo them.

Claude Code's Edit/Write/MultiEdit tools describe a change, not the result.
To show you a real diff before it is applied we replay the change on the
current file contents here. If the replay cannot match (the file changed
under us), the preview says so and the approval card shows the raw request.
"""

from __future__ import annotations

import difflib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_UNDO_FILE_BYTES = 5 * 1024 * 1024
MAX_PREVIEW_LINES = 400


@dataclass(frozen=True)
class EditPreview:
    path: Path
    before: str | None  # None = file does not exist yet
    after: str | None  # None = could not compute (see note)
    note: str = ""

    def unified(self) -> str:
        if self.after is None:
            return self.note
        diff = difflib.unified_diff(
            (self.before or "").splitlines(keepends=True),
            self.after.splitlines(keepends=True),
            fromfile=f"a/{self.path.name}" if self.before is not None else "/dev/null",
            tofile=f"b/{self.path.name}",
        )
        lines = list(diff)
        if len(lines) > MAX_PREVIEW_LINES:
            hidden = len(lines) - MAX_PREVIEW_LINES
            lines = lines[:MAX_PREVIEW_LINES] + [f"\n... {hidden} more diff lines not shown\n"]
        text = "".join(lines)
        return (self.note + "\n" + text).strip() if self.note else text or "(no textual change)"


def _read(path: Path) -> str | None:
    # newline="" keeps CRLF/LF exactly as on disk, so undo restores byte-for-byte.
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        with path.open(encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()


def _apply_one(text: str, old: str, new: str, replace_all: bool) -> str | None:
    if old == "":
        return new + text  # Claude Code uses empty old_string to create content
    if old not in text:
        return None
    return text.replace(old, new) if replace_all else text.replace(old, new, 1)


def preview_edit(tool_name: str, tool_input: dict[str, Any], path: Path) -> EditPreview:
    before = _read(path)
    if tool_name == "Write":
        return EditPreview(path, before, str(tool_input.get("content", "")))
    if tool_name == "Edit":
        result = _apply_one(before or "", str(tool_input.get("old_string", "")),
                            str(tool_input.get("new_string", "")), bool(tool_input.get("replace_all")))
        if result is None:
            return EditPreview(path, before, None, "Could not locate the text to replace; the file may have changed. "
                                                   "Approving will most likely fail harmlessly.")
        return EditPreview(path, before, result)
    if tool_name == "MultiEdit":
        text = before or ""
        for i, edit in enumerate(tool_input.get("edits") or []):
            nxt = _apply_one(text, str(edit.get("old_string", "")), str(edit.get("new_string", "")),
                             bool(edit.get("replace_all")))
            if nxt is None:
                return EditPreview(path, before, None, f"Edit #{i + 1} does not match the current file.")
            text = nxt
        return EditPreview(path, before, text)
    if tool_name == "NotebookEdit":
        src = str(tool_input.get("new_source", ""))
        mode = tool_input.get("edit_mode", "replace")
        return EditPreview(path, before, None, f"Notebook cell {mode} ({tool_input.get('cell_id', '?')}):\n{src}")
    return EditPreview(path, before, None, f"{tool_name}: no preview available.")


@dataclass(frozen=True)
class UndoEntry:
    path: Path
    before: str | None  # None = file was created by the edit; undo deletes it


class UndoJournal:
    """The last N approved edits, newest last. "undo that" pops one."""

    def __init__(self, depth: int = 20) -> None:
        self._entries: deque[UndoEntry] = deque(maxlen=depth)

    def record(self, path: Path) -> None:
        try:
            if path.exists() and path.stat().st_size > MAX_UNDO_FILE_BYTES:
                return
        except OSError:
            return
        self._entries.append(UndoEntry(path, _read(path)))

    def __len__(self) -> int:
        return len(self._entries)

    def undo_last(self) -> UndoEntry | None:
        if not self._entries:
            return None
        entry = self._entries.pop()
        if entry.before is None:
            entry.path.unlink(missing_ok=True)
        else:
            with entry.path.open("w", encoding="utf-8", newline="") as fh:
                fh.write(entry.before)
        return entry
