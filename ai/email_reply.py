"""
Reply to the email on screen.

Asked for on 10 Oct 2026: "look at this email and write a reply", "reply
saying I'll do it Monday". Tora reads the email the user is looking at,
writes a reply that answers it - in its tone, signed off the way they sign
off - and fills in the reply form: who it goes to, the subject, and the body
above the quoted original. It never sends: they read it and press Send.

  1. Read: the page's own text through Windows UI Automation - the whole
     thread, the senders' addresses, the subject, even what is scrolled out
     of view. A page that shows no text is read from a picture of the window
     instead (the owner's choice: page text, screenshot as the backup).
  2. Write: one call to the writer model - what the email says and asks,
     the user's instructions word for word, nothing invented ([confirm
     date] where a fact is missing), their own sign-off from the thread.
  3. Fill: with no reply open, the app's own Reply - or Reply all when
     others were on it - so the app fills the addresses itself; a reply the
     user opened keeps the recipients they chose. Then only what is still
     empty: the Subject, and the body, pasted at its top.

Each email app is a MailApp: how to know it, read it, and fill a reply.
Gmail in the browser first; Outlook goes in the same shape. Nothing of the
email is kept: its text goes to the writer and is dropped.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ai import gemini, write_for_me
from ai.gemini import Models

_log = logging.getLogger("tora.email")

MAX_TEXT = 30000           # characters of the page: a long thread, not a whole mailbox
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


class MailError(RuntimeError):
    """Something about the email app that Tora tells the user."""


@dataclass
class Mail:
    """The email on screen, as read - kept only for the one reply."""
    text: str = ""          # the page's text: the thread, its senders, the subject
    me: str = ""            # the user's own address, when the window says it
    app: str = ""


@dataclass
class Reply:
    to: list = field(default_factory=list)
    cc: list = field(default_factory=list)
    subject: str = ""
    body: str = ""
    reply_all: bool = False

    def __bool__(self) -> bool:
        return bool(self.body.strip())


class MailApp:
    """An email app Tora can reply in. Gmail in the browser now; Outlook
    later, in the same shape."""
    name = ""

    def matches(self, title: str) -> bool:
        return False

    def read(self) -> Mail:
        raise NotImplementedError

    def draft_text(self) -> Optional[str]:
        """What an open reply's body holds already (a signature), or None
        when no reply is open."""
        raise NotImplementedError

    def fill(self, reply: Reply, keyboard=None, clipboard=None) -> list[str]:
        """Open a reply if none is, fill what is empty. Returns what was
        filled. Never sends."""
        raise NotImplementedError


# ── Windows UI Automation, in small pieces a test can stand in for ───────────

def _ui():
    from uiautomation import uiautomation as _ua
    return _ua._AutomationClient.instance().IUIAutomation


def _window():
    import ctypes
    import uiautomation as auto
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return auto.ControlFromHandle(hwnd) if hwnd else None


def _find_all(root, *, name: Optional[str] = None, control_type: Optional[int] = None, limit: int = 40) -> list:
    """Windows' own search - milliseconds on a page of thousands of nodes,
    where walking it from Python takes seconds."""
    import uiautomation as auto
    if root is None:
        return []
    ui = _ui()
    conds = []
    if name is not None:
        conds.append(ui.CreatePropertyCondition(auto.PropertyId.NameProperty, name))
    if control_type is not None:
        conds.append(ui.CreatePropertyCondition(auto.PropertyId.ControlTypeProperty, control_type))
    cond = (conds[0] if len(conds) == 1 else ui.CreateAndCondition(conds[0], conds[1]) if conds
            else ui.CreateTrueCondition())
    found = root.Element.FindAll(4, cond)                     # TreeScope_Descendants
    n = found.Length if found else 0
    return [auto.Control.CreateControlFromElement(found.GetElement(i)) for i in range(min(n, limit))]


def _page_text(window) -> str:
    """The text of the web page in `window`."""
    import uiautomation as auto
    for doc in _find_all(window, control_type=auto.ControlType.DocumentControl, limit=4):
        try:
            text = doc.GetTextPattern().DocumentRange.GetText(MAX_TEXT * 2)
        except Exception:  # noqa: BLE001 - no text pattern on this one
            continue
        if text and len(text.strip()) > 200:
            return text
    return ""


def _value(control) -> str:
    try:
        return str(control.GetValuePattern().Value or "")
    except Exception:  # noqa: BLE001
        return ""


def _shown(control) -> bool:
    try:
        return not control.IsOffscreen
    except Exception:  # noqa: BLE001
        return True


def _focus(control) -> None:
    control.SetFocus()
    time.sleep(0.08)


def _invoke(control) -> None:
    control.GetInvokePattern().Invoke()


def _wait(found: Callable, seconds: float):
    deadline = time.monotonic() + seconds
    while True:
        got = found()
        if got is not None or time.monotonic() > deadline:
            return got
        time.sleep(0.1)


class GmailWeb(MailApp):
    """Gmail in a Chromium browser (Opera, Chrome, Edge). Its page is in the
    accessibility tree, and its controls carry Gmail's own labels."""
    name = "Gmail"
    BODY, SUBJECT, TO = "Message Body", "Subject", "To recipients"
    REPLY, REPLY_ALL = "Reply", "Reply all"

    def matches(self, title: str) -> bool:
        return bool(re.search(r"\bGmail\b", title or ""))

    def read(self) -> Mail:
        window = _window()
        if window is None:
            raise MailError("no window in front")
        me = _EMAIL_RE.search(window.Name or "")
        text = _page_text(window)
        return Mail(_tidy(text)[:MAX_TEXT], me.group(0) if me else "", self.name)

    def _last(self, window, name: str):
        shown = [c for c in _find_all(window, name=name) if _shown(c)]
        return shown[-1] if shown else None              # the newest reply on the page

    def draft_text(self) -> Optional[str]:
        body = self._last(_window(), self.BODY)
        return None if body is None else _value(body)

    def fill(self, reply: Reply, keyboard=None, clipboard=None) -> list[str]:
        """Open a reply if none is, fill what is empty. Returns what was
        filled. Never sends."""
        raise NotImplementedError


