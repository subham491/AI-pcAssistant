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


def test_audio_keeps_the_moment_before_speech_was_detected():
    # A soft first sound ("h" in "hello") is below the speech level; without real pre-roll it is
    # cut off and Whisper drops or garbles the first word.
    ep = Endpointer(EndpointerConfig(preroll_s=0.3, min_speech_s=0.25))
    for _ in range(10):
        ep.feed(np.full(FRAME_SAMPLES, 7, dtype=np.int16))  # quiet lead-in, marked with the value 7
    for _ in range(6):
        ep.feed(frame(3000))
    audio = np.round(ep.audio() * 32768).astype(np.int16)
    lead_in = int(np.count_nonzero(audio == 7)) / FRAME_SAMPLES
    assert lead_in >= 3  # about 0.24 s of audio from before the first speech frame


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


def test_trailing_off_speech_is_not_read_as_silence():
    # Words often fade to well under the "speech started" level. That used to count as silence
    # and end the capture mid-sentence.
    ep = Endpointer(EndpointerConfig(end_silence_s=0.4))
    for _ in range(10):
        ep.feed(frame(50))
    for _ in range(6):
        ep.feed(frame(3000))
    for _ in range(12):  # ~1 s of soft speech: above the room noise, below the start threshold
        assert ep.feed(frame(260)) is EndState.SPEAKING
    assert [ep.feed(frame(50)) for _ in range(6)][-1] is EndState.DONE


def test_click_to_stop_waits_through_long_pauses_until_finish():
    ep = Endpointer(EndpointerConfig(end_silence_s=0.4, stop_on_silence=False))
    for _ in range(8):
        ep.feed(frame(3000))
    for _ in range(60):  # ~5 s pause
        assert ep.feed(frame(50)) is EndState.SPEAKING
    assert ep.finish() is EndState.DONE and ep.audio().size > 0


def test_finish_before_any_speech_reports_no_speech():
    ep = Endpointer(EndpointerConfig())
    ep.feed(frame(50))
    assert ep.finish() is EndState.NO_SPEECH
