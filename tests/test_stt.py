import numpy as np

from assistant.config import VoiceConfig
from assistant.voice.stt import DEFAULT_MODEL, SpeechToText, build_prompt, resolve_model


def test_auto_model_is_the_accent_robust_one_and_explicit_names_pass_through():
    assert resolve_model("auto") == DEFAULT_MODEL == "large-v3-turbo"
    assert resolve_model("small.en") == "small.en"
    assert resolve_model("someone/whisper-indian-english-ct2") == "someone/whisper-indian-english-ct2"


def test_prompt_puts_the_assistant_name_first_and_drops_duplicates():
    assert build_prompt("Hellbot", ["Jira", "Hellbot", " "]) == "Glossary: Hellbot, Jira."
    assert build_prompt("", []) is None


class _FakeModel:
    def __init__(self, delay):
        self.delay, self.kwargs = delay, None

    def transcribe(self, audio, **kwargs):
        import time

        self.kwargs = kwargs
        time.sleep(self.delay)
        return iter([type("Seg", (), {"text": " fix the test "})()]), None


def test_cpu_uses_greedy_decoding_and_warns_once_when_slow(monkeypatch):
    import assistant.voice.stt as stt_mod

    monkeypatch.setattr(stt_mod, "SLOW_FACTOR", 0.0)
    monkeypatch.setattr(stt_mod.time, "monotonic", iter([0.0, 9.0, 20.0, 29.0]).__next__)
    notes = []
    stt = SpeechToText(VoiceConfig(vocabulary=["Jira"]), name="Hellbot", on_slow=notes.append)
    stt._model, stt.device = _FakeModel(0), "cpu"
    audio = np.zeros(16_000, dtype=np.float32)
    assert stt.transcribe(audio) == "fix the test"
    assert stt._model.kwargs["beam_size"] == 1 and stt._model.kwargs["initial_prompt"] == "Glossary: Hellbot, Jira."
    stt.transcribe(audio)
    assert len(notes) == 1 and "small.en" in notes[0]