# ── Windows UI Automation, in small pieces a test can stand in for ───────────

def _ui():
    from uiautomation import uiautomation as _ua
    return _ua._AutomationClient.instance().IUIAutomation


def _window():
    import ctypes
    import uiautomation as auto
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return auto.ControlFromHandle(hwnd) if hwnd else None


def _find_all(root, *, name: Optional[str] = None, control_type: Optional[int] = None, limit: int = 40) -> list:
    """Windows' own search - milliseconds on a page of thousands of nodes,
    where walking it from Python takes seconds."""
    import uiautomation as auto
    if root is None:
        return []
    ui = _ui()
    conds = []
    if name is not None:
        conds.append(ui.CreatePropertyCondition(auto.PropertyId.NameProperty, name))
    if control_type is not None:
        conds.append(ui.CreatePropertyCondition(auto.PropertyId.ControlTypeProperty, control_type))
    cond = (conds[0] if len(conds) == 1 else ui.CreateAndCondition(conds[0], conds[1]) if conds
            else ui.CreateTrueCondition())
    found = root.Element.FindAll(4, cond)                     # TreeScope_Descendants
    n = found.Length if found else 0
    return [auto.Control.CreateControlFromElement(found.GetElement(i)) for i in range(min(n, limit))]


def _page_text(window) -> str:
    """The text of the web page in `window`."""
    import uiautomation as auto
    for doc in _find_all(window, control_type=auto.ControlType.DocumentControl, limit=4):
        try:
            text = doc.GetTextPattern().DocumentRange.GetText(MAX_TEXT * 2)
        except Exception:  # noqa: BLE001 - no text pattern on this one
            continue
        if text and len(text.strip()) > 200:
            return text
    return ""


def _value(control) -> str:
    try:
        return str(control.GetValuePattern().Value or "")
    except Exception:  # noqa: BLE001
        return ""


def _shown(control) -> bool:
    try:
        return not control.IsOffscreen
    except Exception:  # noqa: BLE001
        return True


def _focus(control) -> None:
    control.SetFocus()
    time.sleep(0.08)


def _invoke(control) -> None:
    control.GetInvokePattern().Invoke()


def _wait(found: Callable, seconds: float):
    deadline = time.monotonic() + seconds
    while True:
        got = found()
        if got is not None or time.monotonic() > deadline:
            return got
        time.sleep(0.1)


class GmailWeb(MailApp):
    """Gmail in a Chromium browser (Opera, Chrome, Edge). Its page is in the
    accessibility tree, and its controls carry Gmail's own labels."""
    name = "Gmail"
    BODY, SUBJECT, TO = "Message Body", "Subject", "To recipients"
    REPLY, REPLY_ALL = "Reply", "Reply all"

    def matches(self, title: str) -> bool:
        return bool(re.search(r"\bGmail\b", title or ""))

    def read(self) -> Mail:
        window = _window()
        if window is None:
            raise MailError("no window in front")
        me = _EMAIL_RE.search(window.Name or "")
        text = _page_text(window)
        return Mail(_tidy(text)[:MAX_TEXT], me.group(0) if me else "", self.name)

    def _last(self, window, name: str):
        shown = [c for c in _find_all(window, name=name) if _shown(c)]
        return shown[-1] if shown else None              # the newest reply on the page

    def draft_text(self) -> Optional[str]:
        body = self._last(_window(), self.BODY)
        return None if body is None else _value(body)

    def _has_recipients(self, to) -> bool:
        """A recipient already on the reply: Gmail shows each as a chip
        beside the To box, named with its address or with "Remove"."""
        parent = to.GetParentControl()
        for _ in range(2):
            if parent is not None and parent.GetParentControl() is not None:
                parent = parent.GetParentControl()
        for c in _find_all(parent, limit=60):
            name = c.Name or ""
            if "@" in name or name.startswith("Remove"):
                return True
        return bool(_value(to).strip())

    def fill(self, reply: Reply, keyboard=None, clipboard=None) -> list[str]:
        keyboard = keyboard or write_for_me.Keyboard()
        write_for_me.keys_let_go(keyboard)
        window = _window()
        filled = []
        body = self._last(window, self.BODY)
        if body is None:
            # Gmail's own Reply fills the addresses - and Reply all the Cc.
            button = (reply.reply_all and self._last(window, self.REPLY_ALL)) or self._last(window, self.REPLY)
            if not button:
                raise MailError("I can't see a Reply button - open the email first.")
            _invoke(button)
            filled.append("reply all" if button.Name == self.REPLY_ALL else "reply")
            body = _wait(lambda: self._last(window, self.BODY), 4.0)
            if body is None:
                raise MailError("the reply did not open")
        # To is Gmail's own: its Reply fills it, and a reply the user opened
        # keeps the recipients they chose. Pasting addresses beside chips that
        # could not be seen for certain would put John on it twice.
        subject = self._last(window, self.SUBJECT)
        if subject is not None and reply.subject and not _value(subject).strip():
            _focus(subject)
            write_for_me.paste(reply.subject, keyboard=keyboard, clipboard=clipboard)
            filled.append("subject")
        _focus(body)
        keyboard.to_start()                               # above the quoted original and the signature
        write_for_me.paste(reply.body.strip() + "\n\n", keyboard=keyboard, clipboard=clipboard)
        filled.append("body")
        return filled


