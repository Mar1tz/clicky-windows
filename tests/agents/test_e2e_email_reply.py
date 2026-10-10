"""Reply to the email on screen (ai/email_reply.py).

Asked for on 10 Oct 2026: "look at this email and write a reply" - Tora
reads the email the user is looking at, writes a reply in its tone, signed
off the way they sign off, and fills in the reply form. Never sent: they
press Send. That evening: "it mustn't take screenshots and click - it needs
to be instant". So the page's own text is read (never a picture), Gmail's
Reply is pressed through Windows (no mouse) while it is read, the subject
comes from the window's title, and the body is typed in as it is written.
Nothing of the email is kept.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import companion_manager as cm
from ai import email_reply, gemini, write_for_me
from ai.email_reply import GmailWeb, Mail, MailError
from ai.gemini import Models

THREAD = ("Inbox\nSunflex Ops - additions made along the way\nJohn Rostance <john@sunflex.co.za> to me, "
          "Anna <anna@sunflex.co.za>\nHi Torben, thanks for the summary. Can we meet this week to go "
          "through it?\nRegards, John\nTorben Mikkelsen <mtorben642@gmail.com>\nHi John, I've attached a "
          "short summary.\nKind regards,\nTorben Mikkelsen\nAgentora")
ME = "mtorben642@gmail.com"
TITLE = "Sunflex Ops – additions made along the way - mtorben642@gmail.com - Gmail - Opera"


# -- which app, which subject, who it goes to -------------------------------------------------------------------

@pytest.mark.parametrize("title, app", [
    (TITLE, "Gmail"),
    ("Inbox (1,952) - mtorben642@gmail.com - Gmail - Google Chrome", "Gmail"),
    ("Sent Items - Torben Mikkelsen - Outlook", None),
    ("WhatsApp", None),
])
def test_the_app_in_front(title, app):
    found = email_reply.app_for(title)
    assert (found.name if found else None) == app


def test_the_subject_is_the_windows_title():
    assert GmailWeb.subject_of(TITLE) == "Sunflex Ops – additions made along the way"
    assert GmailWeb.subject_of("WhatsApp") == ""


@pytest.mark.parametrize("said, all_", [("reply to this", True), ("say yes but ask about pricing", True),
                                        ("just reply to him", False), ("only send it back to the sender", False),
                                        ("reply, not to everyone", False)])
def test_reply_all_unless_they_say_only_the_sender(said, all_):
    assert email_reply.reply_all_wanted(said) is all_


# -- the writing, streamed ----------------------------------------------------------------------------------------

def _stream(monkeypatch, *pieces):
    sent = {}

    async def fake_stream(model, contents, **kw):
        sent.update(model=model, contents=contents, **kw)
        for p in pieces:
            yield p
    monkeypatch.setattr(gemini, "stream", fake_stream)
    return sent


def _written(request="say yes but ask which day", mail=Mail(THREAD, ME, "Gmail"), draft="-- \nTorben"):
    async def collect():
        return [p async for p in email_reply.stream_reply(request, mail, draft)]
    return asyncio.run(collect())


def test_the_reply_is_written_from_the_page_and_their_words(monkeypatch):
    sent = _stream(monkeypatch, "\n Hi John,\n\n", "Yes, happy to meet. ", "Which day works?")
    assert _written() == ["Hi John,\n\n", "Yes, happy to meet. ", "Which day works?"]
    assert sent["model"] == Models.WRITER and sent["think"] == 0, "the fastest model, no thinking"
    parts = sent["contents"][0]["parts"]
    assert len(parts) == 1 and "text" in parts[0], "the page's text - never a picture"
    ask = parts[0]["text"]
    assert "Can we meet this week" in ask and "say yes but ask which day" in ask
    assert "already holds" in ask and "Torben" in ask, "a signature in the box: no second sign-off"
    system = " ".join(sent["system"].split())
    assert ME in system, "who the user is - so the right message is answered"
    assert "Never invent a fact, date, price or promise" in system and "[confirm date]" in system
    assert "Sign off the way the user signs off their own emails in the thread" in system
    assert "ONLY the body" in system


def test_a_failed_call_types_nothing(monkeypatch):
    async def fails(*_a, **_kw):
        raise RuntimeError("503")
        yield  # pragma: no cover - an async generator
    monkeypatch.setattr(gemini, "stream", fails)
    assert _written() == []


# -- opening Gmail's reply, through Windows ---------------------------------------------------------------------

class Page:
    """Gmail's page as UI Automation shows it, to the degree open_reply() looks."""

    def __init__(self, *, reply_open=False, subject_field=False, subject="", reply_all_button=True, text=THREAD,
                 settable=True):
        self.invoked, self.focused, self.timeline = [], [], []
        self.settable = settable
        self.body = SimpleNamespace(Name="Message Body", value="")
        self.subject = SimpleNamespace(Name="Subject", value=subject)
        self.reply_open, self.subject_field, self.text = reply_open, subject_field, text
        self.buttons = {"Reply": SimpleNamespace(Name="Reply"), "Send": SimpleNamespace(Name="Send")}
        if reply_all_button:
            self.buttons["Reply all"] = SimpleNamespace(Name="Reply all")

    def find_all(self, root, *, name=None, control_type=None, limit=40):
        if name in self.buttons:
            return [self.buttons[name]]
        if name == "Message Body":
            return [self.body] if self.reply_open else []
        if name == "Subject":
            return [self.subject] if self.reply_open and self.subject_field else []
        return []

    def invoke(self, control):
        self.invoked.append(control.Name)
        if control.Name in ("Reply", "Reply all"):
            self.reply_open = True

    def set_value(self, control, text):
        if not self.settable:
            return False
        control.value = text
        self.timeline.append(("set", control.Name, text))
        return True

    def focus(self, control):
        self.focused.append(control.Name)
        self.timeline.append(("focus", control.Name))


