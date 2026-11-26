import numpy as np

from assistant.voice.endpoint import FRAME_SAMPLES, Endpointer, EndpointerConfig, EndState


def frame(level):
    rng = np.random.default_rng(0)
    return (rng.standard_normal(FRAME_SAMPLES) * level).astype(np.int16)


def test_speech_then_silence_ends_utterance():
    ep = Endpointer(EndpointerConfig(end_silence_s=0.4))
    for _ in range(10):
        assert ep.feed(frame(50)) is EndState.WAITING
    for _ in range(10):
        ep.feed(frame(3000))
    assert ep.state is EndState.SPEAKING
    states = [ep.feed(frame(50)) for _ in range(6)]
    assert states[-1] is EndState.DONE
    audio = ep.audio()
    assert audio.dtype == np.float32 and audio.size > 10 * FRAME_SAMPLES and np.abs(audio).max() <= 1.0


def test_no_speech_times_out():
    ep = Endpointer(EndpointerConfig(no_speech_timeout_s=0.8))
    states = [ep.feed(frame(50)) for _ in range(12)]
    assert states[-1] is EndState.NO_SPEECH


def test_short_click_is_ignored():
    ep = Endpointer(EndpointerConfig(min_speech_s=0.25))
    ep.feed(frame(4000))  # one 80 ms bump
    ep.feed(frame(50))
    assert ep.state is EndState.WAITING


def test_max_length():
    ep = Endpointer(EndpointerConfig(max_utterance_s=1.0))
    states = [ep.feed(frame(3000)) for _ in range(20)]
    assert EndState.TOO_LONG in states
