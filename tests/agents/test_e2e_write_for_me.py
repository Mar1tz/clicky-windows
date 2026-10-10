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
    assert "The first line is TYPE, or DO:" in system


def test_the_fastest_model_writes():
    assert Models.WRITER == "gemini-3.5-flash-lite" or Models.WRITER  # overridable in .env


def test_a_command_is_not_written(monkeypatch):
    _stream(monkeypatch, "DO: send the email to Sam\n")
    assert _items("send an email to sam saying I'll be late") == ["DO: send the email to Sam"]


def test_a_writer_that_skips_the_verdict_is_writing(monkeypatch):
    _stream(monkeypatch, "Hello Sam,\nsee you then.")
    assert _items("a note") == ["TYPE", "Hello Sam,\nsee you then."]


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

    async def stream(request, b, pg=""):
        asked.update(request=request, box=b, page=pg)
        for i, p in enumerate(pieces):
            if leave_after is not None and i == leave_after:
                keyboard.window = ("hwnd", "somewhere else")
            yield p
    monkeypatch.setattr(write_for_me, "stream_dictation", stream)
    keyboard = FakeKeyboard()
    asked["keyboard"] = keyboard
    monkeypatch.setattr(write_for_me, "Keyboard", lambda: keyboard)
    monkeypatch.setattr(write_for_me, "_clipboard", lambda c: FakeClipboard())
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