class Keys:
    def __init__(self):
        self.done = []

    def modifiers_down(self):
        return False

    def to_start(self):
        self.done.append("ctrl+home")

    def select_all(self):
        self.done.append("ctrl+a")

    def type(self, text):
        self.done.append(("typed", text))


@pytest.fixture
def page(monkeypatch):
    from ai import uia_page

    def make(**kw):
        p = Page(**kw)
        monkeypatch.setattr(uia_page, "window", lambda: SimpleNamespace(Name=TITLE))
        monkeypatch.setattr(uia_page, "find_all", p.find_all)
        monkeypatch.setattr(uia_page, "invoke", p.invoke)
        monkeypatch.setattr(uia_page, "focus", p.focus)
        monkeypatch.setattr(uia_page, "set_value", p.set_value)
        monkeypatch.setattr(uia_page, "value", lambda c: getattr(c, "value", ""))
        monkeypatch.setattr(uia_page, "shown", lambda c: True)
        monkeypatch.setattr(uia_page, "wait", lambda found, seconds: found())
        monkeypatch.setattr(uia_page, "page_text", lambda win: p.text)
        return p
    return make


def test_no_reply_open_presses_gmails_reply_all_and_puts_the_caret_at_the_top(page):
    p = page()
    keys = Keys()
    draft, done = GmailWeb().open_reply(True, keys)
    assert p.invoked == ["Reply all"], "Gmail's own Reply all fills To and Cc - pressed, not clicked"
    assert "Send" not in p.invoked, "never sent"
    assert p.focused[-1] == "Message Body" and keys.done == ["ctrl+home"], "above the quote and the signature"
    assert done == ["reply all"] and draft == ""


def test_only_the_sender_gets_plain_reply(page):
    p = page()
    GmailWeb().open_reply(False, Keys())
    assert p.invoked == ["Reply"]


