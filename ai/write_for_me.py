"""
Tora writes into the box you are in.

What the user asked for (10 Oct 2026): talk about what you want written -
loosely, the way you would explain it to someone ("reply that Thursday
works but not Friday, keep it friendly", "put down my idea for the app:
...") - and Tora works out what you mean, writes it properly, and puts it
in whatever text box you are typing in: an email, a chat, a form, a
document, a prompt.

The same day: "it mustn't take screenshots and click - it needs to be
instant. HeyClicky's is so fast the person doesn't leave the page; this one
takes so long I leave the page and then it doesn't work." The first version
looked at a picture of the window, waited for the whole text and pasted it:
five seconds of nothing. Now:

  1. The box: what has the keyboard focus (Windows UI Automation) - its
     kind and name, the window, the text already in it. A password box is
     never written into, and with no box Tora says so rather than guess.
  2. The writing streams from the fastest model, no thinking. Its first
     line says what this is - TYPE, or DO: a command, which is never typed
     and goes back to the voice (the owner's spec: the two never mix).
     "Reply to this" reads the page's own text for what "this" is - never
     a screenshot.
  3. Into the box as it is written, a word at a time: the first words are
     there about a second after they stop talking. Typed as keystrokes,
     never Enter (a new line is Shift+Enter, which sends nothing). A code
     editor - whose closing brackets and indents would mangle typed text -
     gets one paste at the end instead, and the clipboard put back exactly.
     The moment they leave the window or the tab, typing stops; the rest
     waits on the clipboard.
"""

from __future__ import annotations

import ctypes
import logging
import re
import sys
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from ai import gemini
from ai.gemini import Models

_log = logging.getLogger("tora.write")

# Things that take the keyboard focus but are not for writing in.
_NOT_A_BOX = {
    "ButtonControl", "CheckBoxControl", "RadioButtonControl", "ListItemControl", "TreeItemControl",
    "MenuItemControl", "MenuBarControl", "MenuControl", "TabItemControl", "HyperlinkControl",
    "ImageControl", "ScrollBarControl", "SliderControl", "SplitButtonControl", "ToolBarControl",
    "TitleBarControl", "HeaderItemControl", "ListControl", "TreeControl", "TabControl",
    "ProgressBarControl", "DataItemControl", "SpinnerControl",
}
_CONTEXT_CHARS = 3000      # of the text already in the box, its end, for the writer
_PAGE_CHARS = 6000         # of the page, its end, when they say "reply to this"


@dataclass
class Box:
    """The text box with the keyboard focus."""
    kind: str               # "EditControl", "DocumentControl", ...
    name: str               # its accessible name: "Message", "Subject", "Search"
    window: str             # the window's title
    text: str = ""          # what is in it already, when Windows can say
    password: bool = False

    def same(self, other: Optional["Box"]) -> bool:
        return other is not None and (self.kind, self.name, self.window) == (other.kind, other.name, other.window)


def focused_box() -> Optional[Box]:
    """The box the user is typing in, or None when the focus is on nothing
    that takes text (a button, a list) or Windows cannot say."""
    try:
        import uiautomation as auto
        node = auto.GetFocusedControl()
        if node is None:
            return None
        kind = node.ControlTypeName or ""
        if kind in _NOT_A_BOX:
            return None
        password = bool(getattr(node, "IsPassword", False))
        text = ""
        try:
            value = node.GetPattern(auto.PatternId.ValuePattern)
            if value is not None:
                if value.IsReadOnly:
                    return None
                if not password:
                    text = str(value.Value or "")
        except Exception:  # noqa: BLE001 - no value to read: still a box
            pass
        try:
            top = node.GetTopLevelControl()
            window = (top.Name if top is not None else "") or ""
        except Exception:  # noqa: BLE001
            window = ""
        return Box(kind, (node.Name or "").strip()[:80], window.strip()[:120], text[-_CONTEXT_CHARS:], password)
    except Exception:  # noqa: BLE001 - no UI Automation: no box
        return None


# ── The writing ──────────────────────────────────────────────────────────────

