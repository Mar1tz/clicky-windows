"""Reply to the email on screen (ai/email_reply.py).

Asked for on 10 Oct 2026: "look at this email and write a reply" - Tora
reads the email the user is looking at, writes a reply in its tone, signed
off the way they sign off, and fills in the reply form: To, Subject, and
the body above the quoted original. Never sent: they press Send. The page's
own text is read; a picture only when the page shows none. Nothing of the
email is kept.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import companion_manager as cm
from ai import email_reply, gemini, write_for_me
from ai.email_reply import GmailWeb, Mail, MailError, Reply
from ai.gemini import Models

THREAD = ("Inbox\nSunflex Ops - additions made along the way\nJohn Rostance <john@sunflex.co.za> to me, "
          "Anna <anna@sunflex.co.za>\nHi Torben, thanks for the summary. Can we meet this week to go "
          "through it?\nRegards, John\nTorben Mikkelsen <mtorben642@gmail.com>\nHi John, I've attached a "
          "short summary.\nKind regards,\nTorben Mikkelsen\nAgentora")
ME = "mtorben642@gmail.com"


# -- which app ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("title, app", [
    ("Sunflex Ops - additions made along the way - mtorben642@gmail.com - Gmail - Opera", "Gmail"),
    ("Inbox (1,952) - mtorben642@gmail.com - Gmail - Google Chrome", "Gmail"),
    ("Sent Items - Torben Mikkelsen - Outlook", None),
    ("WhatsApp", None),
])
def test_the_app_in_front(title, app):
    found = email_reply.app_for(title)
    assert (found.name if found else None) == app


# -- the writing --------------------------------------------------------------------------------------------------

def _writer(monkeypatch, data=None):
    sent = {}

    async def fake_generate_json(model, contents, schema, **kw):
        sent.update(model=model, contents=contents, schema=schema, **kw)
        return data if data is not None else {
            "to": ["john@sunflex.co.za"], "cc": ["anna@sunflex.co.za", ME], "reply_all": True,
            "subject": "Re: Re: Sunflex Ops - additions made along the way",
            "body": "Hi John,\n\nYes, happy to meet. Does [confirm day] work?\n\nKind regards,\nTorben Mikkelsen\nAgentora"}
    monkeypatch.setattr(gemini, "generate_json", fake_generate_json)
    return sent


def test_the_reply_is_written_from_the_page_and_their_words(monkeypatch):
    sent = _writer(monkeypatch)
    reply = asyncio.run(email_reply.compose_reply("say yes but ask which day", Mail(THREAD, ME, "Gmail"),
                                                  "pic", draft="-- \nTorben"))
    assert sent["model"] == Models.WRITER
    parts = sent["contents"][0]["parts"]
    assert len(parts) == 1, "the page's text, so no picture"
    ask = parts[0]["text"]
    assert "Can we meet this week" in ask and "say yes but ask which day" in ask
    assert "already holds" in ask and "Torben" in ask, "a signature in the box: no second sign-off"
    system = " ".join(sent["system"].split())
    assert ME in system, "who the user is - so the right message is answered"
    assert "Never invent a fact, date, price or promise" in system and "[confirm date]" in system
    assert "Sign off the way the user signs off their own emails in the thread" in system
    assert reply.subject == "Re: Sunflex Ops - additions made along the way", "never Re: Re:"
    assert reply.to == ["john@sunflex.co.za"] and reply.cc == ["anna@sunflex.co.za"], "never cc'd to themselves"
    assert reply.reply_all and "[confirm day]" in reply.body


def test_a_page_with_no_text_is_read_from_its_picture(monkeypatch):
    sent = _writer(monkeypatch)
    asyncio.run(email_reply.compose_reply("reply to this", Mail("", ME, "Gmail"), "pic"))
    assert sent["contents"][0]["parts"][0] == gemini.image_part("pic")


def test_made_up_addresses_are_dropped(monkeypatch):
    _writer(monkeypatch, {"to": ["John Rostance", "john@sunflex.co.za"], "subject": "Re: x", "body": "Hi."})
    reply = asyncio.run(email_reply.compose_reply("reply", Mail(THREAD, ME)))
    assert reply.to == ["john@sunflex.co.za"]


def test_a_failed_call_fills_nothing(monkeypatch):
    async def fails(*_a, **_kw):
        raise RuntimeError("503")
    monkeypatch.setattr(gemini, "generate_json", fails)
    assert not asyncio.run(email_reply.compose_reply("reply", Mail(THREAD, ME)))


# -- filling Gmail's reply form -----------------------------------------------------------------------------------

class Page:
    """Gmail's page as UI Automation shows it, to the degree fill() looks."""

    def __init__(self, *, reply_open=False, subject_field=False, subject="", chips=True, reply_all_button=True):
        self.invoked, self.focused, self.pasted = [], [], []
        self.body = SimpleNamespace(Name="Message Body")
        row = SimpleNamespace(Name="row")
        row.GetParentControl = lambda: SimpleNamespace(Name="compose", GetParentControl=lambda: None)
        self.to_parent = row
        self.to = SimpleNamespace(Name="To recipients", GetParentControl=lambda: row)
        self.chips = [SimpleNamespace(Name="John Rostance <john@sunflex.co.za>")] if chips else []
        self.subject = SimpleNamespace(Name="Subject", value=subject)
        self.reply_open = reply_open
        self.subject_field = subject_field
        self.buttons = {"Reply": SimpleNamespace(Name="Reply"), "Send": SimpleNamespace(Name="Send")}
        if reply_all_button:
            self.buttons["Reply all"] = SimpleNamespace(Name="Reply all")

    def find_all(self, root, *, name=None, control_type=None, limit=40):
        if name is None:
            return list(self.chips)
        if name in self.buttons:
            return [self.buttons[name]]
        if name == "Message Body":
            return [self.body] if self.reply_open else []
        if name == "To recipients":
            return [self.to] if self.reply_open else []
        if name == "Subject":
            return [self.subject] if self.reply_open and self.subject_field else []
        return []

    def invoke(self, control):
        self.invoked.append(control.Name)
        if control.Name in ("Reply", "Reply all"):
            self.reply_open = True


