import io
import json

import pytest

from assistant import gemini


def test_ask_sends_the_key_as_a_header_and_returns_the_text(tmp_path, monkeypatch):
    key = tmp_path / "key.txt"
    key.write_text("SECRET\n")
    seen = {}

    def fake_urlopen(req, timeout):
        seen["key"], seen["url"], seen["body"] = req.get_header("X-goog-api-key"), req.full_url, json.loads(req.data)
        return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}).encode())

    monkeypatch.setattr(gemini.urllib.request, "urlopen", fake_urlopen)
    assert gemini.ask("question", key, "some-model") == "hi"
    assert seen["key"] == "SECRET" and "some-model" in seen["url"] and "SECRET" not in seen["url"]
    assert seen["body"]["contents"][0]["parts"][0]["text"] == "question"


def test_failures_become_one_error_type_that_does_not_leak_the_key(tmp_path, monkeypatch):
    key = tmp_path / "key.txt"
    key.write_text("SECRET")
    monkeypatch.setattr(gemini.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(gemini.GeminiError) as err:
        gemini.ask("q", key)
    assert "offline" in str(err.value) and "SECRET" not in str(err.value)
