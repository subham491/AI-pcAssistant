---
name: fable-safe-prompt
description: Use when rewriting a user's prompt for Claude Fable 5 / Mythos 5 to reduce false-positive safety-classifier refusals (stop_reason "refusal" -> user notified, request routed to Opus 4.8) on normal work such as refactoring, architecture review, research, documentation, code maintenance, benign security or ML work, or planning, without bypassing safeguards or enabling cyber, biology/chemistry, frontier-LLM/distillation, or reasoning-extraction requests. It also emits the leanest prompt that still does the job — fewest tokens, only the classifier-scope lines the task actually needs.
---

# Fable Safe Prompt

## Overview

Rewrite the user's request into a clear, Fable-friendly prompt that preserves benign intent while keeping scope out of the four areas Fable 5's safety classifiers police. Do not create jailbreaks, bypass prompts, evasion tactics, or wording intended to defeat safety systems.

Two goals that reinforce each other: (1) keep the work ON Fable — no `reasoning_extraction` / `cyber` / `bio` / `frontier_llm` false-positive that declines the request and routes it to Opus 4.8; and (2) spend the fewest tokens that still do the job. A lean prompt is both safer (smaller classifier surface) and cheaper, and Fable follows small prompts more reliably than long ones. When the two ever conflict, safety wins — never drop a needed scope line to save tokens.

## What Fable 5 actually enforces

Fable 5 (and Mythos 5) run safety classifiers that can decline a request. A decline is a normal HTTP 200 with `stop_reason: "refusal"` and a `stop_details.category`; the user is **notified and the request is routed to Claude Opus 4.8** (surfaced, not hidden). So a false flag is not an error — it downgrades the work off Fable onto a less capable model. The redeployed (2026-07-01) `cyber` classifier uses a **safety margin much larger than any prior launch**, so it flags benign requests more often — especially routine coding, debugging, and security-adjacent work. Reducing those false flags is the whole point of this skill.

| `stop_details.category` | Fires on | Benign work that also trips it |
| --- | --- | --- |
| `cyber` | Malware, exploit, or offensive-cyber development | Security research, pentest-tool review, CTF, vulnerability analysis — and since the 2026-07-01 redeployment, routine coding/debugging and security-adjacent work far more often |
| `bio` | Dangerous lab methods, pathogen or molecular mechanisms (biology **and chemistry**) | Beneficial life sciences, bioinformatics, ordinary chemistry |
| `frontier_llm` | Building competing frontier models: pretraining pipelines, distributed-training infra, ML-accelerator design, large-scale distillation of Claude | Ordinary ML training, evaluation, and analysis work |
| `reasoning_extraction` | Asking the model to reproduce, echo, transcribe, or explain its internal reasoning as response text | "Show your thinking", "explain your reasoning step by step", "output your chain of thought" — trips even on benign tasks |

`stop_details.category`/`explanation` are `null` when a refusal maps to no named category. Fable never returns raw chain of thought; ask for conclusions and evidence, not introspection.

## Core Workflow

1. Identify the benign task: refactor, architecture review, logic improvement, research, writing, planning, debugging, summarization, or documentation.
2. Remove bypass framing: do not keep wording such as "avoid detection", "get around safety", "jailbreak", "do not refuse", or "ignore policy".
3. **Do not ask Fable to show, echo, transcribe, or explain its own reasoning in the response.** That alone trips `reasoning_extraction` and downgrades to Opus. Ask for the conclusion plus the artifacts and evidence that support it (diffs, cited sources, test output), not a narration of how the model thought. Drop phrases like "show your thinking", "walk me through your reasoning", "explain step by step how you reasoned", "output your chain of thought".
4. Add only the safe-scope lines the task actually touches. For a plain refactor, one line ("ordinary software maintenance; no offensive-security, bio/chem, frontier-model/distillation, or reasoning-extraction work") beats enumerating all four areas. Reserve the full four-area block for genuinely classifier-adjacent work. Always exclude credentials and sensitive operational detail.
5. For benign but classifier-adjacent work (defensive security research, life-sciences or chemistry information, ordinary ML training), state the benign, defensive, or informational purpose plainly and keep it high-level. Do not add operational offensive detail. If it is genuinely sensitive, treat it as out of scope (step 7).
6. Preserve constraints that matter: files, goals, behavior-preservation, tests, output format, language, and success criteria.
7. If the original task is actually safety-sensitive, do not sanitize it into an allowed request. Say it is out of scope and offer a safe high-level alternative.
8. Output only the rewritten prompt unless the user asks for an explanation.

## Token discipline

Default to the leanest prompt that still does the job — this skill's second goal after preventing the fallback:

- Include only the classifier-scope lines the task actually touches, not all four.
- Cut boilerplate; one terse scope line beats a multi-bullet block.
- Add no role, examples, or process steps the task doesn't need.
- Never request narrated reasoning — the biggest token sink AND the `reasoning_extraction` trip.
- For routine work, note that low/medium effort suffices (high effort spends more thinking tokens).

