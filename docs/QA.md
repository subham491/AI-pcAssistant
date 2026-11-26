# QA notes and Windows test plan

The automated suite (`python -m pytest -q`, 117 tests, Linux + Windows CI) covers the pure
logic. Everything below needs a real Windows desktop, a mic and your Claude login. Run it once
after setup and after every SDK upgrade. Each step says what you should see; anything else is
a bug worth noting with `logs\assistant.log` and `logs\audit.jsonl`.

## 0. Smoke (5 minutes)

| # | Do | Expect |
|---|---|---|
| 0.1 | `python -m assistant doctor` | All OK. WARN only for GPU (if none) |
| 0.2 | `python -m assistant text`, type `hello` | A reply within ~5 s. `audit.jsonl` has `session_connected` and `turn_done` |
| 0.3 | Close and re-run `text`, ask `what did I just say?` | It remembers (session resumed) |
| 0.4 | Set `auth_mode = "api_key"` with no key set, run `text` | Clear "ANTHROPIC_API_KEY is not set" message, no crash. Set it back |

## 1. Voice

| # | Do | Expect |
|---|---|---|
| 1.1 | Start the app, say "Hey Jarvis", pause, "what time is it" | Beep, status "Listening", second beep, transcript in overlay, spoken answer |
| 1.2 | Ctrl+Alt+Space, speak | Same without wake word |
| 1.3 | Ctrl+Alt+Space while it is talking | Speech stops immediately, it listens |
| 1.4 | Wake it and say nothing for 5 s | Returns to Ready, no turn sent |
| 1.5 | Join a Teams/Zoom test call, say "Hey Jarvis" | No wake (mic in use by call app). Hotkey still works |
| 1.6 | Let it read a long answer; check it does not wake itself | No self-trigger from its own voice |
| 1.7 | Ask for code ("write a python hello world") | Code appears with Copy button; it says "I've put the code on screen" instead of reading code |
| 1.8 | Speak a project-specific word that Whisper gets wrong, add it to `vocabulary`, restart, repeat | Recognised correctly |

## 2. Screen

| # | Do | Expect |
|---|---|---|
| 2.1 | Focus VS Code, say "look at this, what file is open?" | Correct file. Activity shows "Captured: focused window: Code.exe" |
| 2.2 | Click the overlay, then ask 2.1 again | Still captures VS Code, not the overlay |
| 2.3 | "what's on the left screen" / "right screen" / "both screens" | Correct monitor(s), by physical position |
| 2.4 | Set display scaling to 150% on one monitor, repeat 2.3 | Images are not cropped or offset |
| 2.5 | Open KeePassXC/1Password next to VS Code, ask "this screen" | Password manager area is black with "[hidden: ...]" |
| 2.6 | Minimise everything and ask "look at this" | Graceful message, turn still answered |

## 3. Edits and approvals

Use a scratch repo: `git init %USERPROFILE%\assistant\workspaces\scratch` with one committed file.

| # | Do | Expect |
|---|---|---|
| 3.1 | Review mode: "add a comment at the top of scratch/a.py" | Approval card with coloured diff. Say "apply": file changes, VS Code reloads |
| 3.2 | Same, say "reject" | File unchanged; Claude acknowledges and does not retry another way |
| 3.3 | Ignore a card for 5 minutes | Auto-rejected ("Approval timed out") |
| 3.4 | "undo that" after 3.1 | File restored byte-for-byte (check CRLF with `git diff`) |
| 3.5 | "suggest only mode", ask for an edit | No file written; code shown with Copy |
| 3.6 | "auto apply mode", edit a tracked file in scratch | Applies without a card; activity shows "auto-applied" |
| 3.7 | Auto mode, edit a file outside the home folder | Still asks |
| 3.8 | Auto mode, ask it to change `config.toml` | Still asks |
| 3.9 | Ask it to run `npm install` (or `pip list`) | Approval card with the exact command |
| 3.10 | Ask it to `git status` | Runs without asking |
| 3.11 | Ask it to read `%USERPROFILE%\.ssh\id_ed25519` (or any .kdbx) | Blocked, never asked; audit shows tier 3 deny |
| 3.12 | Add `"Bash(*)"` to `~/.claude/settings.json` allow list, ask for `format C:` style command | Still denied (PreToolUse hook). Remove the allow rule after |

## 4. Kill switch

| # | Do | Expect |
|---|---|---|
| 4.1 | Ask for a long task, press Ctrl+Alt+Q mid-way | Speech stops, "■ Stopped" within a second, status Ready |
| 4.2 | Ask it to run `ping -t localhost` (approve), then Ctrl+Alt+Q | Within ~5 s "Hard stop: ended N process(es)"; no `ping.exe` left in Task Manager |
| 4.3 | After 4.2, ask a follow-up | Reconnects and resumes the conversation |
| 4.4 | Approve a `press_keys` of `ctrl+shift+esc`; stop during it | No modifier key stuck afterwards |

## 5. Remote mode

| # | Do | Expect |
|---|---|---|
| 5.1 | Focus the Remote Desktop window, ask "what's this error?" | REMOTE badge appears; answer about the remote screen |
| 5.2 | In remote mode ask it to fix a file | No file/shell tools used; code shown with Copy button |
| 5.3 | Focus a local window and ask something | REMOTE badge goes away |
| 5.4 | Say "remote mode" with a local window focused | Stays remote until "local mode" |

## 6. Pull her project

| # | Do | Expect |
|---|---|---|
| 6.1 | Say "pull her project" | Overlay shows a robocopy command targeting `\\tsclient\C\...\inbox\<name>` |
| 6.2 | Run it on the remote laptop (drive redirection on) | ~15 s after it finishes: "New snapshot ..." and a file summary; `workspaces\<name>` has a commit |
| 6.3 | Change one file remotely, pull again | Summary lists exactly that file (M) |
| 6.4 | Pull with no changes | "no changes since last time", no new commit |
| 6.5 | Drop a `.zip` of a project in `inbox\` | Imported the same way |

## Edge cases and failure modes reviewed

- **Allow rules bypassing approval:** handled. The PreToolUse hook returns allow/ask/deny for
  every call; "ask" forces our approval card even if a settings rule would auto-allow.
- **Mode switched mid-turn:** `can_use_tool` re-classifies at approval time, so "suggest only"
  said while Claude is working blocks the next write.
- **Allow-list bypass by chaining** (`git status && del ...`): any `; & | > < \` $(` or newline
  disqualifies the allow list.
- **Path traversal** (`home\..\x`) in auto mode: resolved before the "inside home" check.
- **Zip-slip** in inbox zips: rejected.
- **Foreign `.git` inside a pulled copy:** never copied over the snapshot repo.
- **Same-size edit in the same second** during snapshots: index rebuilt so git re-hashes.
- **Stale session id** after Claude Code cleanup: falls back to a fresh session.
- **No mic / no GPU / CUDA DLLs missing:** app still runs (typing / CPU fallback).
- **Approval never answered:** times out to reject after `approval_timeout_s` (300 s).
- **Busy while a new request arrives:** refused with a spoken hint, never interleaved.
- **Known gaps:** see README "Known limitations".
