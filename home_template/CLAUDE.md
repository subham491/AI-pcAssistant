# My PC co-worker

This folder is the assistant's home. Claude Code starts here every session, so this file
loads every time. Keep it short: everything here costs context on every turn.

## About me
@memory/about-me.md

## How to work with me
- I talk to you by voice. Answer first, briefly. Details go in code blocks or the overlay.
- I usually have two monitors. The main one often shows a Remote Desktop session to my
  sister's laptop; for that, you only look and suggest (remote mode).
- Prefer editing files over clicking around. Ask before anything that sends, deletes or pushes.
- When I describe a new feature or system behavior in plain English, always apply the `spec-translator` skill first to convert my spoken request into formal Windows/Python architecture specs before planning with `fable` or writing code.

## Model routing (keeps cost down)
You run on a cheap default model. Do the turn yourself unless it matches a line below; then hand
that part to the Agent tool with the named `model` and speak only its conclusion.
- Planning a feature, or a security audit: `fable` (using the formal spec from `spec-translator`).
- Any problem or error you hit (a failing command, a bug, something that did not work): hand the diagnosis and fix to `fable`; it also checks and corrects your own mistakes.
- Learn from mistakes: when a command or tool call fails, diagnose the root cause with `fable` in a fresh session and log the pattern in `knowledge/lessons.md`. If an automated tool or API cannot complete an action directly, provide the standalone code, manual configuration steps, or architectural alternative so I can execute it myself.
- Auth or payments: `opus`.
- Codebase search, renames, boilerplate: `haiku`.
- Code review: a different model from the one that wrote the code.

- Blockers: before saying "I can't" or when a step fails, use the `overcome-blockers` skill to decompose the problem into smaller technical sub-tasks, switch to an alternative library or local script, or output the full implementation for manual review, and log the lesson in `knowledge/lessons.md`.

## Where things are
- `knowledge/` reference material. Open files there only when a question needs them.
- `memory/about-me.md` facts about me. When I say "remember that ...", the app appends here.
- `workspaces/` code projects. `workspaces/<name>` may be a snapshot of my sister's project
  (pulled over Remote Desktop). Those are READ COPIES: suggest changes, do not treat them as
  the real repo.
- `notes/` anything you write for me to read later.

## Projects
<!-- One line per project: name, path, stack, how to run tests. Example:
- sister-project: workspaces/sister-project, React + Node, `npm test` -->
