"""Tiny build-progress tracker: a JSON state file plus a self-refreshing HTML view.

Usage from a build script:

    from tools.progress import mark, STEP_IDS
    mark("phase1-lean-profile", "active")
    ... do the work ...
    mark("phase1-lean-profile", "done")

Run directly to (re)initialize the tracker and open the viewer:

    python tools/progress.py --init
"""
from __future__ import annotations

import argparse
import json
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / "progress.json"
HTML_PATH = HERE / "progress.html"


@dataclass(frozen=True)
class Step:
    id: str
    phase: str
    label: str


STEPS: list[Step] = [
    Step("p3-ladder-design", "Phase 3: Ladder + Treasurer", "Design escalation ladder (Steward > Haiku > Sonnet > Opus > Fable) and doubt signals"),
    Step("p3-ladder-build", "Phase 3: Ladder + Treasurer", "Build ladder: escalate one rung on low confidence, tool failure, or risky topic"),
    Step("p3-config", "Phase 3: Ladder + Treasurer", "Config table: job type to model, per-rung settings"),
    Step("p3-treasurer", "Phase 3: Ladder + Treasurer", "Treasurer: daily and weekly cost tally from the audit log"),
    Step("p3-downgrade", "Phase 3: Ladder + Treasurer", "Downgrade or warn as the configured cap nears"),
    Step("p3-wire", "Phase 3: Ladder + Treasurer", "Wire ladder + Treasurer into the orchestrator and audit log"),
    Step("p3-tests", "Phase 3: Ladder + Treasurer", "Tests for ladder escalation and Treasurer limits"),
    Step("p4-inspector-design", "Phase 4: Inspector + Scribe", "Design cross-family review for code-changing and risky turns"),
    Step("p4-inspector-build", "Phase 4: Inspector + Scribe", "Build Inspector: Gemini reviews Claude's work, advice only"),
    Step("p4-scribe-build", "Phase 4: Inspector + Scribe", "Build Scribe: Gemini-written session summaries to keep context short"),
    Step("p4-wire", "Phase 4: Inspector + Scribe", "Wire Inspector and Scribe into orchestrator and session rolls"),
    Step("p4-tests", "Phase 4: Inspector + Scribe", "Tests for Inspector and Scribe"),
    Step("verify", "Verify", "Full test suite green"),
]
STEP_IDS = [s.id for s in STEPS]
_BY_ID = {s.id: s for s in STEPS}

STATUS_COLOR = {"pending": "#555", "active": "#ffae42", "done": "#7bd88f", "failed": "#ff7b7b"}
STATUS_ICON = {"pending": "&#9675;", "active": "&#9679;", "done": "&#10003;", "failed": "&#10007;"}


def _load() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"status": {s.id: "pending" for s in STEPS}, "updated": "", "note": ""}


def _save(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _render(state)


def mark(step_id: str, status: str, note: str = "") -> None:
    if step_id not in _BY_ID:
        raise ValueError(f"unknown step id: {step_id}")
    if status not in STATUS_COLOR:
        raise ValueError(f"unknown status: {status}")
    state = _load()
    state["status"][step_id] = status
    state["updated"] = datetime.now().strftime("%H:%M:%S")
    if note:
        state["note"] = note
    _save(state)


def _render(state: dict) -> None:
    statuses = state.get("status", {})
    done = sum(1 for v in statuses.values() if v == "done")
    total = len(STEPS)
    pct = round(100 * done / total) if total else 0

    phases: dict[str, list[Step]] = {}
    for s in STEPS:
        phases.setdefault(s.phase, []).append(s)

    rows = []
    for phase, steps in phases.items():
        rows.append(f'<h2>{phase}</h2>')
        for s in steps:
            st = statuses.get(s.id, "pending")
            color = STATUS_COLOR[st]
            icon = STATUS_ICON[st]
            spin = ' class="spin"' if st == "active" else ""
            rows.append(
                f'<div class="row"><span{spin} style="color:{color}">{icon}</span>'
                f'<span class="label" style="color:{color}">{s.label}</span>'
                f'<span class="st">{st}</span></div>'
            )
    body = "\n".join(rows)
    note = state.get("note", "")
    updated = state.get("updated", "")

    html = f"""<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="2">
<title>Hellbot kingdom build</title>
<style>
body{{background:#14161a;color:#e8e8e8;font:15px Segoe UI,sans-serif;margin:30px auto;max-width:720px;padding:0 16px}}
h1{{font-size:22px;margin-bottom:4px}}h2{{font-size:15px;color:#9ecbff;margin:22px 0 6px}}
.bar{{height:18px;border-radius:9px;background:#1d2026;overflow:hidden;margin:10px 0 18px;border:1px solid #333}}
.fill{{height:100%;background:linear-gradient(90deg,#4aa3ff,#7bd88f);width:{pct}%;transition:width .4s}}
.row{{display:flex;gap:10px;align-items:center;padding:4px 0;font-size:14px}}
.label{{flex:1}}
.st{{color:#888;font-size:12px;text-transform:uppercase}}
.spin{{display:inline-block;animation:spin 1s linear infinite}}
@keyframes spin{{from{{transform:rotate(0)}}to{{transform:rotate(360deg)}}}}
.meta{{color:#888;font-size:12px;margin-top:22px}}
</style>
<h1>Building the Hellbot kingdom &mdash; Phase 3 &amp; 4</h1>
<div class="meta">{done}/{total} steps done &middot; {pct}% &middot; last update {updated}</div>
<div class="bar"><div class="fill"></div></div>
{body}
<div class="meta">{note}</div>
"""
    HTML_PATH.write_text(html, encoding="utf-8")


def init() -> None:
    state = {"status": {s.id: "pending" for s in STEPS}, "updated": datetime.now().strftime("%H:%M:%S"), "note": "Starting."}
    _save(state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    if args.init:
        init()
    if args.open or args.init:
        webbrowser.open(HTML_PATH.as_uri())
