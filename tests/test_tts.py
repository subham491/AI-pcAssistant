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