def test_an_open_reply_gets_its_empty_subject_from_the_title_set_not_typed(page):
    """10 Oct: a typed subject was cut off at "Re:" and its rest ran on into
    the body when the focus moved. Now it is set - no keystrokes at all."""
    p = page(reply_open=True, subject_field=True, subject="")
    keys = Keys()
    _draft, done = GmailWeb().open_reply(True, keys)
    assert p.invoked == [], "a reply was open: nothing pressed"
    assert p.subject.value == "Re: Sunflex Ops – additions made along the way"
    assert not any(isinstance(d, tuple) for d in keys.done), "not one key typed for the subject"
    assert p.timeline.index(("set", "Subject", p.subject.value)) < p.timeline.index(("focus", "Message Body"))
    assert done == ["subject"]


def test_a_lone_re_is_an_empty_subject(page):
    p = page(reply_open=True, subject_field=True, subject="Re:")
    GmailWeb().open_reply(True, Keys())
    assert p.subject.value == "Re: Sunflex Ops – additions made along the way"


def test_a_subject_that_cannot_be_set_is_typed_and_waited_for_before_the_body(page, monkeypatch):
    from ai import uia_page
    p = page(reply_open=True, subject_field=True, subject="Re:", settable=False)
    waited = []
    monkeypatch.setattr(uia_page, "wait", lambda found, seconds: waited.append(seconds) or found())
    keys = Keys()
    GmailWeb().open_reply(True, keys)
    assert keys.done[:2] == ["ctrl+a", ("typed", "Re: Sunflex Ops – additions made along the way")]
    assert waited, "the typed subject is waited for before the focus moves"
    assert p.focused == ["Subject", "Message Body"] and keys.done[-1] == "ctrl+home"


def test_a_subject_already_written_is_kept(page):
    page(reply_open=True, subject_field=True, subject="Meeting this week")
    keys = Keys()
    GmailWeb().open_reply(True, keys)
    assert not any(isinstance(d, tuple) for d in keys.done)


def test_no_reply_button_says_open_the_email(page):
    p = page()
    p.buttons.clear()
    with pytest.raises(MailError, match="open the email"):
        GmailWeb().open_reply(True, Keys())


def test_a_page_with_no_text_is_said_at_once_never_photographed(page):
    page(text="")
    with pytest.raises(MailError, match="can't read"):
        GmailWeb().read()


def test_the_page_is_read_with_the_users_address(page):
    page()
    mail = GmailWeb().read()
    assert mail.text == THREAD and mail.me == ME and mail.draft == ""


def test_a_reply_already_open_is_read_with_its_signature(page):
    p = page(reply_open=True)
    p.body.value = "-- \nTorben"
    assert GmailWeb().read().draft == "-- \nTorben"


# -- the voice tool -----------------------------------------------------------------------------------------------

class FakeGmail(email_reply.MailApp):
    name = "Gmail"

    def __init__(self, draft=""):
        self.draft, self.opened_with = draft, None

    def matches(self, title):
        return "Gmail" in title

    def read(self):
        return Mail(THREAD, ME, self.name)

    def open_reply(self, reply_all, keyboard):
        self.opened_with = reply_all
        return self.draft, ["reply all"]


class FakeKeyboard:
    def __init__(self):
        self.done = []

    def modifiers_down(self):
        return False

    def where(self):
        return ("hwnd", TITLE)

    def type(self, text):
        self.done.append(text)


def _manager(monkeypatch, app, title=TITLE, pieces=("Hi John,\n\n", "Yes - which day suits you?\n\n",
                                                   "Kind regards,\nTorben Mikkelsen\nAgentora")):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._activity = lambda what: None
    mgr._privacy_guard = False
    card = []
    mgr.sig_response_chunk = SimpleNamespace(emit=card.append)
    monkeypatch.setattr(cm, "active_window_title", lambda: title)
    monkeypatch.setattr(email_reply, "APPS", [app])
    keyboard = FakeKeyboard()
    monkeypatch.setattr(write_for_me, "Keyboard", lambda: keyboard)
    sent = {}

    async def stream_reply(request, mail, draft=""):
        sent.update(request=request, mail=mail, draft=draft)
        for p in pieces:
            yield p
    monkeypatch.setattr(email_reply, "stream_reply", stream_reply)
    return mgr, card, keyboard, sent


