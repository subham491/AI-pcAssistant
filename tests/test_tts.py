import threading

import numpy as np

from assistant.voice.tts import PocketVoice


class FakeStream:
    def __init__(self):
        self.written, self.aborted, self.closed = [], 0, 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed += 1

    def write(self, data):
        self.written.append(data)

    def abort(self):
        self.aborted += 1


class FakeModel:
    def __init__(self, stop_after=None):
        self.stop_after = stop_after

    def generate_audio_stream(self, state, text, stop):
        for i in range(4):
            if i == self.stop_after:
                stop.set()  # barge-in arrives mid-sentence
            yield np.zeros(480, dtype="float32")


def test_pocket_voice_plays_every_chunk_as_mono_frames():
    stream = FakeStream()
    PocketVoice(FakeModel(), object(), lambda: stream).speak("hello", threading.Event())
    assert [c.shape for c in stream.written] == [(480, 1)] * 4
    assert (stream.aborted, stream.closed) == (0, 1)


def test_pocket_voice_stop_cuts_off_immediately():
    stream = FakeStream()
    PocketVoice(FakeModel(stop_after=2), object(), lambda: stream).speak("hello", threading.Event())
    assert len(stream.written) == 2  # nothing after the stop
    assert (stream.aborted, stream.closed) == (1, 1)  # buffered audio dropped


def test_pocket_voice_opens_a_fresh_stream_for_each_sentence():
    # A stream opened once at startup stays on that speaker even after you switch the
    # Windows default (headset -> monitor), so Hellbot went silent while YouTube played.
    streams = []
    voice = PocketVoice(FakeModel(), object(), lambda: streams.append(FakeStream()) or streams[-1])
    voice.speak("one", threading.Event())
    voice.speak("two", threading.Event())
    assert len(streams) == 2 and all(s.closed == 1 for s in streams)


def _collect(speaker, sentences, timeout=5.0):
    import time

    seen = []
    speaker.on_sentence = seen.append
    for s in sentences:
        speaker.say(s)
    deadline = time.monotonic() + timeout
    while (not seen or seen[-1] is not None) and time.monotonic() < deadline:
        time.sleep(0.01)
    return seen


def test_muted_speaker_still_shows_each_sentence_for_a_reading_time(monkeypatch):
    from assistant.config import VoiceConfig
    from assistant.voice import tts

    monkeypatch.setattr(tts, "reading_time", lambda text: 0.02)
    speaker = tts.Speaker(VoiceConfig(tts_enabled=False))
    try:
        assert _collect(speaker, ["One.", "Two.", "  "]) == ["One.", "Two.", None]
        assert not speaker.speaking
    finally:
        speaker.close()


def test_stop_cuts_a_shown_sentence_short_and_a_failing_listener_does_not_kill_the_voice(monkeypatch):
    import time

    from assistant.config import VoiceConfig
    from assistant.voice import tts

    monkeypatch.setattr(tts, "reading_time", lambda text: 30.0)
    speaker = tts.Speaker(VoiceConfig(tts_enabled=False))
    try:
        started = threading.Event()

        def boom(sentence):
            started.set()
            raise RuntimeError("ui went away")

        speaker.on_sentence = boom
        speaker.say("A very long sentence.")
        speaker.say("Never shown.")
        assert started.wait(2)
        speaker.stop()
        deadline = time.monotonic() + 2
        while speaker.speaking and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not speaker.speaking
    finally:
        speaker.close()


def test_reading_time_grows_with_length_and_is_capped():
    from assistant.voice.tts import reading_time

    assert reading_time("Done.") < reading_time("I found two hundred old updates.") <= 7.0
    assert reading_time("word " * 200) == 7.0
