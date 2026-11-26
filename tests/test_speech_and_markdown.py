from assistant.ui.markdown import split_code_blocks
from assistant.voice.speech_text import SpeechChunker, clean_for_speech


def run(deltas):
    c = SpeechChunker()
    out = []
    for d in deltas:
        out += c.feed(d)
    return out + c.flush()


def test_sentences_stream_out_early():
    c = SpeechChunker()
    assert c.feed("The test fails because the mock is never awaited. ") == []
    out = c.feed("Add an await on line 12.")
    assert out == ["The test fails because the mock is never awaited."]
    assert c.flush() == ["Add an await on line 12."]


def test_code_fences_are_not_spoken_even_when_split():
    text = "Here is the fix for the bug in the loader:\n``" + "`python\nprint('secret code')\n``" + "`\nThat should do it now."
    deltas = [text[i:i + 7] for i in range(0, len(text), 7)]
    spoken = " ".join(run(deltas))
    assert "print" not in spoken and "secret" not in spoken
    assert "I've put the code on screen." in spoken
    assert "That should do it now." in spoken


def test_markdown_cleanup():
    assert clean_for_speech("**Bold** see https://example.com and `x`") == "Bold see a link and x"


def test_split_code_blocks():
    segs = split_code_blocks("Intro\n```python\na = 1\n```\nOutro")
    assert [(s.kind, s.text, s.language) for s in segs] == [
        ("prose", "Intro", ""), ("code", "a = 1", "python"), ("prose", "Outro", "")]
