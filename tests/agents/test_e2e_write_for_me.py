"""Tora writes into the box you are in (ai/write_for_me.py).

Asked for on 10 Oct 2026: talk about what you want written - loosely, the
way you'd explain it - and Tora works out what you mean, writes it well,
and puts it in whatever text box you are typing in. Never a password box,
never Enter, and the clipboard it borrows is put back as it was. The
owner's spec the same day: dictation or command is decided first, and the
two never mix - a command is never typed, and dictation never acts.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import companion_manager as cm
from ai import gemini, write_for_me
from ai.gemini import Models
from ai.write_for_me import Box, Written

BOX = Box("EditControl", "Message", "Inbox - Gmail - Opera", "Hi Sam,\n")


# -- the writing ------------------------------------------------------------------------------------------------

def _writer(monkeypatch, reply="Thursday works for me, but Friday doesn't - see you then!", kind="dictation",
            command=""):
    sent = {}

    async def fake_generate_json(model, contents, schema, **kw):
        sent.update(model=model, contents=contents, schema=schema, **kw)
        return {"kind": kind, "text": reply, "command": command}
    monkeypatch.setattr(gemini, "generate_json", fake_generate_json)
    return sent


def test_the_writer_gets_their_words_the_box_and_the_window(monkeypatch):
    sent = _writer(monkeypatch)
    written = asyncio.run(write_for_me.compose("say thursday works but not friday, friendly", BOX, "pic"))
    assert written.kind == "dictation"
    assert written.text == "Thursday works for me, but Friday doesn't - see you then!"
    assert sent["model"] == Models.WRITER
    parts = sent["contents"][0]["parts"]
    assert parts[0] == gemini.image_part("pic"), "the window, for what 'this' means"
    ask = parts[-1]["text"]
    assert 'named "Message"' in ask and "Inbox - Gmail - Opera" in ask
    assert "Hi Sam," in ask, "what is in the box already"
    assert "say thursday works but not friday, friendly" in ask
    assert "ONLY what goes in the box" in sent["system"] and "[placeholder]" in sent["system"]
    system = " ".join(sent["system"].split())
    assert "changing their mind" in system and "filler" in system, "rambling made into clear text"
    assert sent["schema"]["properties"]["kind"]["enum"] == ["dictation", "command"]
    assert sent["schema"]["propertyOrdering"][0] == "kind", "it decides what this is first"


def test_a_command_is_not_written(monkeypatch):
    _writer(monkeypatch, reply="", kind="command", command="send the email to Sam")
    written = asyncio.run(write_for_me.compose("send an email to sam saying I'll be late", BOX))
    assert written.kind == "command" and written.text == "" and written.command == "send the email to Sam"


def test_the_writer_knows_write_from_send():
    system = " ".join(write_for_me._SYSTEM.split())
    assert "\"write an email saying I'll be late\" is dictation" in system
    assert "\"send an email saying I'll be late\" is a command" in system
    assert "it is dictation: a text box is focused" in system and "a command even with a text box open" in system


def test_without_a_picture_it_writes_from_the_words(monkeypatch):
    sent = _writer(monkeypatch)
    asyncio.run(write_for_me.compose("a tweet about our launch", Box("EditControl", "", "X")))
    assert len(sent["contents"][0]["parts"]) == 1


@pytest.mark.parametrize("raw, clean", [
    ('"Hello there."', "Hello there."),
    ("```\nprint('hi')\n```", "print('hi')"),
    ("  Plain text.  ", "Plain text."),
])
def test_the_text_alone(raw, clean):
    assert write_for_me._clean(raw) == clean


def test_a_failed_call_writes_nothing(monkeypatch):
    async def fails(*_a, **_kw):
        raise RuntimeError("503")
    monkeypatch.setattr(gemini, "generate_json", fails)
    written = asyncio.run(write_for_me.compose("anything", BOX))
    assert not written and written.text == ""


# -- into the box -----------------------------------------------------------------------------------------------

class FakeKeyboard:
    def __init__(self, held=0):
        self.held, self.done = held, []

    def modifiers_down(self):
        self.held -= 1
        return self.held >= 0

    def paste(self):
        self.done.append("ctrl+v")

    def type(self, text):
        self.done.append(("typed", text))


class FakeClipboard:
    def __init__(self, items=((13, b"theirs"),), opens=True):
        self.items, self.opens, self.log = list(items), opens, []

    def save(self):
        return list(self.items) if self.opens else None

    def set_text(self, text):
        self.log.append(("set", text))
        self.items = [(13, text)]
        return True

    def restore(self, items):
        self.log.append(("restore", items))
        self.items = list(items)


def test_it_is_pasted_and_the_clipboard_put_back(monkeypatch):
    monkeypatch.setattr(write_for_me, "PASTE_SETTLE_S", 0)
    kb, clip = FakeKeyboard(), FakeClipboard(items=[(13, b"theirs"), (49161, b"<html>")])
    assert write_for_me.put("Hello.", BOX, keyboard=kb, clipboard=clip, now_focused=lambda: BOX) == "pasted"
    assert kb.done == ["ctrl+v"], "pasted - and never Enter"
    assert clip.log[0] == ("set", "Hello.")
    assert clip.items == [(13, b"theirs"), (49161, b"<html>")], "every format they had, back"


def test_it_waits_for_the_talk_shortcut_to_be_let_go(monkeypatch):
    monkeypatch.setattr(write_for_me, "PASTE_SETTLE_S", 0)
    kb = FakeKeyboard(held=3)
    write_for_me.put("Hello.", BOX, keyboard=kb, clipboard=FakeClipboard(), now_focused=lambda: BOX)
    assert kb.held < 0 and kb.done == ["ctrl+v"]


def test_a_box_that_lost_the_focus_gets_nothing_and_the_text_waits_on_the_clipboard():
    kb, clip = FakeKeyboard(), FakeClipboard()
    other = Box("ButtonControl", "Send", "Inbox - Gmail - Opera")
    assert write_for_me.put("Hello.", BOX, keyboard=kb, clipboard=clip, now_focused=lambda: other) == "clipboard"
    assert kb.done == [] and clip.items == [(13, "Hello.")]


def test_a_clipboard_that_will_not_open_means_typing():
    kb = FakeKeyboard()
    assert write_for_me.put("Hi\nthere", BOX, keyboard=kb, clipboard=FakeClipboard(opens=False),
                            now_focused=lambda: BOX) == "typed"
    assert kb.done == [("typed", "Hi\nthere")]


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


# -- the voice tool ---------------------------------------------------------------------------------------------

def _manager(box, monkeypatch, *, written="Thursday works, Friday doesn't.", how="pasted", sensitive=False,
             kind="dictation"):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._activity = lambda what: None
    mgr._privacy_guard = sensitive
    asked = {"card": []}
    mgr.sig_response_chunk = SimpleNamespace(emit=asked["card"].append)
    monkeypatch.setattr(write_for_me, "focused_box", lambda: box)

    async def compose(request, b, picture=""):
        asked.update(request=request, box=b, picture=picture)
        return Written(kind, written if kind == "dictation" else "", "send the email" if kind == "command" else "")
    monkeypatch.setattr(write_for_me, "compose", compose)
    monkeypatch.setattr(write_for_me, "put", lambda text, b: asked.update(put=text) or how)

    async def picture():
        return "" if sensitive else "pic"
    mgr._window_picture = picture
    return mgr, asked


def test_the_voice_tool_writes_into_the_box(monkeypatch, caplog):
    mgr, asked = _manager(BOX, monkeypatch)
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        out = asyncio.run(mgr._tool_write_for_me({"request": "tell Sam thursday yes friday no"}))
    assert out.get("ok") and "error" not in out
    assert asked["request"] == "tell Sam thursday yes friday no" and asked["picture"] == "pic"
    assert asked["put"] == "Thursday works, Friday doesn't."
    assert "Thursday" not in str(out), "the voice is not handed the text to read out"
    assert asked["card"] == ["Thursday works, Friday doesn't.\n\n"], "what was written, on the card"
    assert "write for me: 31 chars into EditControl 'Message'" in caplog.text


def test_a_command_is_handed_back_and_nothing_typed(monkeypatch, caplog):
    mgr, asked = _manager(BOX, monkeypatch, kind="command")
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        out = asyncio.run(mgr._tool_write_for_me({"request": "send an email to sam saying I'll be late"}))
    assert out["ok"] is False and "Nothing was typed" in out["result"]
    assert "desktop_action" in out["result"] and "delegate_task" in out["result"], "Tora's actions, which ask first"
    assert "put" not in asked and asked["card"] == []
    assert "a command, not dictation" in caplog.text


def test_with_no_box_it_says_click_into_one(monkeypatch):
    mgr, asked = _manager(None, monkeypatch)
    out = asyncio.run(mgr._tool_write_for_me({"request": "a note"}))
    assert "click into" in out["error"] and "put" not in asked


def test_never_a_password_box(monkeypatch):
    mgr, asked = _manager(Box("EditControl", "Password", "Sign in", password=True), monkeypatch)
    out = asyncio.run(mgr._tool_write_for_me({"request": "my password"}))
    assert "password" in out["error"] and "request" not in asked


def test_a_box_that_lost_the_focus_leaves_it_on_the_clipboard(monkeypatch):
    mgr, _asked = _manager(BOX, monkeypatch, how="clipboard")
    out = asyncio.run(mgr._tool_write_for_me({"request": "a note"}))
    assert out.get("ok") and "Ctrl+V" in out["result"]


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
