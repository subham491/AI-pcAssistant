from assistant.voice.conversation import HOLDOFF_S, Conversation


def make(follow_up_s=25.0):
    t = [0.0]
    return Conversation(follow_up_s, lambda: t[0]), t


def test_closed_until_a_turn_happens_then_open_for_the_window_after_the_reply():
    c, t = make()
    assert not c.is_open()
    c.touch()
    assert c.is_open()
    t[0] = 100.0
    assert c.speaking(True) and c.is_open()  # still replying: window keeps renewing
    t[0] = 100.0 + 24
    assert c.is_open()
    t[0] = 100.0 + 26
    assert not c.is_open()


def test_ignores_the_room_ringing_just_after_the_reply():
    c, t = make()
    c.speaking(True)
    t[0] = HOLDOFF_S / 2
    assert c.speaking(False)
    t[0] = HOLDOFF_S + 0.1
    assert not c.speaking(False)


def test_zero_turns_it_off():
    c, t = make(0)
    c.touch()
    assert not c.is_open()
    c.speaking(True)
    t[0] = 0.1
    assert not c.speaking(False)
