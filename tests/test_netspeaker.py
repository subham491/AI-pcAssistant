import threading
import urllib.request

import numpy as np

from assistant.voice import netspeaker


volumes: list[int] = []


def fake_renderer(monkeypatch, tmp_path):
    """A speaker that fetches the stream when told to Play, like the real one."""
    calls, heard, done = [], [], threading.Event()
    volumes.clear()

    def soap(url, action, args="", service=""):
        calls.append(action)
        if action == "GetVolume":
            return "<CurrentVolume>60</CurrentVolume>"
        if action == "SetVolume":
            volumes.append(int(args.split("<DesiredVolume>")[1].split("<")[0]))
        if action == "SetAVTransportURI":
            stream_url = args.split("<CurrentURI>")[1].split("</CurrentURI>")[0]

            def fetch():
                heard.append(urllib.request.urlopen(stream_url, timeout=5).read())
                done.set()

            threading.Thread(target=fetch, daemon=True).start()

    monkeypatch.setattr(netspeaker, "find_renderer", lambda name: ("Fake Bar", "http://x/ctl", "http://x/vol", "127.0.0.1"))
    monkeypatch.setattr(netspeaker, "_DUCKED", tmp_path / "ducked.txt")
    monkeypatch.setattr(netspeaker, "_soap", soap)
    monkeypatch.setattr(netspeaker, "LATENCY_S", 0.0)
    return calls, heard, done


def test_one_wav_stream_spans_the_sentences_of_a_reply(monkeypatch, tmp_path):
    calls, heard, done = fake_renderer(monkeypatch, tmp_path)
    speaker = netspeaker.NetSpeaker("bar", 24000)
    for _sentence in range(2):
        with speaker as stream:
            stream.write(np.full((240, 1), 0.5, dtype="float32"))
    speaker.rest(threading.Event())
    assert done.wait(5)
    assert calls == ["SetAVTransportURI", "Play"]  # started once, not per sentence
    header, pcm = heard[0][:44], np.frombuffer(heard[0][44:], dtype="<i2")
    assert header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    assert len(pcm) == 480 and set(pcm) == {16383}  # float 0.5 as 16-bit mono, nothing lost


def test_abort_stops_the_speaker_and_the_next_reply_starts_a_new_stream(monkeypatch, tmp_path):
    calls, heard, done = fake_renderer(monkeypatch, tmp_path)
    speaker = netspeaker.NetSpeaker("bar", 24000)
    speaker.write(np.zeros((240, 1), dtype="float32"))
    speaker.abort()
    assert done.wait(5) and calls[-1] == "Stop"
    speaker.rest(threading.Event())  # nothing left to wait for
    speaker.write(np.zeros((240, 1), dtype="float32"))
    assert calls.count("Play") == 2
    speaker.abort()


def test_while_the_tv_is_on_the_reply_goes_to_the_pc_and_the_speaker_drops_to_a_third(monkeypatch, tmp_path):
    calls, _heard, _done = fake_renderer(monkeypatch, tmp_path)
    tv_on = [True]
    monkeypatch.setattr(netspeaker, "_tv_is_on", lambda url: tv_on[0])
    speaker = netspeaker.NetSpeaker("bar", 24000, "http://tv/status")
    assert speaker.shared() and speaker.shared()  # asked per sentence, decided once per reply
    assert volumes == [20] and "Play" not in calls  # 60 -> a third, and the TV's sound was not cut
    speaker.rest(threading.Event())
    assert volumes == [20, 60] and not netspeaker._DUCKED.exists()
    tv_on[0] = False
    assert not speaker.shared()  # TV off: the speaker is free again


def test_a_volume_left_down_by_a_killed_reply_is_put_back_at_the_next_start(monkeypatch, tmp_path):
    fake_renderer(monkeypatch, tmp_path)
    netspeaker._DUCKED.write_text("55")
    netspeaker.NetSpeaker("bar", 24000)
    assert volumes == [55] and not netspeaker._DUCKED.exists()
