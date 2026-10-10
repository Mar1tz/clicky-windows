"""
Reply to the email on screen.

Asked for on 10 Oct 2026: "look at this email and write a reply", "reply
saying I'll do it Monday". Tora reads the email the user is looking at,
writes a reply that answers it - in its tone, signed off the way they sign
off - and fills in the reply form: who it goes to, the subject, and the
body above the quoted original. It never sends: they read it and press Send.

The same evening: "it mustn't take screenshots and click - it needs to be
instant", and the first version made them wait five seconds for a reply
written whole. Now, all at once:

  * Read: the page's own text, through Windows UI Automation (ai/uia_page)
    - the whole thread and its senders, even what is scrolled away. Never a
    screenshot: a page that shows no text is said so, at once.
  * Open: with no reply open, Gmail's own Reply - or Reply all when others
    were on it - pressed through Windows, no mouse. Gmail fills the
    addresses itself, and the subject is the window's title; a reply the
    user opened keeps the recipients they chose. This runs while the page
    is read, so the reply box is open by the time the writing starts.
  * Write: the fastest model streams the body - what the email asks, their
    instructions word for word, nothing invented ([confirm date] where a
    fact is missing), their own sign-off from the thread - and it is typed
    into the body as it comes, above the quote and the signature.

Each email app is a MailApp: how to know it, read it, and open a reply.
Gmail in the browser first; Outlook goes in the same shape. Nothing of the
email is kept: its text goes to the writer and is dropped.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from ai import gemini, uia_page, write_for_me
from ai.gemini import Models

_log = logging.getLogger("tora.email")

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


class MailError(RuntimeError):
    """Something about the email app that Tora tells the user."""


@dataclass
class Mail:
    """The email on screen, as read - kept only for the one reply."""
    text: str = ""          # the page's text: the thread, its senders, the subject
    me: str = ""            # the user's own address, when the window says it
    app: str = ""
    draft: str = ""         # what a reply already open held (a signature): the writer leaves the sign-off


class MailApp:
    """An email app Tora can reply in. Gmail in the browser now; Outlook
    later, in the same shape."""
    name = ""

    def matches(self, title: str) -> bool:
        return False

    def read(self) -> Mail:
        """The email's text, from the page. Raises MailError when it shows none."""
        raise NotImplementedError

    def open_reply(self, reply_all: bool, keyboard) -> tuple[str, list[str]]:
        """A reply ready to type into: opened if none is, its empty subject
        filled, the caret at the top of its body. Returns what the body
        held already (a signature) and what was done. Never sends."""
        raise NotImplementedError


_ONLY_SENDER_RE = re.compile(r"\b(only|just)\b[^.?!]*\b(him|her|them|sender|back)\b|\bnot (to )?(everyone|all)\b",
                             re.IGNORECASE)


def reply_all_wanted(request: str) -> bool:
    """Reply all - the owner's choice when others were on it - unless they
    said to answer only the sender."""
    return not _ONLY_SENDER_RE.search(request or "")


