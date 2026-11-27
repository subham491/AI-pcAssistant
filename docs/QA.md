# QA notes and Windows test plan

The automated suite (`python -m pytest -q`, Linux + Windows CI) covers the pure
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

## 1. Voice and the orb

| # | Do | Expect |
|---|---|---|
| 1.1 | Start the app and say something without clicking | Orb stays asleep (eyes closed, slow wisps). Nothing is transcribed; `assistant.log` has no "transcribed" line |
| 1.2 | Click the orb (or Ctrl+Alt+Space), say "what time is it", stop talking | Beep, eyes open, orb glows red and swells with your voice. ~2 s after you stop: second beep, amber "thinking" smoke, your words appear beside the orb as a small wisp, then the spoken answer |
| 1.3 | Click, say half a sentence, pause for 1.5 s, finish it | The whole sentence is transcribed (this is the "stops listening midway" bug) |
| 1.4 | Click, speak, click again straight away | Stops listening at once, no 2 s wait |
| 1.5 | Set `stop_on_silence = false`, restart, click, speak, wait 6 s, click | Still listening during the wait; ends only on the second click |
| 1.6 | Click and say nothing for 8 s | Goes back to sleep, no turn sent |
| 1.7 | Click the orb while it is talking | Speech stops immediately, it listens |
| 1.8 | Read 5 sentences you would really say (names, jargon, numbers) | Transcript matches. Note any misheard words; add recurring ones to `vocabulary` |
| 1.9 | First run after this update | Activity log: "Loading speech model large-v3-turbo…" then "Speech model ready on CUDA" (or CPU). On CPU, a one-time note if transcription is slow |
| 1.10 | Click on the desktop just outside the smoke; drag the orb; restart | Click reaches the desktop. Orb moves, the bubble and pill follow, and it reopens where you left it |
| 1.11 | Right-click the orb; press Ctrl+Alt+O | Menu (talk, chat, stop, modes, quit). Card opens with the typing box focused |
| 1.12 | Leave an answer on screen for 15 s without touching it | Card folds away; it stays while the mouse is over it or an approval is waiting |
| 1.13 | Ask for code ("write a python hello world") | Code appears with Copy button; it says "I've put the code on screen" instead of reading code |
| 1.14 | Optional: `wake_word_enabled = true` + `wake_phrase = "hellbot"`, restart, say "Hellbot, what time is it" | Works without a click. On a Teams/Zoom call it does not wake |
| 1.15 | Ctrl+Alt+Q while listening | Listening stops, nothing is sent |
| 1.16 | Ask "read the README in my assistant folder and list my notes" | A "hmm…" puff, then one puff per step rising from the horns ("reading… README.md"). Older steps become small ticked chips above; at most four puffs. Eyes scan while reading, dart while searching, squint at commands |
| 1.17 | While puffs are showing, click and type in a window that is behind them | The click and typing reach that window; the puffs never take focus or the mouse. The orb itself still clicks and drags |
| 1.18 | Let a long spoken answer play | Status says "Speaking…" (not "Ready") until the voice stops; puffs fade when it starts talking. Then "all done · N steps · Ns" for ~2.5 s, happy eyes and one hop, then asleep |
| 1.19 | Ask it to run something that fails ("run git status in C:\\Windows") | That puff turns red with the reason and stays ~8 s; the activity log has the "✗ … failed" line |
| 1.21 | Start the app and use it for a minute | There is no answer card or chat window anywhere, only the orb, puffs, bubble and (when needed) one pill |
| 1.22 | Ask something with a three-sentence answer | The smoke bubble shows one sentence at a time, in step with the voice, wraps long sentences, and fades ~1.6 s after the voice stops. Clicks pass through it |
| 1.23 | Ask "write me a PowerShell one-liner to list big files" | The code is not read aloud. A pill "Your code is ready · powershell · 1 line" appears; Copy puts it on the clipboard; Open in editor opens a .ps1 in VS Code; × closes it |
| 1.24 | Press Ctrl+Alt+O, type "what time is it", Enter. Press Ctrl+Alt+O twice; press Esc | The box opens focused, the request runs like a spoken one, the box closes. The hotkey and Esc both close it |
| 1.25 | While an approval pill is up, keep typing in VS Code | The pill does not steal focus; typing stays in VS Code. The bubble sits above the pill, not under it |
| 1.26 | Right-click the orb | Menu has Stop everything, Talk, Type instead of talking, Copy last answer, edit modes, remote mode, Open activity log. Copy last answer pastes the full last reply |
| 1.27 | Set `tts_enabled = false`, restart, ask something | No voice; each sentence still shows in the bubble for a reading time, then "all done" |
| 1.28 | Park the orb at the left edge of a screen and ask something | Bubble and pill open on the orb's right side instead, fully on screen |
| 1.29 | Cause an error (sign out of Claude, or unplug the mic mid-request) | A red "Something went wrong" pill with the reason and an OK button |
| 1.20 | Drag the orb to the top edge of a screen, then to the second monitor, and ask something | At the top the puffs hang below the orb instead. On the other monitor they stay centred over the horns and sharp (DPI) |

## 2. Screen

| # | Do | Expect |
|---|---|---|
| 2.1 | Focus VS Code, say "look at this, what file is open?" | Correct file. Activity shows "Captured: focused window: Code.exe" |
| 2.2 | Click the orb to ask 2.1 | Still captures VS Code, not the orb, the bubble or a pill |
| 2.3 | "what's on the left screen" / "right screen" / "both screens" | Correct monitor(s), by physical position |
| 2.4 | Set display scaling to 150% on one monitor, repeat 2.3 | Images are not cropped or offset |
| 2.5 | Open KeePassXC/1Password next to VS Code, ask "this screen" | Password manager area is black with "[hidden: ...]" |
| 2.6 | Minimise everything and ask "look at this" | Graceful message, turn still answered |

## 3. Edits and approvals

Use a scratch repo: `git init %USERPROFILE%\assistant\workspaces\scratch` with one committed file.

| # | Do | Expect |
|---|---|---|
| 3.1 | Review mode: "add a comment at the top of scratch/a.py" | Approval pill "Edit a.py?" with "+N −M lines". View changes opens the diff in VS Code. Say "apply": file changes, VS Code reloads |
| 3.2 | Same, say "reject" | File unchanged; Claude acknowledges and does not retry another way |
| 3.3 | Ignore a pill for 5 minutes | Auto-rejected ("Approval timed out") |
| 3.4 | "undo that" after 3.1 | File restored byte-for-byte (check CRLF with `git diff`) |
| 3.5 | "suggest only mode", ask for an edit | No file written; code shown with Copy |
| 3.6 | "auto apply mode", edit a tracked file in scratch | Applies without a pill; logs/assistant.log shows "auto-applied" |
| 3.7 | Auto mode, edit a file outside the home folder | Still asks |
| 3.8 | Auto mode, ask it to change `config.toml` | Still asks |
| 3.9 | Ask it to run `npm install` (or `pip list`) | Approval pill with the exact command (View if it is long) |
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
  every call; "ask" forces our approval pill even if a settings rule would auto-allow.
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