APPS: list[MailApp] = [GmailWeb()]


def app_for(title: str) -> Optional[MailApp]:
    """The email app in front, by its window's title - None for one Tora
    cannot reply in yet."""
    return next((a for a in APPS if a.matches(title)), None)


def _tidy(text: str) -> str:
    """The page's text with its blank runs closed up."""
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text or "")).strip()


# ── The writing ──────────────────────────────────────────────────────────────

_SYSTEM = """You write the user's reply to the email they are reading.

You get the email app's page (its text, or a picture of it): the thread,
oldest first, with who sent each message. The newest message that is NOT
from the user is the one to answer. The user is {me}.

  • Work out who it is from, what it is about, what is being asked, and
    what a good reply has to cover. The user's instructions come first and
    are followed exactly ("say yes but ask about pricing").
  • Match the email's tone - formal or casual - and its language.
  • Use only what is in the email and in their instructions. Never invent a
    fact, date, price or promise: write a clear placeholder in its place,
    like [confirm date].
  • Sign off the way the user signs off their own emails in the thread,
    word for word (e.g. "Kind regards,\\nTorben Mikkelsen\\nAgentora"). With
    none of theirs to copy, "Kind regards," and their first name. When the
    reply box already holds their signature, no sign-off at all.
  • to: the address of whoever sent the message being answered. cc and
    reply_all: everyone else who was on it, except the user, when there
    were others and the user did not ask to answer only the sender.
  • subject: "Re: " and the original subject, never "Re: Re:".
  • body: the reply alone - no subject line, no quoted original, no
    markdown, no notes to the user."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "to": {"type": "array", "items": {"type": "string"}},
        "cc": {"type": "array", "items": {"type": "string"}},
        "reply_all": {"type": "boolean"},
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["to", "subject", "body"],
    "propertyOrdering": ["to", "cc", "reply_all", "subject", "body"],
}

WRITE_TIMEOUT_S = 25.0


async def compose_reply(request: str, mail: Mail, picture_b64: str = "", draft: Optional[str] = None) -> Reply:
    """The reply, from the email and what the user said. Empty if it failed."""
    ask = ""
    if mail.text:
        ask += f"The email app's page ({mail.app or 'email'}):\n<<<\n{mail.text}\n>>>\n\n"
    if draft and draft.strip():
        ask += f"The reply box already holds:\n<<<\n{draft.strip()[:1500]}\n>>>\n\n"
    ask += f"What the user said:\n{request.strip()}"
    parts = ([gemini.image_part(picture_b64)] if picture_b64 and not mail.text else []) + [gemini.text_part(ask)]
    try:
        data = await gemini.generate_json(
            Models.WRITER, [{"role": "user", "parts": parts}], _SCHEMA,
            system=_SYSTEM.replace("{me}", mail.me or "the account's owner"),
            max_tokens=4096, think="low", media_resolution="high", timeout=WRITE_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - say so; never a half-filled reply
        _log.warning("writing the reply failed: %s", str(e)[:200])
        return Reply()
    if not isinstance(data, dict):
        return Reply()
    addresses = lambda key: [a for a in (data.get(key) or []) if _EMAIL_RE.fullmatch(str(a).strip())]  # noqa: E731
    subject = str(data.get("subject") or "").strip()
    subject = re.sub(r"^(re:\s*)+", "Re: ", subject, flags=re.IGNORECASE)
    return Reply(addresses("to"), [a for a in addresses("cc") if a.lower() != mail.me.lower()], subject,
                 write_for_me._clean(str(data.get("body") or "")), bool(data.get("reply_all")))
