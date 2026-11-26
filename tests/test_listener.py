import threading

import numpy as np

from assistant.config import VoiceConfig
from assistant.voice.endpoint import FRAME_SAMPLES, SAMPLE_RATE
from assistant.voice.listener import CallDetector, Listener, exe_is_running


def _run_listener(muted: bool):
    """Feed quiet, 1 s of 'speech', quiet into a wake-phrase listener; return (utterances, states)."""
    heard, states, done = [], [], threading.Event()

    def on_utterance(audio, needs_wake):
        heard.append((audio.size, needs_wake))
        done.set()

    lis = Listener(VoiceConfig(wake_word_enabled=True, wake_phrase="hellbot"), on_utterance, states.append,
                   is_speaking=lambda: False, wake_muted=lambda: muted)
    for level, seconds in ((50, 0.5), (5000, 1.0), (50, 1.0)):
        for _ in range(round(seconds * SAMPLE_RATE / FRAME_SAMPLES)):
            lis._frames.put(np.full(FRAME_SAMPLES, level, dtype=np.int16))
    lis._thread.start()
    done.wait(1.5)
    lis.close()
    lis._thread.join(2)
    return heard, states


def test_wake_phrase_mode_hands_room_speech_to_stt_quietly():
    heard, states = _run_listener(muted=False)
    assert len(heard) == 1 and heard[0][0] > 0
    assert heard[0][1] is True  # "needs the wake phrase": the app drops it unless it starts with one
    assert states == []  # no beep, no "Listening…" for speech that may not be for us


def test_wake_phrase_mode_does_not_listen_during_a_call():
    assert _run_listener(muted=True) == ([], [])


def test_stale_mic_entry_is_ignored_when_its_exe_is_not_running():
    # Windows leaves "in use" records behind when an app crashes or updates while holding the mic.
    key = r"C:#Users#me#AppData#Local#Discord#app-1.0.9042#Discord.exe"
    assert not exe_is_running(key, {r"c:\users\me\appdata\local\discord\app-1.0.9269\discord.exe"})
    assert exe_is_running(key, {r"c:\users\me\appdata\local\discord\app-1.0.9042\discord.exe"})


def test_call_detector_matches_names_case_insensitively():
    assert CallDetector(["teams"], probe=lambda: [r"C:#Program Files#Teams#Teams.exe"])()
    assert not CallDetector(["teams"], probe=lambda: [r"C:#Tools#python.exe"])()


def _frames(lis, level, seconds):
    for _ in range(round(seconds * SAMPLE_RATE / FRAME_SAMPLES)):
        lis._frames.put(np.full(FRAME_SAMPLES, level, dtype=np.int16))


def _click_listener(**voice):
    heard, states, levels, done = [], [], [], threading.Event()
    lis = Listener(VoiceConfig(**voice), lambda audio, needs_wake: (heard.append((audio.size, needs_wake)), done.set()),
                   states.append, is_speaking=lambda: False, wake_muted=lambda: False, on_level=levels.append)
    return lis, heard, states, levels, done


def _wait_for(cond, timeout=2.0):
    import time
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return cond()


def test_mic_is_ignored_until_the_click():
    lis, heard, states, _levels, done = _click_listener()
    _frames(lis, 5000, 1.0)
    _frames(lis, 50, 3.0)
    lis._thread.start()
    assert not done.wait(0.6) and heard == [] and states == []
    lis.close()
    lis._thread.join(2)


def test_click_then_a_pause_longer_than_the_old_cutoff_still_gets_the_whole_sentence():
    lis, heard, states, levels, done = _click_listener(end_silence_s=2.0)
    lis._thread.start()
    lis.trigger()
    assert _wait_for(lambda: states == ["listening"])
    _frames(lis, 5000, 1.0)
    _frames(lis, 50, 1.2)  # thinking pause: the old 0.7 s cutoff ended the capture here
    _frames(lis, 5000, 1.0)
    _frames(lis, 50, 2.2)
    assert done.wait(2)
    lis.close()
    lis._thread.join(2)
    assert len(heard) == 1 and heard[0][1] is False
    assert heard[0][0] >= int(3.2 * SAMPLE_RATE)  # both halves and the pause between them
    assert states == ["listening", "transcribing"] and max(levels) > 0.5 and levels[-1] == 0.0


def test_second_click_finishes_without_waiting_for_silence():
    lis, heard, states, _levels, done = _click_listener(stop_on_silence=False)
    lis._thread.start()
    lis.trigger()
    assert _wait_for(lambda: states == ["listening"])
    _frames(lis, 5000, 1.0)
    _frames(lis, 50, 3.0)
    assert _wait_for(lambda: lis._frames.empty()) and not done.is_set()  # silence alone never ends it
    lis.trigger()
    assert done.wait(2) and states[-1] == "transcribing"
    lis.close()
    lis._thread.join(2)


def test_cancel_drops_what_was_heard():
    lis, heard, states, _levels, _done = _click_listener()
    lis._thread.start()
    lis.trigger()
    assert _wait_for(lambda: states == ["listening"])
    _frames(lis, 5000, 1.0)
    assert _wait_for(lambda: lis._frames.empty())
    lis.cancel()
    assert _wait_for(lambda: states[-1] == "idle") and heard == []
    lis.close()
    lis._thread.join(2)
