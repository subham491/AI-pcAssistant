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

    lis = Listener(VoiceConfig(wake_phrase="hellbot"), on_utterance, states.append,
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
