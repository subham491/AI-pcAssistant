"""Configuration: defaults in code, overrides in <home>/config.toml.

Every knob lives here so there is exactly one place to look. The TOML file
only needs the keys you want to change; anything missing keeps its default.
Unknown keys are rejected loudly, because a typo that silently does nothing
is worse than a startup error.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class EditMode(str, Enum):
    SUGGEST = "suggest"  # never writes; shows the diff / code to copy
    REVIEW = "review"  # every file change waits for approval (default)
    AUTO = "auto"  # git-tracked files inside the home folder apply without asking


class Guardrails(str, Enum):
    ON = "on"  # ask before consequential actions, block the forbidden list (default)
    NO_PROMPTS = "no_prompts"  # never ask; the forbidden list (credentials, registry, disks...) stays blocked
    OFF = "off"  # never ask, block nothing. Anything the model is told or tricked into doing runs.


class AuthMode(str, Enum):
    SUBSCRIPTION = "subscription"  # your Claude Pro/Max login (claude /login)
    API_KEY = "api_key"  # ANTHROPIC_API_KEY from the environment


@dataclass
class BrainConfig:
    auth_mode: AuthMode = AuthMode.SUBSCRIPTION
    model: str | None = None  # None = Claude Code's default for your plan
    resume_last_session: bool = True
    max_turns: int = 40
    # Load ~/.claude (user) and <home>/.claude + CLAUDE.md (project).
    setting_sources: list[str] = field(default_factory=lambda: ["user", "project"])


@dataclass
class VoiceConfig:
    enabled: bool = True
    # Off by default: the mic is only read after you click the orb or press the talk hotkey.
    wake_word_enabled: bool = False
    # openWakeWord pre-trained model name, or a path to your own .onnx model.
    wake_word_model: str = "hey_jarvis"
    wake_word_threshold: float = 0.5
    # Any phrase, e.g. "hellbot". When set, the phrase is spotted with Whisper instead of the
    # openWakeWord model above: no training needed, and it follows your own pronunciation.
    wake_phrase: str = ""
    # "auto" = large-v3-turbo (much better with accents than the small models). Any faster-whisper
    # size works ("small.en" is ~6x lighter), as does a Hugging Face repo id or folder of a
    # CTranslate2 Whisper model, e.g. a fine-tune for your accent.
    stt_model: str = "auto"
    stt_beam_size: int = 0  # 0 = auto: 5 on a GPU, 1 on the CPU (faster)
    stt_device: str = "auto"  # auto | cuda | cpu
    stt_compute_type: str = "auto"  # auto picks float16 on CUDA, int8 on CPU
    language: str | None = "en"
    # Words Whisper should expect (project names, jargon). Biggest accuracy win.
    vocabulary: list[str] = field(default_factory=list)
    # After a click / hotkey: False = keep listening until you click or press the hotkey again.
    stop_on_silence: bool = True
    end_silence_s: float = 2.0  # pause that ends what you are saying (stop_on_silence only)
    max_utterance_s: float = 120.0
    no_speech_timeout_s: float = 8.0  # give up if you say nothing after the click
    wake_end_silence_s: float = 0.8  # pause that ends speech heard in wake-phrase mode
    tts_enabled: bool = True
    tts_engine: str = "sapi"  # sapi (Windows voices) | pocket (Pocket TTS: natural, CPU-only)
    tts_rate: int = 185  # SAPI words per minute
    # sapi: substring of a Windows voice name. pocket: a built-in voice name ("javert"), or the
    # full path of a voice file made with `pocket-tts export-voice` (or a .wav to clone).
    tts_voice: str | None = None
    # Do not listen for the wake word while one of these apps is using the
    # microphone (you are on a call). Substring match, case-insensitive.
    mute_wake_when_mic_used_by: list[str] = field(
        default_factory=lambda: ["teams", "zoom", "webex", "slack", "discord", "skype"]
    )
    input_device: str | int | None = None  # None = Windows default mic


@dataclass
class UiConfig:
    name: str = "Hellbot"  # shown on the orb card and tray, and what the assistant calls itself
    corner: str = "bottom-right"  # bottom-right | bottom-left | top-right | top-left
    monitor: int = 0  # 0 = Windows primary; or 1, 2 as numbered by `pc-assistant doctor`
    answer_hide_s: float = 12.0  # unused: the answer card is gone. Kept so older config.toml files still load.
    trail: bool = True  # puffs above the orb that show each step Claude takes


@dataclass
class HotkeyConfig:
    talk: str = "<ctrl>+<alt>+<space>"  # pynput syntax
    stop: str = "<ctrl>+<alt>+q"  # kill switch
    toggle_overlay: str = "<ctrl>+<alt>+o"  # opens the type-instead-of-talking box (the name predates it)


@dataclass
class CaptureConfig:
    max_long_edge: int = 1568  # downscale before sending to the model
    # Index into the monitor list (1-based, as Windows Display Settings shows),
    # used for "main screen". 0 = Windows primary monitor.
    main_monitor: int = 0
    save_screenshots: bool = False
    # Windows whose process name matches are blacked out before anything is sent.
    redact_processes: list[str] = field(
        default_factory=lambda: [
            "KeePass.exe",
            "KeePassXC.exe",
            "1Password.exe",
            "Bitwarden.exe",
            "Dashlane.exe",
            "LastPass.exe",
        ]
    )
    # Foreground processes that mean "this is a remote machine" -> remote mode.
    remote_desktop_processes: list[str] = field(
        default_factory=lambda: ["mstsc.exe", "msrdc.exe", "Windows365.exe", "WindowsApp.exe"]
    )


@dataclass
class SafetyConfig:
    guardrails: Guardrails = Guardrails.ON
    edit_mode: EditMode = EditMode.REVIEW
    # Shell commands (prefix match, case-insensitive) that run without asking.
    bash_allow_prefixes: list[str] = field(
        default_factory=lambda: [
            "git status",
            "git diff",
            "git log",
            "git show",
            "git branch",
            "ls",
            "dir",
            "pwd",
            "cat ",
            "type ",
            "head ",
            "tail ",
            "wc ",
            "where ",
            "which ",
            "python --version",
            "node --version",
        ]
    )
    # MCP tools (from your connectors) whose name contains one of these run
    # without asking; everything else from MCP asks first.
    mcp_read_markers: list[str] = field(
        default_factory=lambda: ["get_", "list_", "search_", "read_", "fetch_", "query"]
    )
    extra_forbidden_paths: list[str] = field(default_factory=list)
    extra_forbidden_commands: list[str] = field(default_factory=list)
    approval_timeout_s: float = 300.0


@dataclass
class RemoteConfig:
    # Where her project lives on the remote laptop (used for "pull her project").
    remote_project_path: str = r"C:\Users\sister\Projects\project"
    project_name: str = "sister-project"
    # How long an inbox copy must sit unchanged before it is snapshotted.
    inbox_settle_s: float = 15.0
    robocopy_exclude_dirs: list[str] = field(
        default_factory=lambda: ["node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".git"]
    )
    robocopy_exclude_files: list[str] = field(default_factory=lambda: [".env", "*.pem", "*.key"])


@dataclass
class Config:
    home: Path
    brain: BrainConfig = field(default_factory=BrainConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    remote: RemoteConfig = field(default_factory=RemoteConfig)

    # Derived folders inside the home folder (see home_template/).
    @property
    def workspaces_dir(self) -> Path:
        return self.home / "workspaces"

    @property
    def inbox_dir(self) -> Path:
        return self.home / "inbox"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def memory_dir(self) -> Path:
        return self.home / "memory"

    @property
    def state_dir(self) -> Path:
        return self.home / ".state"


CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")


class ConfigError(ValueError):
    pass


def default_home() -> Path:
    env = os.environ.get("PC_ASSISTANT_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / "assistant"


_SECTIONS = {
    "brain": BrainConfig,
    "voice": VoiceConfig,
    "hotkeys": HotkeyConfig,
    "ui": UiConfig,
    "capture": CaptureConfig,
    "safety": SafetyConfig,
    "remote": RemoteConfig,
}


def _coerce(cls: type, section: str, raw: dict[str, Any]) -> Any:
    if not isinstance(raw, dict):
        raise ConfigError(f"[{section}] must be a table")
    known = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(raw) - set(known)
    if unknown:
        raise ConfigError(f"[{section}] unknown key(s): {', '.join(sorted(unknown))}")
    obj = cls()
    for key, value in raw.items():
        current = getattr(obj, key)
        if isinstance(current, bool) and not isinstance(value, bool):
            raise ConfigError(f"[{section}].{key} must be true or false")
        try:
            if isinstance(current, Enum):
                value = type(current)(value)
            elif isinstance(current, float) and isinstance(value, int):
                value = float(value)
        except ValueError as exc:
            allowed = ", ".join(m.value for m in type(current))  # type: ignore[union-attr]
            raise ConfigError(f"[{section}].{key}: {value!r} is not one of {allowed}") from exc
        setattr(obj, key, value)
    return obj


def load_config(home: Path | None = None) -> Config:
    """Load <home>/config.toml on top of the defaults."""
    home = (home or default_home()).resolve()
    cfg = Config(home=home)
    path = home / "config.toml"
    if not path.exists():
        return cfg
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    unknown = set(data) - set(_SECTIONS)
    if unknown:
        raise ConfigError(f"{path}: unknown section(s): {', '.join(sorted(unknown))}")
    for name, cls in _SECTIONS.items():
        if name in data:
            setattr(cfg, name, _coerce(cls, name, data[name]))
    if cfg.ui.corner not in CORNERS:
        raise ConfigError(f"[ui].corner: {cfg.ui.corner!r} is not one of {', '.join(CORNERS)}")
    return cfg
