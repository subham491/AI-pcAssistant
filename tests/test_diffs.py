from assistant.diffs import UndoJournal, preview_edit


def test_edit_preview_and_crlf_preserving_undo(tmp_path):
    f = tmp_path / "a.py"
    f.write_bytes(b"x = 1\r\ny = 2\r\n")
    prev = preview_edit("Edit", {"old_string": "y = 2", "new_string": "y = 3"}, f)
    diff = prev.unified()
    assert "-y = 2" in diff and "+y = 3" in diff

    journal = UndoJournal()
    journal.record(f)
    f.write_bytes(b"x = 1\r\ny = 3\r\n")
    entry = journal.undo_last()
    assert entry is not None and f.read_bytes() == b"x = 1\r\ny = 2\r\n"
    assert journal.undo_last() is None


def test_write_new_file_and_undo_deletes(tmp_path):
    f = tmp_path / "new.txt"
    prev = preview_edit("Write", {"content": "hello\n"}, f)
    assert prev.before is None and "+hello" in prev.unified()
    journal = UndoJournal()
    journal.record(f)
    f.write_text("hello\n")
    journal.undo_last()
    assert not f.exists()


def test_edit_mismatch_is_explained(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("abc")
    prev = preview_edit("Edit", {"old_string": "zzz", "new_string": "y"}, f)
    assert prev.after is None and "Could not locate" in prev.unified()


def test_multiedit_applies_in_order(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("one two three\n")
    prev = preview_edit("MultiEdit", {"edits": [{"old_string": "one", "new_string": "1"},
                                                {"old_string": "1 two", "new_string": "1 2"}]}, f)
    assert prev.after == "1 2 three\n"


def test_replace_all(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a a a")
    assert preview_edit("Edit", {"old_string": "a", "new_string": "b", "replace_all": True}, f).after == "b b b"
    assert preview_edit("Edit", {"old_string": "a", "new_string": "b"}, f).after == "b a a"