class Keys:
    def __init__(self):
        self.done = []

    def modifiers_down(self):
        return False

    def to_start(self):
        self.done.append("ctrl+home")


@pytest.fixture
def page(monkeypatch):
    def make(**kw):
        p = Page(**kw)
        monkeypatch.setattr(email_reply, "_window", lambda: SimpleNamespace(Name="x - Gmail"))
        monkeypatch.setattr(email_reply, "_find_all", p.find_all)
        monkeypatch.setattr(email_reply, "_invoke", p.invoke)
        monkeypatch.setattr(email_reply, "_focus", lambda c: p.focused.append(c.Name))
        monkeypatch.setattr(email_reply, "_value", lambda c: getattr(c, "value", ""))
        monkeypatch.setattr(email_reply, "_shown", lambda c: True)
        monkeypatch.setattr(email_reply, "_wait", lambda found, seconds: found())
        monkeypatch.setattr(write_for_me, "paste", lambda text, **kw: p.pasted.append((p.focused[-1], text)) or "pasted")
        return p
    return make


REPLY = Reply(["john@sunflex.co.za"], ["anna@sunflex.co.za"], "Re: Sunflex Ops", "Hi John,\n\nYes.", True)


def test_no_reply_open_presses_gmails_reply_all_then_writes_at_the_top(page):
    p = page()
    keys = Keys()
    filled = GmailWeb().fill(REPLY, keyboard=keys)
    assert p.invoked == ["Reply all"], "Gmail's own Reply all fills To and Cc"
    assert "Send" not in p.invoked, "never sent"
    assert p.pasted == [("Message Body", "Hi John,\n\nYes.\n\n")]
    assert keys.done == ["ctrl+home"], "above the quoted original and the signature"
    assert filled == ["reply all", "body"]


def test_only_the_sender_gets_plain_reply(page):
    p = page()
    GmailWeb().fill(Reply(["john@sunflex.co.za"], [], "Re: x", "Hi.", False), keyboard=Keys())
    assert p.invoked == ["Reply"]


