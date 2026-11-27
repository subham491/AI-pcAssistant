from assistant.bubbles import FADE_S, SAY_LINGER_S, YOU_LINGER_S, Captions, Pill, Pills, extension, summarise


def test_your_words_show_then_give_way_to_the_sentence_being_spoken():
    c = Captions()
    assert not c.view(0).visible
    c.heard("  clean up   my inbox ", now=10)
    assert (c.view(10).you, c.view(10).say, c.view(10).opacity) == ("clean up my inbox", "", 1.0)
    c.working()  # Claude is thinking: the words stay however long it takes
    assert c.view(10 + YOU_LINGER_S + 60).visible
    c.spoken("I found 212 old updates.", now=80)
    assert (c.view(80).you, c.view(80).say) == ("", "I found 212 old updates.")
    c.spoken("Shall I bin them?", now=83)
    assert c.view(500).say == "Shall I bin them?"  # stays while the voice is on it


def test_the_last_sentence_lingers_then_fades_when_the_voice_goes_quiet():
    c = Captions()
    c.spoken("All done.", now=5)
    c.spoken(None, now=7)
    assert c.view(7 + SAY_LINGER_S).opacity == 1.0
    assert 0 < c.view(7 + SAY_LINGER_S + FADE_S / 2).opacity < 1
    assert not c.view(7 + SAY_LINGER_S + FADE_S).visible
    assert (c.you, c.say) == ("", "")  # cleared once gone


def test_unanswered_words_fade_on_their_own_and_a_finished_request_clears_up():
    c = Captions()
    c.heard("hello", now=0)
    assert c.view(YOU_LINGER_S - 0.1).visible and not c.view(YOU_LINGER_S + FADE_S).visible
    c.heard("stop", now=20)
    c.working()
    c.settle(now=21)  # handled without a spoken answer
    assert not c.view(21 + FADE_S).visible
    c.spoken(None, now=30)  # quiet with nothing up: nothing to do
    assert not c.view(30).visible


def test_summaries_and_extensions():
    diff = "--- a/x.py\n+++ b/x.py\n@@\n-old\n+new\n+more\n"
    assert summarise(diff, "diff") == "+2 −1 lines"
    assert summarise("print(1)", "text", "python") == "python · 1 line"
    assert summarise("a\nb\nc", "text") == "3 lines"
    assert (extension("Python"), extension("", "diff"), extension("klingon"), extension("powershell")) == \
        ("py", "diff", "txt", "ps1")


def test_pill_offers_view_only_when_there_is_more_than_fits():
    assert not Pill("approval", "1", body="git status").has_view
    assert Pill("approval", "1", body="x" * 71).has_view
    assert Pill("approval", "1", body="a\nb").has_view
    assert Pill("approval", "1", body="+a", body_kind="diff").has_view


def test_the_pill_in_front_is_typing_then_the_oldest_approval_then_error_then_newest_code():
    p = Pills()
    assert p.current() is None
    p.add_code("", "print(1)\nprint(2)", "python")
    p.add_code("Draft reply", "Hi there", "markdown")
    p.add_code("", "   ", "python")  # nothing in it: no pill
    cur = p.current()
    assert (cur.kind, cur.title, cur.sub) == ("code", "Draft reply", "markdown · 1 line · 1 more")
    assert p.codes[0].title == "Your code is ready"

    p.set_error("Claude  is not\nsigned in")
    assert (p.current().kind, p.current().sub) == ("error", "Claude is not signed in")

    p.add_approval("a1", "Edit todo.md", "review mode", "--- a\n+++ b\n-x\n+y\n", "diff")
    p.add_approval("a2", "Run command", "not on the safe list", "pip install requests", "text")
    cur = p.current()
    assert (cur.id, cur.title, cur.sub) == ("a1", "Edit todo.md?", "+1 −1 lines")
    p.close_approval("a1")
    assert (p.current().id, p.current().sub) == ("a2", "pip install requests")

    p.typing = True
    assert p.current().kind == "typing"
    p.typing = False
    p.close_approval("a2")
    assert p.current().kind == "error"
    p.dismiss("error")
    assert p.current().kind == "code"
    p.dismiss(p.current().id)
    assert p.current().sub == "python · 2 lines"  # the older one is still there


def test_a_new_request_drops_old_code_and_errors_but_never_a_pending_approval():
    p = Pills()
    p.add_code("", "x", "")
    p.set_error("boom")
    p.add_approval("a1", "Run command", "why", "", "text")
    assert p.current().sub == "why"  # no body: the reason is shown instead
    p.new_request()
    assert p.current().id == "a1" and not p.codes and p.error is None


def test_code_pills_are_capped():
    p = Pills()
    for i in range(20):
        p.add_code("", f"line {i}", "")
    assert len(p.codes) == 8 and p.current().body == "line 19"