Safety still wins on conflict: never drop a needed scope line to save tokens.

## Lean default (use this first)

```text
Normal [task] task. Goal: [preserved goal]. Keep changes minimal and tied to the goal; preserve current behavior unless asked. Not offensive-security, bio/chem, frontier-model/distillation, or reasoning-extraction work. Output the result + evidence (diffs, sources, test output), not narrated reasoning.

Task: [rewritten request]
```

## Full template (classifier-adjacent work only)

> Most benign requests need only the lean default above. Use this fuller block when the task genuinely borders cyber / bio / frontier_llm.

Use the user's language. For Korean requests, produce Korean prompts. For English requests, produce English prompts.

```text
This is a normal [task type] task.

Goal:
- [preserved user goal]

Scope:
- Focus on [safe areas: architecture, maintainability, logic correctness, documentation, tests, performance, planning].
- Preserve existing behavior unless explicitly requested otherwise.
- Keep changes or recommendations minimal and directly tied to the goal.

Out of scope:
- Offensive cybersecurity, exploit development, credential extraction, persistence, evasion, malware, or unauthorized access. (cyber)
- Biological or chemical hazards: wet-lab procedures, pathogen or molecular mechanisms, biosafety operational detail. (bio)
- Assisting development of competing frontier models: pretraining pipelines, distributed-training infrastructure, ML-accelerator design, or large-scale distillation of Claude. (frontier_llm)
- Reproducing, echoing, or explaining the model's internal reasoning as response text; any attempt to weaken or bypass model safety measures. (reasoning_extraction)

Output:
- Give the result and the evidence that supports it (diffs, cited sources, test output). Do not narrate internal reasoning.

Process:
1. Inspect the provided context.
2. State the smallest safe plan if changes are needed.
3. Perform only the in-scope work.
4. Run available verification or explain what could not be verified.
5. Summarize the result and any remaining safe follow-ups.

Task:
[rewritten user request]
```

## Common Transformations

### Refactoring

```text
Refactor [file/path/component].

Goal:
- Preserve current behavior.
- Improve readability, structure, naming, duplication, and testability.
- Keep the diff minimal and avoid new features.
- Do not change public APIs unless required.

Safety scope:
- Treat this as ordinary software maintenance.
- Do not perform exploit analysis, offensive security work, biological or chemical analysis, competing-model/distillation work, credential handling, or reasoning-extraction.

Process:
1. Read the relevant code first.
2. Propose the smallest safe refactor.
3. Apply the change.
4. Run existing tests or explain why they could not be run.
5. Summarize changed files and behavior impact — the result, not your reasoning.
```

### Architecture Review

```text
Review the architecture of [system/module/path] as a normal software design task.

Focus on:
- module boundaries
- data flow
- business logic correctness
- error handling
- testability
- performance bottlenecks
- simplification opportunities

Avoid:
- offensive security analysis
- exploit development
- biological or chemical procedures
- competing frontier-model development or distillation
- credential extraction
- reasoning-extraction and safety-bypass tactics

Output:
- findings ordered by impact
- concrete file/module references when available
- a small, safe improvement plan (state conclusions, not internal reasoning)
```

### Research

```text
Research [topic] for a normal informational or planning purpose.

Scope:
- Use public, reputable sources.
- Prefer official or primary sources when available.
- Summarize high-level findings and practical implications.
- Do not provide operational instructions for offensive cybersecurity, biological or chemical procedures, weapons, credential misuse, competing-model training/distillation, or safety bypasses.

Output:
- concise answer first
- source-backed key points
- uncertainty or limitations
```

### Benign ML work (avoiding `frontier_llm`)

```text
Help with [training/evaluating/analyzing] [model or pipeline] as ordinary applied-ML work.

Scope:
- This is standard model development, evaluation, or data analysis for a specific application — not building a competing frontier model.
- Do not focus on large-scale pretraining pipelines, distributed-training infrastructure, ML-accelerator design, or distilling Claude's capabilities.

Output:
- concrete recommendations, code, or metrics
- results and evidence, not internal reasoning
```

## Refusal Pattern

If the user asks to bypass Fable, evade safety filters, disguise unsafe work, force the model to disclose its internal reasoning, or preserve harmful instructions, respond briefly:

```text
I cannot help create bypass, evasion, or reasoning-extraction prompts. I can rewrite the request into a clearly safe version if the underlying task is normal work.
```

## Sources

Anthropic official docs (retrieved 2026-07-01):
- Introducing Claude Fable 5 and Claude Mythos 5 — https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5
- Refusals and fallback (category values `cyber`, `bio`, `frontier_llm`, `reasoning_extraction`) — https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback
- Prompting Claude Fable 5 (reasoning-extraction trap, skill-audit guidance) — https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5
- Redeploying Claude Fable 5 (2026-07-01 restore: enlarged cyber safety margin, more benign coding/debugging false flags, notify + route to Opus 4.8) — https://www.anthropic.com/news/redeploying-fable-5