_SYSTEM = """You write for the user: the text they want in the box they are
typing in. They talk loosely - thinking aloud, rambling, repeating
themselves, changing their mind mid-sentence, mixing what they want said
with how they want it said. Work out what they actually mean, then write
the finished text, well: clear, natural and in their voice, the filler
gone, the grammar right, the ideas in a sensible order - as long as it
needs to be and no longer.

FIRST decide what this is:
  • TYPE - words to write out: a message, an email, a reply, notes, a
    description, a prompt, or just talking an idea through. "Write",
    "type", "say", "tell them", "reply that" are TYPE.
  • DO - they want something DONE, not written: "open", "go to", "search
    for", "send", "run", "create a file", "remind me". An instruction to
    act is DO even with a text box open in front of them: "write an email
    saying I'll be late" is TYPE; "send an email saying I'll be late" is
    DO, though the email is half written. DO gets no text.
  • Only when it could truly be either - no instruction to act, no
    instruction to write - it is TYPE: a text box is focused.

For TYPE, the text:
  • Follow everything they asked: what to say, who it is for, the tone,
    length, format, language, names and details - word for word where they
    dictate exact words. Where they changed their mind, write what they
    settled on.
  • Something they want built or developed ("I want a feature that does X,
    but also Y") becomes a clear, well-organised description or request.
  • Fit the box: a chat message reads like a message; an email like an
    email (a greeting and sign-off only when it is the body of one); a
    search box gets a search; a form field just its value; a prompt or a
    plan for something they want built reads as clear, complete
    instructions; code is code.
  • The box may hold text already: write what comes next - never repeat it.
  • The page, when you get it, is what is on their screen - what "this",
    "it" and "reply to that" mean. Use it; never describe it.
  • Never invent a fact they did not give - a date, a price, a name: put a
    short [placeholder] in its place.

Answer in exactly this form. The first line is TYPE, or DO: and in a few
words what they want done. For TYPE the text for the box follows from the
second line - only the text: no preamble, no quotes round it, no notes
after it, and no markdown unless the box is a place for it."""

WRITE_TIMEOUT_S = 20.0


@dataclass
class Written:
    """What the writer made of it: text to type, or a command to hand on."""
    kind: str = "dictation"
    text: str = ""
    command: str = ""

    def __bool__(self) -> bool:
        return bool(self.text) or self.kind == "command"


_ON_SCREEN_RE = re.compile(r"\b(this|that|these|those|reply|respond|answer|above)\b", re.IGNORECASE)


def leans_on_screen(request: str) -> bool:
    """"Reply to this", "answer that": the words mean what is on screen."""
    return bool(_ON_SCREEN_RE.search(request or ""))


def page_context() -> str:
    """The end of the page in front, read as text - what "this" means."""
    try:
        from ai import uia_page
        with uia_page.in_thread():
            return uia_page.page_text(uia_page.window())[-_PAGE_CHARS:]
    except Exception:  # noqa: BLE001 - the words alone still write it
        return ""


def _ask(request: str, box: Box, page: str) -> str:
    where = f'A {box.kind.replace("Control", "").lower() or "text"} box'
    if box.name:
        where += f' named "{box.name}"'
    if box.window:
        where += f' in the window "{box.window}"'
    ask = f"{where}.\n"
    if box.text.strip():
        ask += f"It already holds (its end):\n<<<\n{box.text}\n>>>\n"
    if page:
        ask += f"\nThe page on their screen (its end):\n<<<\n{page}\n>>>\n"
    return ask + f"\nWhat they said they want written:\n{request.strip()}"


async def stream_dictation(request: str, box: Box, page: str = "") -> AsyncIterator[str]:
    """What the writer makes of it, as it is written: first "TYPE" or "DO:
    <what>", then - for TYPE - the text, piece by piece. Nothing at all if
    the call failed."""
    head, decided = "", False
    try:
        async for piece in gemini.stream(
                Models.WRITER, [{"role": "user", "parts": [gemini.text_part(_ask(request, box, page))]}],
                system=_SYSTEM, max_tokens=2048, temperature=0.6, think=0, timeout=WRITE_TIMEOUT_S):
            if decided:
                yield piece
                continue
            head += piece
            if "\n" not in head and len(head) < 120:
                continue
            decided = True
            for item in _split_head(head):
                yield item
    except Exception as e:  # noqa: BLE001 - say so; never a half-guessed box
        _log.warning("writing failed: %s", str(e)[:200])
        return
    if not decided and head.strip():
        for item in _split_head(head):
            yield item


def _split_head(head: str) -> list[str]:
    """The first line's verdict, and whatever text came with it. A writer
    that skipped the verdict and went straight to the text is writing it."""
    first, _, rest = head.partition("\n")
    verdict = first.strip().strip("*`").strip()
    # The verdict in capitals, sometimes with a label after it ("TYPE:
    # WhatsApp message", seen live) - never the text's own "Type the report".
    if re.fullmatch(r"TYPE\b(\s*[:\-–].*)?", verdict):
        return ["TYPE"] + ([rest.lstrip("\n")] if rest.strip() else [])
    done = re.fullmatch(r"DO\b\s*[:\-–]?\s*(.*)", verdict)
    if done:
        return ["DO: " + done.group(1).strip()]
    return ["TYPE", head]