def test_an_open_reply_gets_its_empty_subject_and_keeps_its_recipients(page):
    p = page(reply_open=True, subject_field=True, subject="")
    filled = GmailWeb().fill(REPLY, keyboard=Keys())
    assert p.invoked == [], "a reply was open: nothing pressed"
    assert ("Subject", "Re: Sunflex Ops") in p.pasted
    assert not any(field == "To recipients" for field, _ in p.pasted), "John was already on it"
    assert filled == ["subject", "body"]


def test_a_subject_already_written_is_kept(page):
    p = page(reply_open=True, subject_field=True, subject="Meeting this week")
    GmailWeb().fill(REPLY, keyboard=Keys())
    assert not any(field == "Subject" for field, _ in p.pasted)


def test_an_open_reply_with_nobody_on_it_gets_the_addresses(page):
    p = page(reply_open=True, chips=False)
    filled = GmailWeb().fill(REPLY, keyboard=Keys())
    assert ("To recipients", "john@sunflex.co.za, anna@sunflex.co.za, ") in p.pasted
    assert "to" in filled


def test_no_reply_button_says_open_the_email(page):
    p = page()
    p.buttons.clear()
    with pytest.raises(MailError, match="open the email"):
        GmailWeb().fill(REPLY, keyboard=Keys())


# -- the voice tool -----------------------------------------------------------------------------------------------

class FakeGmail(email_reply.MailApp):
    name = "Gmail"

    def __init__(self, text=THREAD):
        self.text, self.filled_with = text, None

    def matches(self, title):
        return "Gmail" in title

    def read(self):
        return Mail(self.text, ME, self.name)

    def draft_text(self):
        return None

    def fill(self, reply, keyboard=None, clipboard=None):
        self.filled_with = reply
        return ["reply all", "body"]


def _manager(monkeypatch, app, title="Sunflex Ops - mtorben642@gmail.com - Gmail - Opera"):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._activity = lambda what: None
    mgr._privacy_guard = False
    card = []
    mgr.sig_response_chunk = SimpleNamespace(emit=card.append)
    monkeypatch.setattr(cm, "active_window_title", lambda: title)
    monkeypatch.setattr(email_reply, "APPS", [app])

    async def picture():
        return "pic"
    mgr._window_picture = picture
    return mgr, card


def test_the_voice_tool_fills_the_draft_and_never_sends(monkeypatch, caplog):
    _writer(monkeypatch)
    app = FakeGmail()
    mgr, card = _manager(monkeypatch, app)
    with caplog.at_level(logging.INFO):
        out = asyncio.run(mgr._tool_reply_to_email({"request": "say yes but ask which day"}))
    assert out["ok"] and "NOT sent" in out["result"] and "press Send" in out["result"]
    assert app.filled_with.to == ["john@sunflex.co.za"]
    assert card and card[0].startswith("Hi John,"), "the reply on the card"
    assert "email reply: Gmail, read the page" in caplog.text
    assert "Can we meet this week" not in caplog.text, "nothing of the email is kept"


def test_not_gmail_says_so(monkeypatch):
    mgr, _card = _manager(monkeypatch, FakeGmail(), title="Sent Items - Outlook")
    out = asyncio.run(mgr._tool_reply_to_email({"request": "reply to this"}))
    assert "Gmail" in out["error"]


def test_a_page_with_no_text_is_answered_from_a_picture(monkeypatch):
    sent = _writer(monkeypatch)
    mgr, _card = _manager(monkeypatch, FakeGmail(text=""))
    out = asyncio.run(mgr._tool_reply_to_email({"request": "reply to this"}))
    assert out["ok"] and sent["contents"][0]["parts"][0] == gemini.image_part("pic")


def test_the_voice_knows_replying_is_its_own_mode():
    import config
    from ai import gemini_realtime
    decl = next(d for d in gemini_realtime.tool_declarations()[0]["functionDeclarations"]
                if d["name"] == "reply_to_email")
    assert "NEVER sends" in decl["description"]
    rules = " ".join(config._REALTIME_RULES.split())
    assert "REPLYING TO THE EMAIL ON SCREEN is its own mode" in rules and "It never sends" in rules
    assert "reply_to_email" in cm.CompanionManager._tool_handlers(cm.CompanionManager.__new__(cm.CompanionManager))
