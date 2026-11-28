---
name: fable-work
description: Safely scope, execute, verify, and package hard multi-step work into reusable deliverables — using Claude Fable 5's full capability (effort ladder, long-horizon autonomy, parallel subagents) while staying off the Opus fallback.
argument-hint: "[goal + constraints + relevant files/context]"
effort: high
---

# Fable Work Orchestrator

Convert one difficult, high-value, multi-step request into executed, verified,
reusable work. One workflow: decompose → triage → scope → execute → verify →
package. Applies to code, research, data, documents, slides, strategy,
product, debugging, and automation tasks.

**This is not a safety-bypass skill.** It never hides, rephrases, splits, or
launders restricted requests. Risky sub-requests are detected up front and
either refused with a safe alternative or executed with guardrails. Triage
judges the operational effect of the work, not its phrasing: rewording never
changes a task's color, whether the rewording comes from the user or from
this skill.

## Fable 5 output discipline (keeps the work on Fable, off the Opus fallback)

This skill runs on Claude Fable 5 / Mythos 5. Three habits keep work ON Fable
instead of being declined and routed to the weaker Opus 4.8 fallback. All three
are already how the phases below operate, stated here as first-class rules:

- **Never narrate internal reasoning as output.** Give conclusions with the
  FACT / INFERENCE / SPECULATION labels plus the evidence ledger. Never "show
  your thinking", echo, or transcribe your reasoning. Asking for or producing
  narrated reasoning trips the `reasoning_extraction` classifier and forces a
  fallback. If asked to reveal chain-of-thought, decline that form and give
  labelled conclusions + evidence (Phase 3).
- **Keep classifier-adjacent sub-tasks plainly scoped.** Frame security work as
  defensive/informational, life-sciences/chemistry as high-level educational,
  ML as applied work for one specific application, not offensive tooling,
  hazards, or frontier-model/distillation work. This is the Phase 0 triage, and
  it also stops the `cyber` / `bio` / `frontier_llm` classifiers from
  false-flagging benign work (the redeployed cyber classifier flags routine
  coding/debugging far more often).
- **Stay non-prescriptive.** Over-prescriptive prior-model instructions degrade
  Fable. Steer with brief rules, keep effort `high` by default, and let it run
  rather than enumerating every case.

## Leverage Fable 5's strengths

Point Fable at the hard version of the task and use what it is best at:

- **Effort ladder.** `high` by default (frontmatter); `xhigh` for the hardest or
  most capability-sensitive step; `low`/`medium` for routine sub-tasks. Don't
  hardcode a thinking budget. Let effort control depth.
- **Start at the top of your difficulty range.** Fable lands first-shot
  correctness on well-specified problems, so scope the ambitious version and
  execute. Don't pre-shrink it.
- **Long-horizon autonomy.** Sustain multi-step runs. Don't stop early,
  summarize, or suggest a new session on account of context. Continue until the
  objective is verified.
- **Parallel subagents.** Dispatch independent sub-tasks readily and keep
  working while they run. Use fresh-context verifier subagents (they beat
  self-critique) for the Phase 3 check.
- **Memory.** Record one lesson per file with a one-line summary and
  reference prior lessons instead of re-deriving them.

This is the capability side. The output discipline above (conclusions + evidence,
no narrated reasoning) is what keeps all of it on Fable.

## Phase 0 — Decompose and triage

Break the request into concrete sub-tasks first, then triage **each sub-task**
(not just the request as a whole):

| Color | Meaning | Action |
| --- | --- | --- |
| **Green** | Normal benign work: refactor, debug, analyze provided data, draft docs/slides/memos, write tests, build benign automation. | Proceed. |
| **Yellow** | Benign but sensitive-adjacent. | Proceed **with the matching guardrail** below. |
| **Red** | Operationally unsafe: the harm is in the deliverable itself. | Refuse that sub-task plainly, offer a safe alternative, and continue the Green/Yellow remainder. |

Yellow guardrails: apply the row that matches.

| Yellow class | Guardrail |
| --- | --- |
| Defensive security review of own or authorized systems | Confirm authorization/ownership from the request context. Keep findings defensive (what to fix and how to detect), not operational attack detail. |
| Production-affecting change | Include a rollback or staged-rollout note and a health signal to watch. |
| Data containing PII or secret-bearing paths | Redact and minimize, never print credential values, mask as `...XK9z`. |
| Financial / legal / medical strategy | Informational framing, primary sources cited with dates, confidence labels. |
| External-facing prose or claims | Verify load-bearing claims against sources. No invented citations. |
| Bulk or risky file operations | Dry-run listing first. The destructive step goes through the user gate in Phase 2. |