# ── Into the box ─────────────────────────────────────────────────────────────

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


_UNICODE, _KEYUP = 0x4, 0x2
_SHIFT, _ENTER = 0x10, 0x0D


def _keystrokes(text: str) -> list:
    """`text` as key presses: each character as unicode, a new line as
    Shift+Enter - Enter would send a chat message."""
    events = []
    for ch in text:
        if ch == "\r":
            continue
        if ch == "\n":
            events += [(_SHIFT, 0, 0), (_ENTER, 0, 0), (_ENTER, 0, _KEYUP), (_SHIFT, 0, _KEYUP)]
            continue
        code = ord(ch)
        units = [code] if code < 0x10000 else [0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
        events += [(0, u, _UNICODE) for u in units] + [(0, u, _UNICODE | _KEYUP) for u in units]
    return events


class Keyboard:
    """What putting text in needs from Windows. Tests use a fake."""

    def modifiers_down(self) -> bool:
        user32 = ctypes.windll.user32
        return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def where(self) -> tuple:
        """The window in front and its title - which changes with the tab.
        A title's "edited" mark (Notepad's *, VS Code's ●) is not leaving."""
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        return hwnd, buf.value.lstrip("*● ").strip()

    def paste(self) -> None:
        self._ctrl(0x56)                                                 # Ctrl+V

    def to_start(self) -> None:
        self._ctrl(0x24)                                                 # Ctrl+Home: the top of the box

    def select_all(self) -> None:
        self._ctrl(0x41)                                                 # Ctrl+A: in a one-line box, its text

    def _ctrl(self, vk_key: int) -> None:
        user32 = ctypes.windll.user32
        for vk, up in ((0x11, 0), (vk_key, 0), (vk_key, 2), (0x11, 2)):  # ctrl down, key down, key up, ctrl up
            user32.keybd_event(vk, 0, up, 0)
            time.sleep(0.01)

    def type(self, text: str) -> None:
        """Typed in one go: every key of it in a single SendInput."""
        events = _keystrokes(text)
        for i in range(0, len(events), 400):
            batch = events[i:i + 400]
            arr = (_INPUT * len(batch))()
            for slot, (vk, scan, flags) in zip(arr, batch):
                slot.type = 1
                slot.ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
            ctypes.windll.user32.SendInput(len(batch), arr, ctypes.sizeof(_INPUT))


class Clipboard:
    """The Windows clipboard, every format on it, so it can be put back
    exactly: text, a picture, copied files. Tests use a fake."""

    _HANDLES = {2, 3, 9, 14}      # bitmap, metafile, palette, enhanced metafile: GDI handles, not memory
    CF_UNICODETEXT = 13

    def __init__(self) -> None:
        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
        for f in (k32.GlobalLock, u32.GetClipboardData, u32.SetClipboardData, k32.GlobalAlloc,
                  u32.CreateWindowExW):
            f.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [ctypes.c_void_p]
        k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        k32.GlobalSize.argtypes = [ctypes.c_void_p]
        k32.GlobalSize.restype = ctypes.c_size_t
        u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        u32.GetClipboardData.argtypes = [ctypes.c_uint]
        u32.OpenClipboard.argtypes = [ctypes.c_void_p]
        u32.DestroyWindow.argtypes = [ctypes.c_void_p]
        u32.CreateWindowExW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        self._k32, self._u32 = k32, u32
        self._hwnd = None

    def _open(self) -> bool:
        # A window must own the clipboard: opened with none, EmptyClipboard
        # leaves it ownerless and SetClipboardData then fails. An invisible
        # one, made and destroyed on this thread for each use.
        self._hwnd = self._u32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
        for _ in range(20):                       # another app may hold it a moment
            if self._u32.OpenClipboard(self._hwnd):
                return True
            time.sleep(0.02)
        self._close_window()
        return False

    def _close(self) -> None:
        self._u32.CloseClipboard()
        self._close_window()

    def _close_window(self) -> None:
        if self._hwnd:
            self._u32.DestroyWindow(self._hwnd)
            self._hwnd = None

    def save(self) -> Optional[list[tuple[int, bytes]]]:
        if not self._open():
            return None
        try:
            out, fmt = [], 0
            while True:
                fmt = self._u32.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                if fmt in self._HANDLES:
                    continue
                h = self._u32.GetClipboardData(fmt)
                if not h:
                    continue
                size = self._k32.GlobalSize(h)
                p = self._k32.GlobalLock(h)
                if not p:
                    continue
                try:
                    out.append((fmt, ctypes.string_at(p, size)))
                finally:
                    self._k32.GlobalUnlock(h)
            return out
        finally:
            self._close()

    def _put(self, items: list[tuple[int, bytes]]) -> bool:
        if not self._open():
            return False
        try:
            self._u32.EmptyClipboard()
            for fmt, data in items:
                h = self._k32.GlobalAlloc(0x0002, max(1, len(data)))       # GMEM_MOVEABLE
                p = self._k32.GlobalLock(h)
                ctypes.memmove(p, data, len(data))
                self._k32.GlobalUnlock(h)
                self._u32.SetClipboardData(fmt, h)
            return True
        finally:
            self._close()

    def set_text(self, text: str) -> bool:
        # Kept out of Windows' clipboard history and cloud sync: it is
        # Tora's way in, not something the user copied.
        private = self._u32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
        return self._put([(self.CF_UNICODETEXT, (text + "\0").encode("utf-16-le")), (private, b"\0")])

    def restore(self, items: Optional[list[tuple[int, bytes]]]) -> None:
        self._put(items or [])


PASTE_SETTLE_S = 0.4       # the app reads the clipboard after Ctrl+V arrives, not at once


def keys_let_go(keyboard: Keyboard, seconds: float = 3.0) -> None:
    """Wait for the talk shortcut to be let go: a key typed with Win still
    held is a Windows shortcut."""
    deadline = time.monotonic() + seconds
    while keyboard.modifiers_down() and time.monotonic() < deadline:
        time.sleep(0.05)


def _clipboard(clipboard):
    if clipboard is None and sys.platform == "win32":
        return Clipboard()
    return clipboard


def paste(text: str, *, keyboard: Optional[Keyboard] = None, clipboard=None) -> str:
    """`text` into whatever has the focus now: "pasted" (the clipboard put
    back after), or "typed" when the clipboard cannot be had."""
    keyboard = keyboard or Keyboard()
    clipboard = _clipboard(clipboard)
    saved = clipboard.save() if clipboard is not None else None
    if saved is not None and clipboard.set_text(text):
        keyboard.paste()
        time.sleep(PASTE_SETTLE_S)
        clipboard.restore(saved)
        return "pasted"
    keyboard.type(text)
    return "typed"


# Code editors close brackets and indent as you type: typed text comes out
# mangled there, so it is pasted in one go at the end.
_PASTE_APPS = re.compile(r"Visual Studio Code|\bCursor\b|IntelliJ|PyCharm|WebStorm|Rider|Android Studio|"
                         r"Notepad\+\+|Sublime Text|\bZed\b|Visual Studio\b", re.IGNORECASE)


def pastes(box: Box) -> bool:
    """Does this box take the text in one paste rather than typed?"""
    return bool(_PASTE_APPS.search(box.window or ""))


class Typist:
    """Types text into the box in front as it is written - a word at a time,
    so half a word never sits there - and stops the moment the user leaves
    the window or the tab: the rest then waits on the clipboard."""

    def __init__(self, keyboard: Optional[Keyboard] = None, clipboard=None, *, paste_at_end: bool = False):
        self.keyboard = keyboard or Keyboard()
        self._clipboard = clipboard
        self.paste_at_end = paste_at_end
        self.here = self.keyboard.where()
        self.text = ""             # everything written
        self.typed = 0             # how much of it is in the box
        self.left = False

    def feed(self, piece: str) -> None:
        self.text += piece
        if self.paste_at_end or self.left:
            return
        cut = max(self.text.rfind(" "), self.text.rfind("\n")) + 1
        if cut > self.typed:
            self._type(cut)

    def _type(self, upto: int) -> None:
        if self.keyboard.where() != self.here:
            self.left = True
            return
        self.keyboard.type(self.text[self.typed:upto])
        self.typed = upto

    def finish(self) -> str:
        """The rest in. "typed", "pasted", or "clipboard" (they left - the
        rest is on it); "" for nothing written."""
        self.text = self.text.rstrip(" ")
        if not self.text.strip():
            return ""
        if self.paste_at_end and not self.left:
            if self.keyboard.where() == self.here:
                self.typed = len(self.text)
                return paste(self.text, keyboard=self.keyboard, clipboard=self._clipboard)
            self.left = True
        if not self.left:
            self._type(len(self.text))
        if self.left:
            clipboard = _clipboard(self._clipboard)
            if clipboard is not None:
                clipboard.set_text(self.text[self.typed:].lstrip())
            return "clipboard"
        return "typed"