class GmailWeb(MailApp):
    """Gmail in a Chromium browser (Opera, Chrome, Edge). Its page is in the
    accessibility tree, and its controls carry Gmail's own labels."""
    name = "Gmail"
    BODY, SUBJECT = "Message Body", "Subject"
    REPLY, REPLY_ALL = "Reply", "Reply all"

    def matches(self, title: str) -> bool:
        return bool(re.search(r"\bGmail\b", title or ""))

    @staticmethod
    def subject_of(title: str) -> str:
        """The email's subject, from the window's title: "<subject> -
        <address> - Gmail - <browser>"."""
        subject = re.sub(r"\s+-\s+[\w.+-]+@[\w.-]+\s+-\s+Gmail\b.*$", "", title or "").strip()
        return "" if subject == (title or "").strip() else subject

    def read(self) -> Mail:
        with uia_page.in_thread():
            win = uia_page.window()
            if win is None:
                raise MailError("no window in front")
            me = _EMAIL_RE.search(win.Name or "")
            text = uia_page.page_text(win)
            open_body = self._last(win, self.BODY)
            draft = uia_page.value(open_body) if open_body is not None else ""
        if not text:
            raise MailError("I can't read this email's text - open it in Gmail and ask again.")
        return Mail(text, me.group(0) if me else "", self.name, draft)

    def _last(self, win, name: str):
        shown = [c for c in uia_page.find_all(win, name=name) if uia_page.shown(c)]
        return shown[-1] if shown else None              # the newest reply on the page

    def open_reply(self, reply_all: bool, keyboard) -> tuple[str, list[str]]:
        done: list[str] = []
        with uia_page.in_thread():
            win = uia_page.window()
            title = (win.Name if win is not None else "") or ""
            body = self._last(win, self.BODY)
            if body is None:
                # Gmail's own Reply fills the addresses - and Reply all the Cc.
                button = (reply_all and self._last(win, self.REPLY_ALL)) or self._last(win, self.REPLY)
                if not button:
                    raise MailError("I can't see a Reply button - open the email first.")
                uia_page.invoke(button)
                done.append("reply all" if button.Name == self.REPLY_ALL else "reply")
                body = uia_page.wait(lambda: self._last(win, self.BODY), 3.0)
                if body is None:
                    raise MailError("the reply did not open")
            draft = uia_page.value(body)
            subject_box = self._last(win, self.SUBJECT)       # a pop-out reply shows one; inline does not
            write_for_me.keys_let_go(keyboard)
            subject = self.subject_of(title)
            if subject_box is not None and subject and _empty_subject(uia_page.value(subject_box)):
                wanted = subject if re.match(r"re:", subject, re.IGNORECASE) else "Re: " + subject
                # Set, not typed: on 10 Oct a typed subject was cut off at "Re:"
                # and its rest ran on into the body when the focus moved on.
                if not uia_page.set_value(subject_box, wanted):
                    uia_page.focus(subject_box)
                    if uia_page.value(subject_box).strip():
                        keyboard.select_all()                 # its lone "Re:", replaced whole
                    keyboard.type(wanted)
                    uia_page.wait(lambda: True if uia_page.value(subject_box).strip() == wanted else None, 2.0)
                done.append("subject")
            uia_page.focus(body)
            keyboard.to_start()                               # above the quoted original and the signature
        return draft, done


def _empty_subject(text: str) -> bool:
    """No subject yet - or only a "Re:" with nothing after it."""
    return (text or "").strip().lower() in ("", "re:", "re")


APPS: list[MailApp] = [GmailWeb()]


def app_for(title: str) -> Optional[MailApp]:
    """The email app in front, by its window's title - None for one Tora
    cannot reply in yet."""
    return next((a for a in APPS if a.matches(title)), None)


# ── The writing ──────────────────────────────────────────────────────────────

_SYSTEM = """You write the user's reply to the email they are reading.

You get the email app's page as text: the thread, oldest first, with who
sent each message. The newest message that is NOT from the user is the one
to answer. The user is {me}.

  • Work out who it is from, what it is about, what is being asked, and
    what a good reply has to cover. The user's instructions come first and
    are followed exactly ("say yes but ask about pricing").
  • Match the email's tone - formal or casual - and its language.
  • Use only what is in the email and in their instructions. Never invent a
    fact, date, price or promise: write a clear placeholder in its place,
    like [confirm date].
  • Sign off the way the user signs off their own emails in the thread,
    word for word, on lines of their own. With none of theirs to copy,
    "Kind regards," and their first name. When the reply box already holds
    their signature, no sign-off at all.
  • Write ONLY the body of the reply, starting with its greeting: no
    subject line, no "To:", no quoted original, no markdown, no notes to
    the user."""

WRITE_TIMEOUT_S = 20.0


async def stream_reply(request: str, mail: Mail, draft: str = "") -> AsyncIterator[str]:
    """The reply's body, as it is written. Nothing if the call failed."""
    ask = f"The email app's page ({mail.app or 'email'}):\n<<<\n{mail.text}\n>>>\n\n"
    if draft and draft.strip():
        ask += f"The reply box already holds:\n<<<\n{draft.strip()[:1500]}\n>>>\n\n"
    ask += f"What the user said:\n{request.strip()}"
    started = False
    try:
        async for piece in gemini.stream(
                Models.WRITER, [{"role": "user", "parts": [gemini.text_part(ask)]}],
                system=_SYSTEM.replace("{me}", mail.me or "the account's owner"),
                max_tokens=2048, temperature=0.6, think=0, timeout=WRITE_TIMEOUT_S):
            if not started:
                piece = piece.lstrip()
                started = bool(piece)
            if piece:
                yield piece
    except Exception as e:  # noqa: BLE001 - say so; what was typed stays
        _log.warning("writing the reply failed: %s", str(e)[:200])
