import threading

import numpy as np

from assistant.voice.tts import PocketVoice


class FakeStream:
    def __init__(self):
        self.written, self.aborted, self.started = [], 0, 0

    def write(self, data):
        self.written.append(data)

    def abort(self):
        self.aborted += 1

    def start(self):
        self.started += 1


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
    PocketVoice(FakeModel(), object(), stream).speak("hello", threading.Event())
    assert [c.shape for c in stream.written] == [(480, 1)] * 4
    assert stream.aborted == 0


def test_pocket_voice_stop_cuts_off_and_reopens_the_stream():
    stream = FakeStream()
    PocketVoice(FakeModel(stop_after=2), object(), stream).speak("hello", threading.Event())
    assert len(stream.written) == 2  # nothing after the stop
    assert (stream.aborted, stream.started) == (1, 1)  # buffered audio dropped, ready for the next reply