def test_the_voice_tool_types_the_reply_into_the_draft_and_never_sends(monkeypatch, caplog):
    app = FakeGmail()
    mgr, card, keyboard, sent = _manager(monkeypatch, app)
    with caplog.at_level(logging.INFO):
        out = asyncio.run(mgr._tool_reply_to_email({"request": "say yes but ask which day"}))
    assert out["ok"] and "NOT sent" in out["result"] and "press Send" in out["result"]
    assert app.opened_with is True, "reply all - the owner's choice"
    assert "".join(keyboard.done) == "Hi John,\n\nYes - which day suits you?\n\nKind regards,\nTorben Mikkelsen\nAgentora"
    assert card and card[0].startswith("Hi John,"), "the reply on the card"
    assert "email reply: Gmail, read the page" in caplog.text and "first words at" in caplog.text
    assert "Can we meet this week" not in caplog.text, "nothing of the email is kept"


def test_a_signature_in_the_box_gets_a_blank_line_above_it(monkeypatch):
    mgr, _card, keyboard, _sent = _manager(monkeypatch, FakeGmail(draft="-- \nTorben"))
    asyncio.run(mgr._tool_reply_to_email({"request": "reply"}))
    assert "".join(keyboard.done).endswith("Agentora\n\n")


def test_the_writing_starts_while_gmails_reply_opens(monkeypatch):
    import threading
    import time as _time

    class SlowGmail(FakeGmail):
        def open_reply(self, reply_all, keyboard):
            _time.sleep(0.3)                           # Gmail opening its reply box
            self.opened_at = _time.monotonic()
            return super().open_reply(reply_all, keyboard)
    app = SlowGmail()
    mgr, _card, keyboard, sent = _manager(monkeypatch, app)
    asked_at = []
    real = email_reply.stream_reply

    async def timed(request, mail, draft=""):
        asked_at.append(_time.monotonic())
        async for p in real(request, mail, draft):
            yield p
    monkeypatch.setattr(email_reply, "stream_reply", timed)
    out = asyncio.run(mgr._tool_reply_to_email({"request": "reply"}))
    assert out["ok"] and asked_at and asked_at[0] < app.opened_at, "written while the reply box opened"
    assert "".join(keyboard.done).startswith("Hi John,")
    assert threading.active_count() >= 1


def test_just_him_is_plain_reply(monkeypatch):
    app = FakeGmail()
    mgr, *_ = _manager(monkeypatch, app)
    asyncio.run(mgr._tool_reply_to_email({"request": "just reply to him saying thanks"}))
    assert app.opened_with is False


def test_not_gmail_says_so(monkeypatch):
    mgr, *_ = _manager(monkeypatch, FakeGmail(), title="Sent Items - Outlook")
    out = asyncio.run(mgr._tool_reply_to_email({"request": "reply to this"}))
    assert "Gmail" in out["error"]


def test_an_unreadable_email_is_said_at_once(monkeypatch):
    class Unreadable(FakeGmail):
        def read(self):
            raise MailError("I can't read this email's text - open it in Gmail and ask again.")
    mgr, _card, keyboard, _sent = _manager(monkeypatch, Unreadable())
    out = asyncio.run(mgr._tool_reply_to_email({"request": "reply to this"}))
    assert "can't read" in out["error"] and keyboard.done == []


def test_the_voice_knows_replying_is_its_own_mode():
    import config
    from ai import gemini_realtime
    decl = next(d for d in gemini_realtime.tool_declarations()[0]["functionDeclarations"]
                if d["name"] == "reply_to_email")
    assert "NEVER sends" in decl["description"]
    rules = " ".join(config._REALTIME_RULES.split())
    assert "REPLYING TO THE EMAIL ON SCREEN is its own mode" in rules and "It never sends" in rules
    assert "reply_to_email" in cm.CompanionManager._tool_handlers(cm.CompanionManager.__new__(cm.CompanionManager))
