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
    gemini_key_file: str = ""  # a text file holding a Gemini API key; the watchdog asks Gemini why a restart failed
    effort: str | None = None  # low | medium | high | xhigh | max; None = the model's default (high)
    resume_last_session: bool = True
    # A resumed session only grows and every turn re-reads all of it. When a turn ends with more than this
    # many tokens in context, the next turn starts in a fresh session. 0 = never.
    max_context_tokens: int = 150_000
    # The next turn also starts fresh after this long without one (the prompt cache is cold by then, so a
    # long session is paid for again in full). 0 = never.
    fresh_after_idle_minutes: float = 240.0
    max_turns: int = 40
    # The settings sources for the default (lean) voice profile. "project" loads <home>/.claude
    # (the assistant's own skills: tv-remote, window-control, gesture-mouse, restart-self,
    # explain-screen) and CLAUDE.md. It deliberately omits "user" (~/.claude), which drags in ~200
    # plugin skills and 30+ connector tool lists into every turn's context, cheap work or not. The
    # desktop MCP tools are wired in separately and are always available.
    setting_sources: list[str] = field(default_factory=lambda: ["project"])
    # False (default) = the lean profile above. True = also load the full ~/.claude profile
    # (all plugin skills and connectors) for heavy work like Gmail, Drive or docs. Flip it here, or
    # by voice ("full profile" / "lean profile"); switching starts a fresh session.
    full_profile: bool = False

    def profile_sources(self, full: bool) -> list[str]:
        """The settings sources for the lean (default) or full profile.

        Lean = whatever `setting_sources` lists (project only by default). Full adds the user's
        ~/.claude on top, so a voice command can pull in every connector and plugin on demand.
        """
        base = list(self.setting_sources)
        if full and "user" not in base:
            base = ["user"] + base
        return base


@dataclass
class LadderConfig:
    # Climb a rung on doubt (a hedged/low-confidence reply, a tool failure, a risky-work retry).
    # False = always run the job's chosen rung, never escalate.
    escalate: bool = True
    # The ladder never climbs above this rung, whatever the doubt. One of haiku|sonnet|opus|fable.
    max_rung: str = "fable"
    # Job type -> the model a FIRST attempt uses. "default" means [brain].model, so an empty/default
    # table keeps today's behaviour (everything on the configured model; the ladder only escalates).
    # Known job types: simple, code, risky, action, screen, general. Values: a rung
    # (haiku|sonnet|opus|fable) or "default". Risky work still starts one rung up and is floored at
    # Sonnet; see ladder.py.
    jobs: dict[str, str] = field(
        default_factory=lambda: {j: "default" for j in ("simple", "code", "risky", "action", "screen", "general")}
    )


@dataclass
class TreasurerConfig:
    # Spend caps in US dollars (the audit log's per-turn cost, the SDK's estimate at API prices).
    # 0 = off (no cap, no downgrade, no warning) — the default, so nothing changes until you opt in.
    daily_cap_usd: float = 0.0
    weekly_cap_usd: float = 0.0
    warn_at: float = 0.8  # fraction of a cap at which to warn once by voice
    downgrade_at: float = 0.9  # fraction at which to start capping the ladder lower (never below Sonnet for code/risky)


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
    # Conversation mode (wake_phrase only): for this many seconds after a reply ends, speak again without the
    # wake phrase. Each exchange renews it. 0 = off, the wake phrase is needed every time.
    follow_up_s: float = 25.0
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
    # Part of the name of a Wi-Fi speaker (UPnP/DLNA, e.g. "JBL") to speak through instead of the PC's
    # speaker. Pocket engine only; about 3 s slower to start. Falls back to the PC if it does not answer.
    tts_output: str = ""
    # A Samsung TV's status page, e.g. "http://192.168.1.7:8001/api/v2/". A soundbar plays one source at a
    # time, so while this says the TV is on, replies play on the PC and tts_output drops to a third meanwhile.
    tts_tv_status_url: str = ""
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
    # Longest side of a screenshot sent to the model; larger grabs are downscaled. An image costs about
    # width*height/750 tokens on every later turn of the session, so this is the main cost knob.
    max_long_edge: int = 1280
    # The same cap while remote mode is on: text on a remote screen is all there is to go on, so keep more detail.
    remote_max_long_edge: int = 1568
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
    ladder: LadderConfig = field(default_factory=LadderConfig)
    treasurer: TreasurerConfig = field(default_factory=TreasurerConfig)
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

    @property
    def resume_task_file(self) -> Path:
        """A task to continue after a restart: see schedule_resume()."""
        return self.state_dir / "resume_task.txt"


def schedule_resume(cfg: Config, text: str) -> Path:
    """Leave a task for the next start of the app to pick up (for example before restart-self).

    The format is deliberately trivial so a skill or script can write it without this helper: plain
    UTF-8 text in <home>/.state/resume_task.txt, the whole file being what the assistant should be told,
    exactly as if the user had said it. On start the app deletes the file and then runs that text as a
    normal turn; a missing or empty file means a normal start.
    """
    path = cfg.resume_task_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def take_resume_task(cfg: Config) -> str:
    """Read and delete the pending task; "" if there is none. Deleted first-thing so it runs only once."""
    path = cfg.resume_task_file
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")  # utf-8-sig: PowerShell writes a BOM
    except OSError:
        return ""
    try:
        path.unlink()
    except OSError:
        return ""  # cannot be consumed: running it would repeat on every start
    return text.strip()


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
    "ladder": LadderConfig,
    "treasurer": TreasurerConfig,
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
    _validate_ladder(cfg.ladder)
    return cfg


# Kept as literals (not an import of ladder.py) so config.py has no import-time dependency on it.
_RUNGS = ("haiku", "sonnet", "opus", "fable")
_JOBS = ("simple", "code", "risky", "action", "screen", "general")


def _validate_ladder(ladder: LadderConfig) -> None:
    """A typo in the job table or a bad rung name is rejected loudly, like every other unknown key."""
    if ladder.max_rung not in _RUNGS:
        raise ConfigError(f"[ladder].max_rung: {ladder.max_rung!r} is not one of {', '.join(_RUNGS)}")
    if not isinstance(ladder.jobs, dict):
        raise ConfigError("[ladder].jobs must be a table of job = \"model\"")
    bad_jobs = set(ladder.jobs) - set(_JOBS)
    if bad_jobs:
        raise ConfigError(f"[ladder].jobs unknown job type(s): {', '.join(sorted(bad_jobs))}; "
                          f"known: {', '.join(_JOBS)}")
    allowed = set(_RUNGS) | {"default"}
    bad_vals = {v for v in ladder.jobs.values() if v not in allowed}
    if bad_vals:
        raise ConfigError(f"[ladder].jobs values must be one of {', '.join(sorted(allowed))}; "
                          f"got: {', '.join(sorted(map(str, bad_vals)))}")
