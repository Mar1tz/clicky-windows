"""
Tora writes into the box you are in.

What the user asked for (10 Oct 2026): talk about what you want written -
loosely, the way you would explain it to someone ("reply that Thursday
works but not Friday, keep it friendly", "put down my idea for the app:
...") - and Tora works out what you mean, writes it properly, and puts it
in whatever text box you are typing in: an email, a chat, a form, a
document, a prompt.

  1. The box: what has the keyboard focus (Windows UI Automation) - its
     kind and name, the window, the text already in it. A password box is
     never written into, and with no box Tora says so rather than guess.
  2. Dictation or command, then the writing: one call to the writer model
     with the user's own words, the box, the text already there, and a
     picture of the window - "reply to this" means what is on screen. It
     first says which this is: text to type, or something to DO ("send the
     email", "open Spotify"). A command is never typed: it goes back to the
     voice, whose app actions and agents ask the user before anything runs.
     The live voice already made that call when it chose this tool; this is
     the second look, so the two modes never mix (the owner's spec).
  3. Into the box: pasted, the way dictation apps do it - a page of text
     lands at once, in any app, without an editor's auto-indent and
     closing brackets mangling it - and the clipboard is put back exactly
     as it was, whatever was on it. Nothing is ever submitted: no Enter.
     If the box lost the focus while Tora wrote, the text is left on the
     clipboard instead, to paste where they want it.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from dataclasses import dataclass
from typing import Optional

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

FIRST decide what this is - kind:
  • "dictation": words to write out - a message, an email, a reply, notes,
    a description, a prompt, or just talking an idea through. "Write",
    "type", "say", "tell them", "reply that" are dictation.
  • "command": they want something DONE, not written - "open", "go to",
    "search for", "send", "run", "create a file", "remind me". An
    instruction to act is a command even with a text box open in front of
    them: "write an email saying I'll be late" is dictation; "send an email
    saying I'll be late" is a command, though the email is half written.
    A command gets no text.
  • Only when it could truly be either - no instruction to act, no
    instruction to write - it is dictation: a text box is focused.

For dictation, the text:
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
  • The picture is the window they are in, for what "this", "it" and
    "reply to that" mean. Use it; never describe it.
  • Never invent a fact they did not give - a date, a price, a name: put a
    short [placeholder] in its place.
  • text is ONLY what goes in the box: no preamble, no quotes round it, no
    notes after it, and no markdown unless the box is a place for it."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["dictation", "command"]},
        "text": {"type": "string", "description": "For dictation: the finished text. Empty for a command."},
        "command": {"type": "string", "description": "For a command: what they want done, in a few words."},
    },
    "required": ["kind", "text"],
    "propertyOrdering": ["kind", "command", "text"],
}


@dataclass
class Written:
    """What the writer made of it: text to type, or a command to hand on."""
    kind: str = "dictation"
    text: str = ""
    command: str = ""

    def __bool__(self) -> bool:
        return bool(self.text) or self.kind == "command"

WRITE_TIMEOUT_S = 25.0


async def compose(request: str, box: Box, picture_b64: str = "") -> Written:
    """Dictation or command, and for dictation the text for `box`, written
    from what the user said. An empty Written if it failed."""
    where = f'A {box.kind.replace("Control", "").lower() or "text"} box'
    if box.name:
        where += f' named "{box.name}"'
    if box.window:
        where += f' in the window "{box.window}"'
    ask = f"{where}.\n"
    if box.text.strip():
        ask += f"It already holds (its end):\n<<<\n{box.text}\n>>>\n"
    ask += f"\nWhat they said they want written:\n{request.strip()}"
    parts = ([gemini.image_part(picture_b64)] if picture_b64 else []) + [gemini.text_part(ask)]
    try:
        data = await gemini.generate_json(
            Models.WRITER, [{"role": "user", "parts": parts}], _SCHEMA, system=_SYSTEM,
            max_tokens=4096, think="low", media_resolution="medium", timeout=WRITE_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - say so; never a half-written box
        _log.warning("writing failed: %s", str(e)[:200])
        return Written(text="")
    if not isinstance(data, dict):
        return Written(text="")
    if data.get("kind") == "command":
        return Written("command", "", str(data.get("command") or "").strip()[:200])
    return Written("dictation", _clean(str(data.get("text") or "")))


def _clean(text: str) -> str:
    """The text alone: a model that wraps its answer in quotes or a fence anyway."""
    t = (text or "").strip()
    if t.startswith("```") and t.endswith("```"):
        t = t.strip("`")
        t = t.split("\n", 1)[1] if "\n" in t else t
    if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'“”":
        t = t[1:-1]
    return t.strip()


# ── Into the box ─────────────────────────────────────────────────────────────

class Keyboard:
    """What putting text in needs from Windows. Tests use a fake."""

    def modifiers_down(self) -> bool:
        user32 = ctypes.windll.user32
        return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def paste(self) -> None:
        self._ctrl(0x56)                                                 # Ctrl+V

    def to_start(self) -> None:
        self._ctrl(0x24)                                                 # Ctrl+Home: the top of the box

    def _ctrl(self, vk_key: int) -> None:
        user32 = ctypes.windll.user32
        for vk, up in ((0x11, 0), (vk_key, 0), (vk_key, 2), (0x11, 2)):  # ctrl down, key down, key up, ctrl up
            user32.keybd_event(vk, 0, up, 0)
            time.sleep(0.01)

    def type(self, text: str) -> None:
        """Typed, key by key, as unicode: when the clipboard cannot be used.
        A new line is Shift+Enter - Enter would send a chat message."""
        user32 = ctypes.windll.user32
        for ch in text:
            if ch == "\n":
                user32.keybd_event(0x10, 0, 0, 0)
                user32.keybd_event(0x0D, 0, 0, 0)
                user32.keybd_event(0x0D, 0, 2, 0)
                user32.keybd_event(0x10, 0, 2, 0)
            elif ch != "\r":
                for flags in (4, 6):                                     # KEYEVENTF_UNICODE, | KEYUP
                    _unicode_key(ord(ch), flags)
            time.sleep(0.002)


def _unicode_key(code: int, flags: int) -> None:
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

    units = [code] if code < 0x10000 else [0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
    for unit in units:
        inp = INPUT(type=1)
        inp.ki = KEYBDINPUT(0, unit, flags, 0, 0)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


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
    """Wait for the talk shortcut to be let go: Ctrl+V with Win still held
    is Win+Ctrl+V."""
    deadline = time.monotonic() + seconds
    while keyboard.modifiers_down() and time.monotonic() < deadline:
        time.sleep(0.05)


def paste(text: str, *, keyboard: Optional[Keyboard] = None, clipboard=None) -> str:
    """`text` into whatever has the focus now: "pasted" (the clipboard put
    back after), or "typed" when the clipboard cannot be had."""
    keyboard = keyboard or Keyboard()
    if clipboard is None and sys.platform == "win32":
        clipboard = Clipboard()
    saved = clipboard.save() if clipboard is not None else None
    if saved is not None and clipboard.set_text(text):
        keyboard.paste()
        time.sleep(PASTE_SETTLE_S)
        clipboard.restore(saved)
        return "pasted"
    keyboard.type(text)
    return "typed"


def put(text: str, box: Box, *, keyboard: Optional[Keyboard] = None, clipboard=None,
        now_focused=focused_box) -> str:
    """Put `text` into `box`. Returns how: "pasted", "typed", or "clipboard"
    (the box lost the focus - the text waits on the clipboard)."""
    keyboard = keyboard or Keyboard()
    keys_let_go(keyboard)
    if clipboard is None and sys.platform == "win32":
        clipboard = Clipboard()
    if not box.same(now_focused()):
        if clipboard is not None and clipboard.set_text(text):
            return "clipboard"
        return ""
    return paste(text, keyboard=keyboard, clipboard=clipboard)