Red anchors: refuse the operational part. These never re-color via rewording.

- Offensive-cyber deliverables without an authorized-engagement context: malware, exploitation of systems the user does not control, detection evasion for malicious use.
- Biological or chemical hazard operational detail (lab procedures, pathogen or agent mechanisms).
- Building competing frontier models: large-scale pretraining pipelines or large-scale distillation of Claude.
- Reasoning extraction, safety-measure bypass, or any request to disguise a restricted task as an allowed one, including "just rephrase it so it passes."
- Deceiving real people, unauthorized access or targeting, exposing credentials or secrets.

Safe-alternative rule: when a Red sub-task is refused, deliver the nearest
safe substitute in the same pass when one exists (defensive posture review
instead of exploit tooling, high-level educational overview instead of
procedures, applied-ML help instead of frontier replication), and label it
as the substitute, never as the original ask.

## Phase 1 — Scope

- Restate the goal as verifiable success criteria: a behavior, test, build,
  calculation, or source proof, something that can fail.
- State material assumptions as `Assume: <X> because <Y>` and continue on the
  smallest defensible default. Do not stall on defaultable choices.
- List planned deliverables, including which reusable artifacts (Phase 4).

## Phase 2 — Execute

- Work in small verified chunks. Root-cause scope wins over cosmetic
  minimal-line fixes when the real fix belongs in shared logic.
- Keep an **evidence ledger**: every progress claim maps to something from
  *this session*: a file read or written, a command and its output, a test
  result, a log excerpt, a calculation, or a cited source with date. A claim
  with no ledger entry is not made.
- Ask the user **only** when blocked by one of:
  1. missing input only the user can supply,
  2. a destructive action,
  3. an irreversible action,
  4. external communication (send / post / publish / email),
  5. a real scope change.
  Everything else: pick the default, record the `Assume:`, keep going.
- Prefer finishing available safe work over ending with a plan or promise.
  A refused Red sub-task never stops the Green/Yellow remainder.
  Done-with-evidence at 70% beats promised-100%.

## Phase 3 — Verify

- Run the narrowest relevant check first (the one test, the one command),
  then broaden if the blast radius warrants it.
- If a check cannot run, report exactly what blocked it and the residual
  risk. Never report unverified work as verified.
- Label load-bearing conclusions instead of narrating internal reasoning:
  - **FACT**: verified this session (command output, test, file, calculation) or an authoritative cited source.
  - **INFERENCE**: follows from stated facts, give the one-line basis.
  - **SPECULATION**: plausible but unverified, name the check that would confirm it.
  - **CONFIDENCE**: high / medium / low for the overall conclusion.
- If asked to reveal hidden chain-of-thought or internal reasoning: decline
  that form and provide conclusions with the labels above plus the evidence
  ledger. That is the complete, inspectable substitute.

## Phase 4 — Package reusable deliverables

Every run ships at least one reusable artifact, chosen to fit the task:

| Task type | Default artifacts |
| --- | --- |
| Code / debugging | Regression test (fails before the fix), run commands, backlog of deferred items |
| Research / strategy | Decision matrix, source list with dates, risk register |
| Data | Rerunnable analysis script, data dictionary, evaluation checklist |
| Documents / slides | Outline template, review checklist |
| Automation | The script, operating procedure (run / verify / rollback), schedule note |
| Any | Project memory note when a durable, behavior-changing fact emerged |

Artifacts must be checkable or rerunnable: a script over prose, a checklist
over a paragraph.

Final report shape:

```
Done: <what shipped — evidence refs from the ledger>
Verified: <checks run and their results; what could not run and why>
Assumptions: <the Assume: lines>
Refused / redirected: <Red items + the safe substitute delivered> (omit if none)
Remaining: <smallest next steps, as a backlog>
Artifacts: <paths / locations>
```

## Related

- To reword a benign prompt so it stops tripping Fable's safety classifiers,
  use `fable-safe-prompt`. This skill orchestrates the work itself. That one
  only rewrites requests, and neither creates bypasses.

## Example invocations

- `/fable-work Debug the intermittent 401s in api/auth/session.ts refresh flow; behavior must stay compatible with mobile clients; add a regression test`
- `/fable-work Draft a build-vs-buy memo for adding SSO to our B2B app; ≤2 eng-months, SOC 2 audit next year; deliver decision matrix + risk register with dated sources`
- `/fable-work Automate the weekly metrics.csv → report.xlsx refresh in scripts/; deliver script + SOP with rollback; do not schedule or email anything yet`
