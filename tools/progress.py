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
    Step("p1-profile-design", "Phase 1: lean context", "Design lean vs. full settings profile"),
    Step("p1-profile-build", "Phase 1: lean context", "Build lean .claude profile (only voice skills + desktop tools)"),
    Step("p1-profile-switch", "Phase 1: lean context", "Wire default-lean / on-demand-full switch into config + brain"),
    Step("p1-cache-check", "Phase 1: lean context", "Confirm system prompt header stays stable for prompt caching"),
    Step("p1-tests", "Phase 1: lean context", "Tests for the lean/full profile switch"),
    Step("p2-herald", "Phase 2: Herald + Steward", "Herald: free local rule router for turn size/destination"),
    Step("p2-steward", "Phase 2: Herald + Steward", "Steward: Gemini direct-answer path for simple questions"),
    Step("p2-fallback", "Phase 2: Herald + Steward", "Fallback to Claude on doubt, tool failure, or refusal-like text"),
    Step("p2-audit", "Phase 2: Herald + Steward", "Audit log: which rung handled each turn"),
    Step("p2-wire", "Phase 2: Herald + Steward", "Wire Herald into orchestrator.handle_text"),
    Step("p2-tests", "Phase 2: Herald + Steward", "Tests for Herald routing + Steward fallback"),
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
<h1>Building the Hellbot kingdom &mdash; Phase 1 &amp; 2</h1>
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
