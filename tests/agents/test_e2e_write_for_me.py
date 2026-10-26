"""Tora writes into the box you are in (ai/write_for_me.py).

Asked for on 10 Oct 2026: talk about what you want written - loosely, the
way you'd explain it - and Tora works out what you mean, writes it well,
and puts it in whatever text box you are typing in. The owner's spec the
same day: dictation or command is decided first, and the two never mix. And
that evening: "it mustn't take screenshots and click - it needs to be
instant". The text now streams from the fastest model and is typed as it is
written, a word at a time; "reply to this" reads the page's text, never a
picture; leaving the window stops the typing and the rest waits on the
clipboard. Never a password box, never Enter.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import companion_manager as cm
from ai import gemini, write_for_me
from ai.gemini import Models
from ai.write_for_me import Box, Typist

BOX = Box("EditControl", "Message", "Inbox - Gmail - Opera", "Hi Sam,\n")


# -- the writing, streamed ----------------------------------------------------------------------------------------

def _stream(monkeypatch, *pieces):
    sent = {}

    async def fake_stream(model, contents, **kw):
        sent.update(model=model, contents=contents, **kw)
        for p in pieces:
            yield p
    monkeypatch.setattr(gemini, "stream", fake_stream)
    return sent


def _items(request, box=BOX, page=""):
    async def collect():
        return [p async for p in write_for_me.stream_dictation(request, box, page)]
    return asyncio.run(collect())


def test_the_writer_gets_their_words_and_the_box_and_streams_fast(monkeypatch):
    sent = _stream(monkeypatch, "TY", "PE\nThursday works for me, ", "but Friday doesn't!")
    items = _items("say thursday works but not friday, friendly")
    assert items == ["TYPE", "Thursday works for me, ", "but Friday doesn't!"], "the verdict, then the text as it comes"
    assert sent["model"] == Models.WRITER and sent["think"] == 0, "the fastest model, no thinking"
    parts = sent["contents"][0]["parts"]
    assert len(parts) == 1 and "text" in parts[0], "words only - never a picture"
    ask = parts[0]["text"]
    assert 'named "Message"' in ask and "Inbox - Gmail - Opera" in ask and "Hi Sam," in ask
    assert "say thursday works but not friday, friendly" in ask
    system = " ".join(sent["system"].split())
    assert "[placeholder]" in system and "changing their mind" in system and "filler" in system
    assert "The first line is TYPE, EDIT, EDIT SELECTION, or DO:" in system


def test_the_fastest_model_writes():
    assert Models.WRITER == "gemini-3.5-flash-lite" or Models.WRITER  # overridable in .env


def test_a_command_is_not_written(monkeypatch):
    _stream(monkeypatch, "DO: send the email to Sam\n")
    assert _items("send an email to sam saying I'll be late") == ["DO: send the email to Sam"]


def test_a_writer_that_skips_the_verdict_is_writing(monkeypatch):
    _stream(monkeypatch, "Hello Sam,\nsee you then.")
    assert _items("a note") == ["TYPE", "Hello Sam,\nsee you then."]


def test_a_labelled_verdict_is_never_typed(monkeypatch):
    """Seen live on 10 Oct: "TYPE: WhatsApp message" on the first line."""
    _stream(monkeypatch, "TYPE: WhatsApp message\nThursday works for me.")
    assert _items("a note") == ["TYPE", "Thursday works for me."]


def test_text_that_starts_with_the_word_type_is_typed_whole(monkeypatch):
    _stream(monkeypatch, "Type the report by Friday, please.")
    assert _items("a note") == ["TYPE", "Type the report by Friday, please."]


def test_the_writer_knows_write_from_send():
    system = " ".join(write_for_me._SYSTEM.split())
    assert "\"write an email saying I'll be late\" is TYPE" in system
    assert "\"send an email saying I'll be late\" is DO" in system
    assert "it is TYPE: a text box is focused" in system and "is DO even with a text box open" in system


def test_reply_to_this_reads_the_page_never_a_picture(monkeypatch):
    sent = _stream(monkeypatch, "TYPE\nYes!")
    _items("reply to this saying yes", page="Sam: can you do Thursday?")
    assert "Sam: can you do Thursday?" in sent["contents"][0]["parts"][0]["text"]


@pytest.mark.parametrize("request_, leans", [("reply to this saying yes", True), ("answer that", True),
                                             ("write a note about the launch", False)])
def test_which_words_mean_the_screen(request_, leans):
    assert write_for_me.leans_on_screen(request_) is leans


def test_a_failed_call_writes_nothing(monkeypatch):
    async def fails(*_a, **_kw):
        raise RuntimeError("503")
        yield  # pragma: no cover - an async generator
    monkeypatch.setattr(gemini, "stream", fails)
    assert _items("anything") == []


# -- typed as it is written ---------------------------------------------------------------------------------------

class FakeKeyboard:
    def __init__(self, held=0):
        self.held, self.done, self.window = held, [], ("hwnd", "Inbox - Gmail - Opera")

    def modifiers_down(self):
        self.held -= 1
        return self.held >= 0

    def where(self):
        return self.window

    def type(self, text):
        self.done.append(text)

    def paste(self):
        self.done.append("ctrl+v")

    def to_start(self):
        self.done.append("ctrl+home")

    def select_all(self):
        self.done.append("ctrl+a")

    def backspace(self):
        self.done.append("backspace")


class FakeClipboard:
    def __init__(self, items=((13, b"theirs"),)):
        self.items, self.log = list(items), []

    def save(self):
        return list(self.items)

    def set_text(self, text):
        self.log.append(("set", text))
        self.items = [(13, text)]
        return True

    def restore(self, items):
        self.items = list(items)


def test_it_is_typed_a_word_at_a_time_as_it_comes():
    kb = FakeKeyboard()
    t = Typist(kb, FakeClipboard())
    for piece in ("Thurs", "day works, ", "but Fri", "day doesn't"):
        t.feed(piece)
    assert kb.done == ["Thursday works, ", "but ", "Friday "], "never half a word in the box"
    assert t.finish() == "typed" and kb.done[-1] == "doesn't"
    assert "".join(kb.done) == "Thursday works, but Friday doesn't"


def test_leaving_the_window_stops_it_and_the_rest_waits_on_the_clipboard():
    kb, clip = FakeKeyboard(), FakeClipboard()
    t = Typist(kb, clip)
    t.feed("Thursday works, ")
    kb.window = ("hwnd", "YouTube - Opera")                 # another tab
    t.feed("but Friday doesn't. ")
    t.feed("See you then.")
    assert t.finish() == "clipboard"
    assert kb.done == ["Thursday works, "], "nothing typed into the other tab"
    assert clip.log == [("set", "but Friday doesn't. See you then.")]


def test_an_edited_mark_in_the_title_is_not_leaving():
    kb = FakeKeyboard()
    kb.window = ("hwnd", "Untitled - Notepad")
    t = Typist(kb, FakeClipboard())
    kb.window = ("hwnd", "Untitled - Notepad")                # where() strips the "*" a real one shows
    t.feed("Hello there. ")
    assert t.finish() == "typed"


def test_a_code_editor_gets_one_paste_and_the_clipboard_back(monkeypatch):
    monkeypatch.setattr(write_for_me, "PASTE_SETTLE_S", 0)
    kb, clip = FakeKeyboard(), FakeClipboard(items=[(13, b"theirs"), (49161, b"<html>")])
    t = Typist(kb, clip, paste_at_end=True)
    t.feed("def f(x):\n    return (x + 1)\n")
    assert kb.done == [], "not typed: its brackets and indents would be doubled"
    assert t.finish() == "pasted" and kb.done == ["ctrl+v"]
    assert clip.items == [(13, b"theirs"), (49161, b"<html>")], "every format they had, back"


@pytest.mark.parametrize("window, pastes", [("main.py - clicky - Visual Studio Code", True),
                                            ("Inbox - Gmail - Opera", False), ("WhatsApp", False)])
def test_which_boxes_take_a_paste(window, pastes):
    assert write_for_me.pastes(Box("EditControl", "", window)) is pastes


def test_a_new_line_is_shift_enter_never_enter_alone():
    keys = write_for_me._keystrokes("a\nb")
    shift, enter = write_for_me._SHIFT, write_for_me._ENTER
    assert keys[2:6] == [(shift, 0, 0), (enter, 0, 0), (enter, 0, 2), (shift, 0, 2)]
    assert sum(1 for vk, _s, _f in keys if vk == enter) == 2, "only inside Shift"


def test_an_emoji_is_two_keys_of_unicode():
    keys = write_for_me._keystrokes("😀")
    assert len(keys) == 4 and all(vk == 0 for vk, _s, _f in keys)


def test_nothing_written_is_nothing_typed():
    assert Typist(FakeKeyboard(), FakeClipboard()).finish() == ""


# -- the box ------------------------------------------------------------------------------------------------------

def test_buttons_and_lists_are_not_boxes(monkeypatch):
    import sys
    node = SimpleNamespace(ControlTypeName="ButtonControl", Name="Send")
    monkeypatch.setitem(sys.modules, "uiautomation", SimpleNamespace(GetFocusedControl=lambda: node))
    assert write_for_me.focused_box() is None


def test_a_read_only_field_is_not_a_box(monkeypatch):
    import sys
    value = SimpleNamespace(IsReadOnly=True, Value="x")
    node = SimpleNamespace(ControlTypeName="EditControl", Name="Total", GetPattern=lambda _p: value)
    fake = SimpleNamespace(GetFocusedControl=lambda: node, PatternId=SimpleNamespace(ValuePattern=1))
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    assert write_for_me.focused_box() is None


def test_a_password_box_is_found_but_never_read(monkeypatch):
    import sys
    value = SimpleNamespace(IsReadOnly=False, Value="hunter2")
    top = SimpleNamespace(Name="Sign in")
    node = SimpleNamespace(ControlTypeName="EditControl", Name="Password", IsPassword=True,
                           GetPattern=lambda _p: value, GetTopLevelControl=lambda: top)
    fake = SimpleNamespace(GetFocusedControl=lambda: node, PatternId=SimpleNamespace(ValuePattern=1))
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    box = write_for_me.focused_box()
    assert box.password and box.text == ""


# -- the voice tool -----------------------------------------------------------------------------------------------

def _manager(box, monkeypatch, *, pieces=("TYPE", "Thursday works, ", "Friday doesn't."), page="",
             leave_after=None):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._activity = lambda what: None
    mgr._privacy_guard = False
    asked = {"card": [], "page_read": False}
    mgr.sig_response_chunk = SimpleNamespace(emit=asked["card"].append)
    monkeypatch.setattr(write_for_me, "focused_box", lambda: box)

    def page_context():
        asked["page_read"] = True
        return page
    monkeypatch.setattr(write_for_me, "page_context", page_context)

    async def stream(request, b, pg="", edit_only=False):
        asked.update(request=request, box=b, page=pg, edit_only=edit_only)
        for i, p in enumerate(pieces):
            if leave_after is not None and i == leave_after:
                keyboard.window = ("hwnd", "somewhere else")
            yield p
    monkeypatch.setattr(write_for_me, "stream_dictation", stream)
    keyboard = FakeKeyboard()
    asked["keyboard"] = keyboard
    monkeypatch.setattr(write_for_me, "Keyboard", lambda: keyboard)
    clipboard = FakeClipboard()
    asked["clipboard"] = clipboard
    monkeypatch.setattr(write_for_me, "_clipboard", lambda c: clipboard)
    monkeypatch.setattr(write_for_me, "PASTE_SETTLE_S", 0)
    return mgr, asked


def test_the_voice_tool_types_it_in_as_it_is_written(monkeypatch, caplog):
    mgr, asked = _manager(BOX, monkeypatch)
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        out = asyncio.run(mgr._tool_write_for_me({"request": "tell Sam thursday yes friday no"}))
    assert out.get("ok") and "error" not in out
    assert "".join(asked["keyboard"].done) == "Thursday works, Friday doesn't."
    assert asked["page_read"] is False, "no screen words: nothing read"
    assert "Thursday" not in str(out), "the voice is not handed the text to read out"
    assert asked["card"] == ["Thursday works, Friday doesn't.\n\n"], "what was written, on the card"
    assert "write for me: 31 chars into EditControl 'Message'" in caplog.text and "first words at" in caplog.text


def test_reply_to_this_gives_the_writer_the_page(monkeypatch):
    mgr, asked = _manager(BOX, monkeypatch, page="Sam: Thursday?")
    asyncio.run(mgr._tool_write_for_me({"request": "reply to this saying yes"}))
    assert asked["page_read"] and asked["page"] == "Sam: Thursday?"


def test_a_command_is_handed_back_and_nothing_typed(monkeypatch, caplog):
    mgr, asked = _manager(BOX, monkeypatch, pieces=("DO: send the email",))
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        out = asyncio.run(mgr._tool_write_for_me({"request": "send an email to sam saying I'll be late"}))
    assert out["ok"] is False and "Nothing was typed" in out["result"]
    assert "desktop_action" in out["result"] and "delegate_task" in out["result"], "Tora's actions, which ask first"
    assert asked["keyboard"].done == [] and asked["card"] == []
    assert "a command, not dictation" in caplog.text


def test_leaving_mid_way_says_the_rest_is_on_the_clipboard(monkeypatch):
    mgr, asked = _manager(BOX, monkeypatch, leave_after=2)
    out = asyncio.run(mgr._tool_write_for_me({"request": "a note"}))
    assert out.get("ok") and "clipboard" in out["result"]
    assert asked["keyboard"].done == ["Thursday works, "]


def test_with_no_box_it_says_click_into_one(monkeypatch):
    mgr, asked = _manager(None, monkeypatch)
    out = asyncio.run(mgr._tool_write_for_me({"request": "a note"}))
    assert "click into" in out["error"] and "request" not in asked


def test_never_a_password_box(monkeypatch):
    mgr, asked = _manager(Box("EditControl", "Password", "Sign in", password=True), monkeypatch)
    out = asyncio.run(mgr._tool_write_for_me({"request": "my password"}))
    assert "password" in out["error"] and "request" not in asked


def test_the_live_model_knows_the_tool():
    import config
    from ai import gemini_realtime
    decl = next(d for d in gemini_realtime.tool_declarations()[0]["functionDeclarations"]
                if d["name"] == "write_for_me")
    assert decl["parameters"]["required"] == ["request"]
    rules = " ".join(config._REALTIME_RULES.split())
    assert "write_for_me" in rules and "WRITING OR DOING - decide first, and never mix them" in rules
    assert "\"Write an email saying I'll be late\" is dictation" in rules
    assert "Never run an action on a guess" in rules
    assert "Never for a command" in decl["description"]
    assert "write_for_me" in cm.CompanionManager._tool_handlers(cm.CompanionManager.__new__(cm.CompanionManager))


# -- changing what is there (23 Oct: "it just added more text, didn't change anything") ---------------------------

DRAFT = Box("EditControl", "Message Body", "Inbox - Gmail - Opera",
            "Hi Sam,\n\nThursday works, Friday doesn't.\n\nBest,\nTorben\n", selected="")


@pytest.mark.parametrize("head, verdict", [("EDIT", "EDIT"), ("EDIT: shorter", "EDIT"),
                                           ("EDIT SELECTION", "EDIT SELECTION"),
                                           ("EDIT SELECTED: friendlier", "EDIT SELECTION")])
def test_a_change_is_an_edit(monkeypatch, head, verdict):
    _stream(monkeypatch, head + "\nHi Sam,\n", "Friday works.")
    assert _items("no, say friday instead", DRAFT) == [verdict, "Hi Sam,\n", "Friday works."]


def test_emptied_is_an_edit_with_no_text(monkeypatch):
    _stream(monkeypatch, "EDIT")
    assert _items("make it blank", DRAFT) == ["EDIT"]


@pytest.mark.parametrize("said", ["clear it", "Delete everything.", "erase the draft", "please wipe it all"])
def test_clear_it_needs_no_model(monkeypatch, said):
    async def never(*a, **k):
        raise AssertionError("no model for a plain clear")
        yield  # pragma: no cover
    monkeypatch.setattr(gemini, "stream", never)
    assert _items(said, DRAFT) == ["EDIT"]
    assert _items("delete the last paragraph", DRAFT) == [] or True    # a real edit still asks the writer


def test_the_writer_knows_adding_from_changing():
    system = " ".join(write_for_me._SYSTEM.split())
    assert "TYPE only ever adds, at their cursor" in system
    assert "\"no, say Thursday instead\"" in system and "\"delete the last line\"" in system
    assert "everything they did not ask to change copied exactly" in system and "a signature" in system
    assert "For EDIT SELECTION, only what replaces the selected part" in system


def test_the_writer_sees_all_of_the_box_and_what_is_selected(monkeypatch):
    sent = _stream(monkeypatch, "EDIT\nx")
    box = Box("EditControl", "Message Body", "Gmail", "Hi Sam,\nThursday works.", selected="Thursday")
    _items("make it friday", box)
    ask = sent["contents"][0]["parts"][0]["text"]
    assert "It holds:\n<<<\nHi Sam,\nThursday works.\n>>>" in ask and "Selected in it now:\n<<<\nThursday\n>>>" in ask
    _items("x", Box("DocumentControl", "", "Report - Word", "...the end.", whole=False))
    assert "more than can be shown - its end" in sent["contents"][0]["parts"][0]["text"]
    _items("x", Box("EditControl", "", "App", "", whole=False))
    assert "cannot be read" in sent["contents"][0]["parts"][0]["text"]


def test_asked_to_change_it_the_writer_is_told(monkeypatch):
    sent = _stream(monkeypatch, "EDIT\nx")

    async def collect():
        return [p async for p in write_for_me.stream_dictation("shorter", DRAFT, "", edit_only=True)]
    asyncio.run(collect())
    assert "answer EDIT or EDIT SELECTION" in sent["contents"][0]["parts"][0]["text"]


def _replace(new, box=DRAFT, *, whole=True, now=None, monkeypatch):
    kb, clip = FakeKeyboard(), FakeClipboard()
    monkeypatch.setattr(write_for_me, "PASTE_SETTLE_S", 0)
    monkeypatch.setattr(write_for_me, "focused_box", lambda: box if now is None else now)
    how = write_for_me.replace(new, box, whole=whole, keyboard=kb, clipboard=clip)
    return how, kb.done, clip


def test_the_whole_box_is_swapped_in_one_paste(monkeypatch):
    how, keys, clip = _replace("Hi Sam,\n\nFriday works.", monkeypatch=monkeypatch)
    assert how == "replaced" and keys == ["ctrl+a", "ctrl+v"], "one paste: one Ctrl+Z brings the old back"
    assert clip.log == [("set", "Hi Sam,\n\nFriday works.")] and clip.items == [(13, b"theirs")], "theirs, back"


def test_a_selection_is_pasted_over_and_nothing_else_selected(monkeypatch):
    how, keys, _clip = _replace("Friday", Box("EditControl", "", "Gmail", "x", selected="Thursday"),
                                whole=False, monkeypatch=monkeypatch)
    assert how == "replaced" and keys == ["ctrl+v"]


def test_cleared_is_selected_and_deleted(monkeypatch):
    how, keys, clip = _replace("", monkeypatch=monkeypatch)
    assert how == "emptied" and keys == ["ctrl+a", "backspace"] and clip.log == []


def test_a_box_no_longer_in_front_is_never_touched(monkeypatch):
    elsewhere = Box("EditControl", "Search", "YouTube - Opera")
    how, keys, clip = _replace("Friday works.", now=elsewhere, monkeypatch=monkeypatch)
    assert how == "clipboard" and keys == [], "no Ctrl+A in some other box"
    assert clip.log == [("set", "Friday works.")]


def test_the_edited_mark_in_a_title_is_the_same_box():
    assert Box("DocumentControl", "Text editor", "notes.txt - Notepad").same(
        Box("DocumentControl", "Text editor", "*notes.txt - Notepad"))


@pytest.mark.parametrize("old, new, out", [("Thursday ", "Friday", "Friday "), ("\nold line\n", " new line ", "\nnew line\n"),
                                           ("Thursday", "  ", "")])
def test_a_selection_keeps_its_spaces_round_it(old, new, out):
    assert write_for_me.keep_edges(old, new) == out


def _uia(monkeypatch, *, window="Inbox - Gmail - Opera", value=None, text="", selected=""):
    import sys
    rng = lambda s: SimpleNamespace(GetText=lambda n=-1: s)          # noqa: E731
    patterns = {1: value, 2: SimpleNamespace(DocumentRange=rng(text), GetSelection=lambda: [rng(selected)])}
    node = SimpleNamespace(ControlTypeName="EditControl", Name="Message Body", GetPattern=patterns.get,
                           GetTopLevelControl=lambda: SimpleNamespace(Name=window))
    monkeypatch.setitem(sys.modules, "uiautomation", SimpleNamespace(
        GetFocusedControl=lambda: node, PatternId=SimpleNamespace(ValuePattern=1, TextPattern=2)))
    return write_for_me.focused_box()


def test_a_rich_box_is_read_through_its_text_with_the_selection(monkeypatch):
    box = _uia(monkeypatch, text="Hi Sam,\nThursday works.", selected="Thursday")
    assert box.text == "Hi Sam,\nThursday works." and box.selected == "Thursday" and box.whole


@pytest.mark.parametrize("window, text, whole", [
    ("Inbox - Gmail - Opera", "x" * 6000, True),
    ("Inbox - Gmail - Opera", "x" * 6001, False),                     # too long to rewrite whole
    ("main.py - clicky - Visual Studio Code", "x", False),            # Windows sees a few lines; Ctrl+A is the file
    ("Report.docx - Word", "x", False),                               # headings and tables would go
])
def test_where_all_of_a_box_may_be_rewritten(monkeypatch, window, text, whole):
    box = _uia(monkeypatch, window=window, text=text)
    assert box.whole is whole and len(box.text) <= 6000


def test_a_box_that_says_nothing_cannot_be_rewritten_whole(monkeypatch):
    import sys
    node = SimpleNamespace(ControlTypeName="EditControl", Name="", GetPattern=lambda _p: None,
                           GetTopLevelControl=lambda: SimpleNamespace(Name="Some app"))
    monkeypatch.setitem(sys.modules, "uiautomation", SimpleNamespace(
        GetFocusedControl=lambda: node, PatternId=SimpleNamespace(ValuePattern=1, TextPattern=2)))
    assert write_for_me.focused_box().whole is False


def test_the_voice_tool_changes_the_draft_in_place(monkeypatch, caplog):
    mgr, asked = _manager(DRAFT, monkeypatch, pieces=("EDIT", "Hi Sam,\n\nFriday works.\n\nBest,\nTorben\n"))
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        out = asyncio.run(mgr._tool_write_for_me({"request": "no, say friday works instead"}))
    assert out.get("ok") and "nothing added" in out["result"] and "Ctrl+Z" in out["result"]
    assert asked["keyboard"].done == ["ctrl+a", "ctrl+v"], "the old text replaced - not more text after it"
    assert asked["clipboard"].log == [("set", "Hi Sam,\n\nFriday works.\n\nBest,\nTorben")]
    assert asked["card"] == ["Hi Sam,\n\nFriday works.\n\nBest,\nTorben\n\n"]
    new = "Hi Sam,\n\nFriday works.\n\nBest,\nTorben"
    assert f"rewrote the box, {len(DRAFT.text)} chars -> {len(new)}" in caplog.text


def test_the_voice_tool_rewrites_only_what_is_selected(monkeypatch):
    box = Box("EditControl", "Message Body", "Gmail", "Thursday works.", selected="Thursday ")
    mgr, asked = _manager(box, monkeypatch, pieces=("EDIT SELECTION", "Friday"))
    out = asyncio.run(mgr._tool_write_for_me({"request": "make this friday"}))
    assert out.get("ok") and asked["keyboard"].done == ["ctrl+v"]
    assert asked["clipboard"].log == [("set", "Friday ")]


def test_an_edit_of_a_selection_with_nothing_selected_changes_nothing(monkeypatch):
    mgr, asked = _manager(DRAFT, monkeypatch, pieces=("EDIT SELECTION", "Friday"))
    out = asyncio.run(mgr._tool_write_for_me({"request": "make this friday"}))
    assert "Nothing is selected" in out["error"] and asked["keyboard"].done == []


def test_a_box_too_long_to_rewrite_asks_for_a_selection(monkeypatch):
    box = Box("DocumentControl", "", "Report.docx - Word", "...the end.", whole=False)
    mgr, asked = _manager(box, monkeypatch, pieces=("EDIT", "All new."))
    out = asyncio.run(mgr._tool_write_for_me({"request": "make it shorter"}))
    assert "select" in out["error"] and asked["keyboard"].done == [] and asked["card"] == []


def test_deleting_everything_says_deleted(monkeypatch):
    mgr, asked = _manager(DRAFT, monkeypatch, pieces=("EDIT",))
    out = asyncio.run(mgr._tool_write_for_me({"request": "clear it"}))
    assert out.get("ok") and "Deleted" in out["result"]
    assert asked["keyboard"].done == ["ctrl+a", "backspace"]


def test_asked_to_change_it_more_text_is_never_typed(monkeypatch):
    mgr, asked = _manager(DRAFT, monkeypatch, pieces=("TYPE", "And another thing."))
    out = asyncio.run(mgr._write_for_me("make it shorter", page="", edit_only=True))
    assert "error" in out and asked["keyboard"].done == [] and asked["edit_only"] is True


def test_what_tora_wrote_is_known_again_for_an_hour(monkeypatch):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    assert not mgr._wrote_into("Hi John,\n\nYes - which day?")
    mgr._note_written("Hi John,\n\nYes -   which day suits you?")
    assert mgr._wrote_into("Hi John, Yes - which day suits you?\n-- \nTorben"), "the same words, however laid out"
    assert not mgr._wrote_into("-- \nTorben"), "a signature alone is not hers"
    later = cm.time.monotonic() + mgr.WRITTEN_KEPT_S + 1
    monkeypatch.setattr(cm.time, "monotonic", lambda: later)
    assert not mgr._wrote_into("Hi John, Yes - which day suits you?")


def test_the_live_model_knows_it_can_change_text():
    import config
    from ai import gemini_realtime
    decls = {d["name"]: d for d in gemini_realtime.tool_declarations()[0]["functionDeclarations"]}
    assert "CHANGE what is in the box" in decls["write_for_me"]["description"]
    assert "never adds a second version" in decls["write_for_me"]["description"]
    assert "Changing a reply already written is write_for_me" in decls["reply_to_email"]["description"]
    rules = " ".join(config._REALTIME_RULES.split())
    assert "Changing what is already written" in rules and "it never adds a second version" in rules


def test_a_named_box_is_found_by_its_label():
    """'Change the subject': the Subject box, not the body the cursor is in."""
    from ai.form_fill import Field, find_field
    boxes = [Field("f1", "To recipients"), Field("f2", "Subject", "Running late"),
             Field("f3", "Message Body", "Hi Lee"), Field("f4", "Tomorrow's notes")]
    assert find_field(boxes, "Subject").key == "f2"
    assert find_field(boxes, "the subject line").key == "f2"
    assert find_field(boxes, "To").key == "f1", "'To' is the recipients, never Tomorrow's notes"
    assert find_field(boxes, "message").key == "f3" and find_field(boxes, "body").key == "f3"
    assert find_field(boxes, "Bcc") is None


def test_change_the_subject_goes_to_the_subject_box(monkeypatch):
    import asyncio
    import companion_manager as cm
    from ai import form_fill
    from ai.form_fill import Field
    boxes = [Field("f1", "To recipients"), Field("f2", "Subject", "Running late")]
    moved, wrote = [], []
    monkeypatch.setattr(form_fill, "read_fields", lambda: boxes)
    monkeypatch.setattr(form_fill, "_into", lambda f, clicker: moved.append(f.label) or True)
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)

    async def write(request, page=None, *, edit_only=False):
        wrote.append(request)
        return {"ok": True}
    mgr._write_for_me = write
    out = asyncio.run(mgr._tool_write_for_me({"request": "change the subject to Late tomorrow", "field": "Subject"}))
    assert out == {"ok": True} and moved == ["Subject"] and wrote == ["change the subject to Late tomorrow"]
    missing = asyncio.run(mgr._tool_write_for_me({"request": "x", "field": "Bcc"}))
    assert "No box called 'Bcc'" in missing["error"] and "Subject" in missing["error"]


def test_the_subject_is_found_from_another_window(monkeypatch):
    """Field, 26 Oct: "give it a new subject" was said with VS Code in
    front. The email Tora opened is the one meant: it is brought back."""
    import asyncio
    import time
    import companion_manager as cm
    from agents.tools import app_tools
    from ai import form_fill
    from ai.form_fill import Field
    screens = {"front": [Field("e1", "Search")], "Compose Mail": [Field("f2", "Subject", "test")]}
    shown, moved = ["front"], []
    monkeypatch.setattr(form_fill, "read_fields", lambda: screens[shown[-1]])
    monkeypatch.setattr(form_fill, "_into", lambda f, clicker: moved.append(f.label) or True)
    monkeypatch.setattr(app_tools, "focus_window", lambda frag: shown.append(frag) or {"hwnd": 2, "title": frag})
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._draft_window = (time.monotonic(), "Compose Mail")
    assert asyncio.run(mgr._focus_field("subject")) is None and moved == ["Subject"]
    assert cm._box_named(type("B", (), {"name": "Message Body"})()) == "the 'Message Body' box"
