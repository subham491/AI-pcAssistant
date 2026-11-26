# AI PC Assistant: a voice co-worker for Windows

Talk to your PC and it works alongside you. Wake word and speech-to-text run **locally**
(your audio never leaves the machine). The brain is **Claude Code**, driven through the
Claude Agent SDK, so everything Claude Code can do (skills, subagents, plugins, MCP
connectors, CLAUDE.md) works here too. It looks at your screen when you ask, edits your
files with your approval, and asks before doing anything consequential.

```
 mic ─► wake word ─► end-of-speech ─► Whisper (local) ─► transcript ─┐
                                                                      ▼
 overlay ◄── reply text + code + approval cards ◄── orchestrator ──► Claude Code (Agent SDK)
 speaker ◄── sentences (Windows voices)                 ▲   │           │ tools
                                                        │   │           ▼
                    screenshots (focused window) ───────┘   └── safety policy (allow / ask / deny)
```

- **Design:** the approved blueprint is the source of truth for *why*:
  [architecture blueprint](https://claude.ai/artifact/DmaZegrFWC5BbqwKeqMcUi).
- **Status:** v0.1. Covers blueprint phases 1 to 3 (spike, ears and eyes, hands). Phase 4
  (watch mode, speaker verification, correction log) is not built yet. See
  [Known limitations](#known-limitations-and-tech-debt).

---

## 1. Setup (one script)

Requirements: Windows 10/11 x64, a microphone, a Claude Pro/Max subscription (or an API key).
An NVIDIA GPU makes speech-to-text much faster but is optional.

```powershell
git clone https://github.com/subham491/AI-pcAssistant.git
cd AI-pcAssistant
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

The script is safe to re-run. It installs Python 3.12 and Git if missing (winget, user scope,
no admin), creates `.venv`, installs the app, adds CUDA libraries if you have an NVIDIA GPU,
creates your home folder at `%USERPROFILE%\assistant`, downloads the models, adds a
**Start Menu > PC Assistant** shortcut, signs you in to Claude, and runs a health check.

Options: `-Startup` (start at sign-in), `-AssistantHome D:\assistant`, `-CpuOnly`,
`-SkipModels`, `-SkipLogin`.

### First run, in this order

```powershell
.\.venv\Scripts\python -m assistant doctor   # every line should be OK
.\.venv\Scripts\python -m assistant text     # type to it: no mic, no GUI. Proves sign-in works.
.\.venv\Scripts\python -m assistant run      # the full app (same as the Start Menu shortcut)
```

In `text` mode try: `look at this and tell me what app it is`, then
`create notes/hello.md with the word hi` (you get a diff, type `apply`).

### Sign-in and the auth switch

`auth_mode = "subscription"` (default) uses your Claude login. Sign in once with
`python -m assistant login` (type `/login`, finish in the browser, `/exit`).
To use an API key instead, set `auth_mode = "api_key"` in `config.toml` and set the
`ANTHROPIC_API_KEY` environment variable. In subscription mode the app deliberately hides
any `ANTHROPIC_API_KEY` from Claude Code so you never get billed by accident.

> Policy note from the blueprint: using your own subscription through the SDK for a personal,
> never-distributed app is not explicitly settled in Anthropic's docs. Keep it personal; if
> you ever share this app, switch to `api_key`.

---

## 2. Daily use

### Talking to it

| Do this | What happens |
|---|---|
| Say **"Hey Jarvis"**, then speak | Beep, it listens until you pause (~0.7 s), beeps again, transcribes locally |
| Press **Ctrl+Alt+Space**, then speak | Same, without the wake word. Also cuts off a reply that is being spoken |
| Type in the overlay's box | Same as speaking |
| Press **Ctrl+Alt+Q**, say "stop", or click **■ Stop** | Kill switch (see below) |
| **Ctrl+Alt+O** or click the tray icon | Show / hide the overlay |

The wake word pauses automatically while Teams, Zoom, Slack, etc. are using your microphone.
It also ignores its own voice while it is speaking.

### What it sees

Nothing, unless a turn asks. Phrases like *look, this, see, screen, error, window* attach a
screenshot of the **focused window** (not the whole screen; the overlay never counts as
focused). Say which screen if you mean something else:

| You say | Captured |
|---|---|
| "look at this" / "explain this error" | the focused window |
| "this screen" | the whole monitor the focused window is on |
| "left screen", "right screen", "main screen", "other screen" | that monitor (left/right by position) |
| "the screen with Teams" | the monitor showing that app |
| "both screens" | one image per monitor |

Claude can also take screenshots itself (`mcp__desktop__screenshot`). Password managers
(`redact_processes` in config) are blacked out before anything is sent.

### Voice commands handled locally (never sent to Claude)

| Say | Effect |
|---|---|
| "stop", "cancel" | kill switch |
| "apply", "yes", "go ahead" / "reject", "no" | answers the pending approval card (if none is pending, it is a normal message) |
| "suggest only mode", "review mode", "auto apply mode" | switches the edit mode |
| "remote mode" / "local mode" | forces remote mode on / back to automatic |
| "undo that" | reverts the last applied file edit |
| "remember that ..." | appends to `memory/about-me.md` |
| "pull her project" | shows the copy command for the remote laptop |
| "start over" | new Claude conversation |

### Edit modes (switch by voice or tray menu)

- **Suggest only:** never writes files. Shows the change with a **Copy** button.
- **Review each edit (default):** every file change shows a coloured diff; say "apply" or click.
- **Auto-apply:** git-tracked files inside the home folder apply without asking (the diff is still
  logged, "undo that" reverts). Anything else still asks. Changes to the assistant's own
  settings (`config.toml`, `.claude/settings.json`) always ask.

### Safety tiers (decided by code, not by the model)

| Tier | Examples | Behaviour |
|---|---|---|
| T0 observe | read files, search, screenshots, connector reads (`get_`, `list_`, `search_`...) | runs, logged |
| T1 reversible | file edits (per edit mode), allow-listed commands (`git status`, `ls`...), open a URL | runs, logged |
| T2 consequential | any other shell command, clicks/typing, launching apps, connector writes, unknown tools | **approval card** |
| T3 forbidden | credential files (.ssh, browser passwords, .kdbx...), registry, disk tools, `sudo`/runas, download-and-run pipes | blocked, never asked |

Every decision and result goes to `logs/audit.jsonl`. The hard denials run in a PreToolUse
hook, so even an allow rule in your `~/.claude/settings.json` cannot bypass them.

### Kill switch

1. Stops speech, rejects pending approvals, releases any held keys/mouse buttons, and asks
   Claude to stop (`interrupt()`).
2. If Claude has not stopped within 4 seconds, it kills Claude Code and every process it
   started, then reconnects (resuming the conversation) on your next request.

### Remote laptop (your sister's)

When the focused window is Remote Desktop (`mstsc.exe`, `msrdc.exe`, Windows App), remote mode
turns on by itself (orange **REMOTE** badge). In remote mode a separate Claude session runs
with **no file, shell or input tools at all**: it reads the screen, explains, and gives code
with a Copy button for you to paste. Clipboard redirection in Remote Desktop carries it across.
She should know when you ask the assistant to look at her screen.

**Pull a copy of her project** (so Claude can read the whole codebase):

1. One time: in Remote Desktop, *Show Options > Local Resources > More > Drives*, tick only the
   drive with your home folder. Set `[remote] remote_project_path` in `config.toml`.
2. Say "pull her project". Paste the shown `robocopy` command into a terminal **on her laptop**.
   It copies into your `inbox\` and skips `node_modules`, `.venv`, `.env`, keys.
3. Fifteen seconds after the copy stops changing, the app moves it to
   `workspaces\<project>\`, commits a dated snapshot, and shows what changed since last time.

It is a read copy: real changes still happen on her laptop.

---

## 3. Your home folder

```
%USERPROFILE%\assistant\
  CLAUDE.md            loads every session: who you are, how to work with you, your projects
  config.toml          all settings (restart after editing)
  memory\about-me.md   facts about you; "remember that ..." appends here
  knowledge\           reference files, read on demand
  workspaces\          code projects and snapshots (each its own git repo)
  inbox\               shared into Remote Desktop for "pull her project"
  notes\               things Claude writes for you
  logs\                assistant.log, audit.jsonl
  .claude\             skills\, agents\, settings.json for this folder
```

Claude Code starts with this folder as its working directory, so `CLAUDE.md`, `.claude\skills`
and `.claude\agents` here apply to every conversation. Your user-level `~/.claude` (skills,
plugins, MCP servers) loads too.

**Bringing over your claude.ai Project:** paste the Project instructions into `CLAUDE.md`, put
knowledge files in `knowledge\` (import only small, always-needed ones with
`@knowledge/file.md`), and paste your memory export into `memory\about-me.md`. Connectors you
added at claude.ai are available automatically when signed in with your subscription.

---

## 4. Improving and customising it

Most improvements need **no Python at all**:

| You want | Do this |
|---|---|
| It to know more about you or a project | Edit `CLAUDE.md` / `memory\about-me.md` |
| A repeatable procedure ("review my PR", "explain this screen") | Add a skill: `.claude\skills\<name>\SKILL.md` (see `explain-screen`) |
| A specialist helper | Add a subagent: `.claude\agents\<name>.md` (see `code-reviewer`) |
| Jira, Drive, GitHub... | Add an MCP server/connector in Claude Code (`claude mcp add ...`) or at claude.ai |
| Better recognition of your words | `[voice] vocabulary = [...]` in `config.toml` |
| A different wake word | `wake_phrase = "hellbot"` (any phrase, no training: spotted with Whisper, say "Hellbot, what time is it" in one go or "Hellbot", beep, then speak). Or `wake_word_model` = another openWakeWord model or your own `.onnx` |
| Faster/better speech-to-text | `stt_model` = `base.en` (faster) / `medium.en` (better) |
| More commands to run without asking | `[safety] bash_allow_prefixes` |
| Block more things | `[safety] extra_forbidden_commands` / `extra_forbidden_paths` (regex / path) |

### Code map (when you do want to change Python)

```
src/assistant/
  cli.py            commands: run, text, doctor, login, init
  app.py            wiring: Qt main thread, asyncio core thread, mic, hotkeys
  orchestrator.py   the hub: local intents, turns, approvals, kill switch, inbox watcher
  brain.py          Claude Agent SDK session, system prompt, policy hooks
  policy.py         safety tiers  <- change what needs approval here
  intents.py        voice commands and "which screen"  <- add a voice command here
  diffs.py          diff previews and undo
  inbox.py          "pull her project" snapshots
  config.py         every setting and its default
  desktop/          capture.py (screenshots), tools.py (Claude's desktop tools), control.py (input, UIA), win32.py
  voice/            listener.py (mic + wake word), endpoint.py, stt.py (Whisper), tts.py (SAPI), speech_text.py
  ui/               overlay.py (tray + overlay), sink.py (UI interface), markdown.py
```

Recipes:

- **New voice command:** add a `Kind` and a phrase set in `intents.py`, handle it in
  `Orchestrator.handle_text`, add a case to `tests/test_intents.py`.
- **New desktop tool for Claude:** add a `@tool` in `desktop/tools.py`, then give it a tier in
  `policy.py` (`DESKTOP_OBSERVE` / `DESKTOP_REVERSIBLE` / `DESKTOP_CONSEQUENTIAL`). Unclassified
  tools default to "ask".
- **Different voice (Piper, Kokoro):** replace `Speaker` in `voice/tts.py`; keep `say()`,
  `stop()` and `speaking`.

### Tests

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest -q
```

117 unit tests cover the policy, intents, screen selection, diffs/undo, speech chunking,
end-of-speech detection, inbox snapshots, the brain's permission plumbing and the orchestrator.
CI runs them on Windows and Linux. Manual test steps for the parts that need a real desktop
are in [docs/QA.md](docs/QA.md).

---

## 5. Decisions taken for you (change any of them)

The blueprint left these open. Each is a default in `config.toml` or a small code change.

| Question | Default chosen | Why / how to change |
|---|---|---|
| GPU | Auto: NVIDIA GPU if usable, else CPU | `stt_device`; setup adds CUDA libs when `nvidia-smi` exists |
| Language | English (`small.en`) | `language = "hi"` / `stt_model = "small"` for other or mixed languages |
| Wake word vs push-to-talk | Both on: "Hey Jarvis" (pre-trained) + Ctrl+Alt+Space | `wake_word_enabled = false` for hotkey only; custom wake word via your own model |
| Voice out | Windows built-in voices (SAPI) | Local, zero setup, interruptible. Piper is a swap-in |
| End-of-speech | Adaptive energy detector + Whisper's Silero VAD trim | Simple and deterministic; streaming Silero is a swap-in in `voice/endpoint.py` |
| Window capture | `mss` region grab of the focused window | Windows.Graphics.Capture is the upgrade (true per-window, no overlap) |
| Remote mode trigger | Automatic when an RDP client is focused, plus voice override | `remote_desktop_processes` |
| Undo | In-memory last 20 edits (restores exact bytes) | Lost on restart; git history is the backup |
| Busy behaviour | A new request while one runs is refused ("say stop") | Simpler than queueing; revisit if it annoys you |

---

## Known limitations and tech debt

Untested on Windows by the author (built on Linux). The policy, intents, diffs, inbox, brain
plumbing and orchestrator are unit-tested on both OSes, and the approval flow was verified
end-to-end against real Claude Code. **Everything touching the mic, speakers, screen, input,
UI Automation, tray and hotkeys needs your first run on Windows** (see docs/QA.md).

- Window screenshots grab the screen area of the window, so anything overlapping it is
  included. Windows.Graphics.Capture would fix this.
- "Stop" by voice needs the wake word or hotkey first; the always-on hotkey is the reliable
  kill switch.
- Unsaved VS Code buffers are not detected. Turn on VS Code *Auto Save* so disk is current.
- Claude Code's own VS Code diff integration is not used (unconfirmed under the SDK).
- No takeover detection while it clicks/types (every click/type is approved individually instead).
- Child processes are killed by walking the process tree (psutil), not a Windows Job Object.
- Native Windows Claude Code has no sandbox. Shell safety is the policy plus your approvals.
- Prompt injection: text on screen, in files or tickets can try to instruct Claude. The system
  prompt treats it as data and T2 approvals are the backstop; read approval cards.
- Not built yet (blueprint phase 4): watch mode, speaker verification, correction log.
- The SDK is pinned (`claude-agent-sdk==0.2.189`). Upgrade deliberately: bump it in
  `pyproject.toml`, re-run `setup.ps1`, run the tests and `assistant text`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `doctor` says Auth WARN / "I couldn't reach Claude" | `python -m assistant login`, then `python -m assistant text` |
| Wakes by mistake / misses you | `wake_word_threshold` up / down (0.3 to 0.7) |
| Cuts you off mid-sentence | `end_silence_s = 1.0` |
| Transcription slow | `stt_model = "base.en"`, or check `doctor` shows your GPU |
| `cublas64_12.dll not found` | Re-run `setup.ps1` (installs CUDA libs); it falls back to CPU meanwhile |
| Clicks land in the wrong place | Report it with the monitor layout from `doctor`; DPI awareness is set per monitor |
| Hotkey does nothing | Another app owns it; change `[hotkeys]` |
| Logs | `%USERPROFILE%\assistant\logs\assistant.log` and `audit.jsonl` |
