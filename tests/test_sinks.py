"""Every UI sink must have every method the core calls, so a new UiSink method cannot crash a turn."""

import inspect
import re
from pathlib import Path

import pytest

import assistant
from assistant.cli import _PrintSink
from assistant.ui.sink import NullSink, UiSink

SRC = Path(assistant.__file__).parent
PROTOCOL = {n for n, v in vars(UiSink).items() if inspect.isfunction(v)}


def _called_on_ui() -> set[str]:
    files = [SRC / "orchestrator.py", SRC / "brain.py", SRC / "desktop" / "tools.py"]
    return {m for f in files for m in re.findall(r"\bui\.(\w+)\(", f.read_text(encoding="utf-8"))}


def _sinks() -> list[type]:
    sinks: list[type] = [NullSink, _PrintSink]
    try:
        from assistant.ui.tray import QtSink
    except ImportError:
        return sinks
    return sinks + [QtSink]


def test_protocol_declares_every_call_the_core_makes():
    assert _called_on_ui() <= PROTOCOL


@pytest.mark.parametrize("sink", _sinks(), ids=lambda c: c.__name__)
def test_sink_implements_the_whole_protocol(sink):
    assert PROTOCOL, "UiSink lost its methods"
    assert not {n for n in PROTOCOL if not callable(getattr(sink, n, None))}


def test_print_sink_runs_every_method(capsys):
    from assistant.steps import describe
    from assistant.ui.sink import ApprovalRequest

    sink = _PrintSink()
    sink.status("thinking", "Working…")
    sink.transcript("hi")
    sink.assistant_text("hello")
    sink.activity("→ Read app.py")
    sink.step_started(describe("t1", "Read", {"file_path": "app.py"}))
    sink.step_finished("t1", True, "")
    sink.turn_finished(True)
    sink.show_code("Code", "x = 1", "python")
    sink.request_approval(ApprovalRequest("a1", "Run command", 2, "why", "dir"))
    sink.approval_closed("a1", "approved")
    sink.modes("review", False)
    assert "→ Read app.py" in capsys.readouterr().out
