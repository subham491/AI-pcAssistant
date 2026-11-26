"""Action safety policy: every tool call gets a tier before it runs.

Our code decides, not the model. The model can ask for anything; this module
answers allow / ask / deny. Two enforcement points use it (see brain.py):

* a PreToolUse hook, which runs for *every* tool call, including ones your
  ~/.claude settings auto-allow. It enforces the hard denials (T3, suggest-only,
  remote mode) so an allow rule can never bypass them.
* the can_use_tool callback, which handles "ask": it shows the approval card
  (with a diff for file edits) and waits for you.

Tiers (from the blueprint, section 7):
    T0 observe        - runs, logged
    T1 reversible     - runs, logged (file edits follow the edit mode)
    T2 consequential  - asks you first
    T3 forbidden      - blocked, never asked
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path, PurePath
from typing import Any, Callable, Literal

from .config import Config, EditMode

DESKTOP_SERVER = "desktop"  # name of our in-process MCP server


def desktop_tool(name: str) -> str:
    return f"mcp__{DESKTOP_SERVER}__{name}"


class Tier(IntEnum):
    OBSERVE = 0
    REVERSIBLE = 1
    CONSEQUENTIAL = 2
    FORBIDDEN = 3


Action = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class Decision:
    tier: Tier
    action: Action
    reason: str
    file_path: Path | None = None  # set for file edits

    @property
    def is_file_edit(self) -> bool:
        return self.file_path is not None


FILE_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
READ_TOOLS = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
# Built-ins with no side effects outside the session.
SAFE_BUILTINS = {
    "TodoWrite", "Task", "Agent", "Skill", "WebSearch", "WebFetch", "ToolSearch",
    "BashOutput", "KillBash", "KillShell", "ListMcpResourcesTool", "ReadMcpResourceTool",
    "ExitPlanMode", "TaskOutput", "TaskStop",
}

DESKTOP_OBSERVE = {desktop_tool(n) for n in ("screenshot", "list_monitors", "list_windows", "ui_tree")}
DESKTOP_REVERSIBLE = {desktop_tool(n) for n in ("open_url", "show_code")}
DESKTOP_CONSEQUENTIAL = {desktop_tool(n) for n in ("open_app", "click", "type_text", "press_keys", "click_element")}

# Credentials and system internals. Matched case-insensitively against the
# path with forward slashes. Reading these is as bad as writing them: the
# contents would leave the machine.
FORBIDDEN_PATH_PATTERNS = [
    r"/\.ssh(/|$)",
    r"/\.gnupg(/|$)",
    r"/\.aws/credentials$",
    r"/\.azure(/|$)",
    r"/\.kube/config$",
    r"/\.docker/config\.json$",
    r"/\.git-credentials$",
    r"/\.netrc$",
    r"/\.claude/\.credentials\.json$",
    r"/appdata/(roaming|local)/microsoft/(credentials|protect|vault)(/|$)",
    r"/windows/system32/config(/|$)",
    r"\.(kdbx|kdb|pfx|p12|ppk)$",
    r"/id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$",
    r"/(login data|cookies|web data)$",  # Chromium profile stores
    r"/(logins\.json|key[34]\.db|cookies\.sqlite)$",  # Firefox profile stores
    r"/logs/audit\.jsonl$",  # the assistant's own audit trail
]

# Always ask, whatever the edit mode: these change what the assistant may do.
SENSITIVE_EDIT_PATTERNS = [
    r"/\.claude/settings(\.local)?\.json$",
    r"/config\.toml$",
    r"/\.mcp\.json$",
]

FORBIDDEN_COMMAND_PATTERNS = [
    r"\breg(\.exe)?\s+(add|delete|import|load|restore|unload|copy)\b",
    r"\b(set|new|remove)-itemproperty\b.*\bhk(lm|cu|ey_)",
    r"\bformat(\.com)?\s+[a-z]:",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\bcipher(\.exe)?\s+/w\b",
    r"\bvssadmin\s+delete\b",
    r"\bwbadmin\s+delete\b",
    r"\bclear-disk\b|\binitialize-disk\b|\bformat-volume\b|\bremove-partition\b",
    r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(/|~|\$home|[a-z]:[\\/]?)(\s|$)",
    r"\bremove-item\b.*-recurse.*\s([a-z]:[\\/]?|~|\$env:userprofile|\$home)\s*$",
    r"\b(rd|rmdir)\s+/s\b.*\s[a-z]:[\\/]?\s*$",
    r"\bshutdown(\.exe)?\b|\brestart-computer\b|\bstop-computer\b",
    r"\bnet\s+(user|localgroup)\b",
    r"\b(set|add)-mppreference\b",
    r"\bnetsh\s+advfirewall\b",
    r"\bset-executionpolicy\b",
    r"\brunas\b|-verb\s+runas\b|\bsudo\b",
    r"\bcmdkey\b|\bvaultcmd\b|\bmimikatz\b",
    r"\b(iwr|irm|invoke-webrequest|invoke-restmethod|curl|wget)\b.*\|\s*(iex|invoke-expression|sh|bash|powershell|pwsh)\b",
    r"\bschtasks(\.exe)?\s+/(create|change|delete)\b",
    r"\bsc(\.exe)?\s+(create|config|delete)\b",
    r"\btakeown\b|\bicacls\b.*\s/grant\b",
]

# Shell metacharacters that chain or redirect; an allow-listed prefix followed
# by one of these is no longer "just git status".
_SHELL_CHAINING = re.compile(r"[;&|><`\n]|\$\(")

GitTrackedProbe = Callable[[Path], bool]


def git_tracked_or_new_in_repo(path: Path) -> bool:
    """True if `path` is git-tracked, or does not exist yet but sits in a work tree.

    Existing untracked/ignored files return False: overwriting them cannot be
    undone from git, so auto-apply must ask.
    """
    folder = path.parent
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    try:
        inside = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return False
        if not path.exists():
            return True
        tracked = subprocess.run(
            ["git", "-C", str(path.parent), "ls-files", "--error-unmatch", path.name],
            capture_output=True, text=True, timeout=5,
        )
        return tracked.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _posix(path: str | PurePath) -> str:
    return str(path).replace("\\", "/").lower()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _paths_in(tool_input: dict[str, Any]) -> list[str]:
    keys = ("file_path", "notebook_path", "path")
    return [str(tool_input[k]) for k in keys if isinstance(tool_input.get(k), str) and tool_input[k]]


class Policy:
    """Stateful only in the two switches you flip by voice: edit mode and remote mode."""

    def __init__(self, cfg: Config, git_probe: GitTrackedProbe = git_tracked_or_new_in_repo) -> None:
        self.cfg = cfg
        self.edit_mode: EditMode = cfg.safety.edit_mode
        self.remote_mode = False
        self._git_probe = git_probe
        self._forbidden_paths = [re.compile(p, re.I) for p in FORBIDDEN_PATH_PATTERNS]
        self._forbidden_paths += [re.compile(re.escape(_posix(p)), re.I) for p in cfg.safety.extra_forbidden_paths]
        self._sensitive_edits = [re.compile(p, re.I) for p in SENSITIVE_EDIT_PATTERNS]
        self._forbidden_cmds = [re.compile(p, re.I) for p in FORBIDDEN_COMMAND_PATTERNS]
        self._forbidden_cmds += [re.compile(p, re.I) for p in cfg.safety.extra_forbidden_commands]

    # -- helpers ---------------------------------------------------------

    def _resolve(self, raw: str) -> Path:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (self.cfg.home / p)

    def forbidden_path(self, raw: str) -> bool:
        posix = _posix(self._resolve(raw))
        return any(r.search(posix) for r in self._forbidden_paths)

    def forbidden_command(self, command: str) -> str | None:
        for r in self._forbidden_cmds:
            if r.search(command):
                return r.pattern
        return None

    def command_allow_listed(self, command: str) -> bool:
        cmd = command.strip()
        if _SHELL_CHAINING.search(cmd):
            return False
        low = cmd.lower()
        for prefix in self.cfg.safety.bash_allow_prefixes:
            p = prefix.lower()
            # "ls" must match "ls" and "ls -la", not "lsblk".
            if low == p.strip() or low.startswith(p if p.endswith(" ") else p + " "):
                return True
        return False

    # -- the decision ----------------------------------------------------

    def classify(self, tool_name: str, tool_input: dict[str, Any]) -> Decision:
        # 1. Credentials and system files: blocked for every tool.
        for raw in _paths_in(tool_input):
            if self.forbidden_path(raw):
                return Decision(Tier.FORBIDDEN, "deny", f"{raw} is on the protected list (credentials/system files).")

        # 2. File edits follow the edit mode.
        if tool_name in FILE_EDIT_TOOLS:
            return self._classify_edit(tool_name, tool_input)

        # 3. Shell.
        if tool_name in SHELL_TOOLS:
            command = str(tool_input.get("command", ""))
            if self.remote_mode:
                return Decision(Tier.FORBIDDEN, "deny",
                                "Remote mode: shell commands would run on THIS PC, not the remote one. Suggest the "
                                "command as text instead.")
            if pattern := self.forbidden_command(command):
                return Decision(Tier.FORBIDDEN, "deny", f"Command matches a forbidden pattern ({pattern}).")
            if self.command_allow_listed(command):
                return Decision(Tier.REVERSIBLE, "allow", "Allow-listed read-only command.")
            return Decision(Tier.CONSEQUENTIAL, "ask", "Shell command outside the allow list.")

        # 4. Read-only built-ins.
        if tool_name in READ_TOOLS:
            if self.remote_mode:
                return Decision(Tier.FORBIDDEN, "deny",
                                "Remote mode: Read/Glob/Grep see THIS PC's files, not the remote laptop's.")
            return Decision(Tier.OBSERVE, "allow", "Read-only.")
        if tool_name in SAFE_BUILTINS:
            return Decision(Tier.OBSERVE, "allow", "No side effects outside the session.")

        # 5. Our desktop tools.
        if tool_name in DESKTOP_OBSERVE:
            return Decision(Tier.OBSERVE, "allow", "Look only.")
        if tool_name in DESKTOP_REVERSIBLE:
            return Decision(Tier.REVERSIBLE, "allow", "Opens or shows something; nothing is changed.")
        if tool_name in DESKTOP_CONSEQUENTIAL:
            return Decision(Tier.CONSEQUENTIAL, "ask", "Controls your mouse/keyboard or launches a program.")

        # 6. Other MCP servers (your connectors: Jira, Drive, ...).
        if tool_name.startswith("mcp__"):
            short = tool_name.split("__")[-1].lower()
            if any(marker in short for marker in self.cfg.safety.mcp_read_markers):
                return Decision(Tier.OBSERVE, "allow", "Connector read.")
            return Decision(Tier.CONSEQUENTIAL, "ask", "Connector action that may change something.")

        # 7. Anything new or unknown: ask. Safe default when Claude Code adds tools.
        return Decision(Tier.CONSEQUENTIAL, "ask", f"Unknown tool {tool_name}.")

    def _classify_edit(self, tool_name: str, tool_input: dict[str, Any]) -> Decision:
        raw = _paths_in(tool_input)
        if not raw:
            return Decision(Tier.CONSEQUENTIAL, "ask", f"{tool_name} without a file path.")
        path = self._resolve(raw[0])
        if self.remote_mode:
            return Decision(Tier.FORBIDDEN, "deny",
                            "Remote mode: file tools would change THIS PC, not the remote laptop. Show the code "
                            "with show_code so the user can paste it.", path)
        if self.edit_mode is EditMode.SUGGEST:
            return Decision(Tier.FORBIDDEN, "deny",
                            "Suggest-only mode is on: do not write files. Show the change as a diff or a code "
                            "block (show_code) and explain it.", path)
        if any(r.search(_posix(path)) for r in self._sensitive_edits):
            return Decision(Tier.CONSEQUENTIAL, "ask", "Changes the assistant's own settings.", path)
        if self.edit_mode is EditMode.AUTO:
            if _is_within(path, self.cfg.home) and self._git_probe(path):
                return Decision(Tier.REVERSIBLE, "allow", "Auto-apply: git-tracked file inside the home folder.", path)
            return Decision(Tier.CONSEQUENTIAL, "ask", "Auto-apply covers only git-tracked files inside the home "
                                                         "folder; this one needs your OK.", path)
        return Decision(Tier.REVERSIBLE, "ask", "Review mode: every edit waits for you.", path)
