"""
What is under a trace, read - never photographed (docs/TRACE_TO_FOCUS.md,
TF-04).

Most of what people trace has words: a paragraph they underline, a table
row, a button, an error message. Windows UI Automation gives the control
at a point and the text at a point (TextPattern.RangeFromPoint, widened to
the word, line or paragraph) in milliseconds, and no pixels leave the PC -
the owner's "no screenshots, it needs to be instant" (10 Oct 2026). Only a
picture - a chart, a photo, a board - still needs a look, and the voice
knows which from what is read here.

An underline reads its line; a circle or a sweep the paragraphs inside it;
a point the line and the control under it. Time-boxed: what is not read in
READ_BUDGET_S stays a shape and a place. Never a password field.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from ai import uia_page
from ai.trace import Focus

_log = logging.getLogger("tora.trace")

READ_BUDGET_S = 0.3
_UNIT = {"underline": "Line", "point": "Line", "circle": "Paragraph", "sweep": "Paragraph"}
_MAX_TEXT = 600
_WHAT = {"ButtonControl": "button", "HyperlinkControl": "link", "ImageControl": "picture",
         "EditControl": "text box", "TabItemControl": "tab", "MenuItemControl": "menu item",
         "ListItemControl": "list item", "DataItemControl": "cell", "CheckBoxControl": "check box",
         "ComboBoxControl": "drop-down", "HeaderItemControl": "column heading"}
# What text cannot describe: a look is worth its cost for these.
PICTURES = {"picture", "chart", "canvas"}


@dataclass
class Reading:
    focus: Focus
    text: str = ""                                       # the words under it
    things: list = field(default_factory=list)           # 'button "Export"', 'picture "Revenue chart"'
    window: str = ""
    files: list = field(default_factory=list)            # a file traced over, as its path (ai/screen_files)

    def is_picture(self) -> bool:
        """Nothing to read, or what is there is a picture: worth a look."""
        return not self.text or any(t.split(" ")[0] in PICTURES for t in self.things)

    def line(self) -> str:
        """One clause for the voice: 'circled "Total due within 30 days" (a paragraph)'."""
        if self.files:
            what = "the file " + ", ".join(f'"{f}"' for f in self.files[:4]) if len(self.files) == 1 else \
                "the files " + ", ".join(f'"{f}"' for f in self.files[:4])
            return f"{self.focus.describe()} {what}"
        parts = []
        if self.text:
            parts.append(f'"{self.text}"')
        if self.things:
            parts.append(", ".join(self.things[:3]))
        what = " - ".join(parts) or "an area with no text to read"
        return f"{self.focus.describe()} {what}"


def _text_pattern(node, auto):
    try:
        return node.GetPattern(auto.PatternId.TextPattern)
    except Exception:  # noqa: BLE001
        return None


class _Texts:
    """Where the words at a point come from: the nearest element up the tree
    that gives text. On a web page that is the document, ~7 parents above
    what is at the point - so once it is found for a focus it is kept, and
    the next point on the same page asks it straight away (WR-02, 18 Oct
    2026: a circle's 9 points climbed 68 parents, 190 ms of a 230 ms read).
    A control with text of its own - a box on the page - still answers for
    itself."""

    def __init__(self, auto):
        self.auto = auto
        self.pattern = None
        self.box: Optional[tuple] = None

    def at(self, ctrl, x: int, y: int):
        own = _text_pattern(ctrl, self.auto)
        if own is not None:
            return own
        if self.pattern is not None and self.box is not None:
            l, t, r, b = self.box
            if l <= x < r and t <= y < b:
                return self.pattern
        node = ctrl
        for _ in range(7):
            try:
                node = node.GetParentControl()
            except Exception:  # noqa: BLE001
                return None
            if node is None:
                return None
            pattern = _text_pattern(node, self.auto)
            if pattern is not None:
                try:
                    r = node.BoundingRectangle
                    self.pattern, self.box = pattern, (r.left, r.top, r.right, r.bottom)
                except Exception:  # noqa: BLE001 - used for this point, not kept
                    pass
                return pattern
        return None


def _text_at(pattern, x: int, y: int, unit: int) -> str:
    """The text at a point, widened to the word, line or paragraph."""
    try:
        rng = pattern.RangeFromPoint(x, y)
        if rng is None:
            return ""
        rng.ExpandToEnclosingUnit(unit, waitTime=0)
        return " ".join((rng.GetText(_MAX_TEXT) or "").split())
    except Exception:  # noqa: BLE001 - a provider that will not say
        return ""


def _text_between(pattern, a: tuple, b: tuple, auto) -> str:
    """The text from one point to another - everything inside a circle or a
    sweep, read as one span, where a few sample points miss most of it."""
    try:
        start, end = pattern.RangeFromPoint(*a), pattern.RangeFromPoint(*b)
        if start is None or end is None:
            return ""
        start.MoveEndpointByRange(auto.TextPatternRangeEndpoint.End, end,
                                  auto.TextPatternRangeEndpoint.End, waitTime=0)
        return " ".join((start.GetText(_MAX_TEXT) or "").split())
    except Exception:  # noqa: BLE001
        return ""


def _window_at(x: float, y: float) -> str:
    """The title of the window under a point - asked of Windows, not walked
    up the accessibility tree (20 ms of parents)."""
    from ai import screen_files
    hwnd = screen_files.root_window_at(x, y)
    if not hwnd:
        return ""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value[:90]
    except Exception:  # noqa: BLE001
        return ""


def _thing(ctrl) -> str:
    """'button "Export"' - what kind of thing is under the point, and its name."""
    kind = _WHAT.get(ctrl.ControlTypeName or "", "")
    name = " ".join((ctrl.Name or "").split())[:80]
    if not kind or not name:
        return ""
    if kind == "picture" and any(w in name.lower() for w in ("chart", "graph", "plot")):
        kind = "chart"
    return f'{kind} "{name}"'


def read(focuses: list[Focus], budget_s: float = READ_BUDGET_S) -> list[Reading]:
    """What is under each thing they showed. Blocking - call it in a thread."""
    out = [Reading(f) for f in focuses]
    try:
        import uiautomation as auto
    except Exception:  # noqa: BLE001 - no UI Automation: shapes and places only
        return out
    deadline = time.monotonic() + budget_s
    with uia_page.in_thread():
        for reading in out:
            unit = getattr(auto.TextUnit, _UNIT.get(reading.focus.kind, "Line"))
            source = _Texts(auto)
            texts: list[str] = []
            names: list[str] = []                     # list items: in File Explorer, files
            for x, y in reading.focus.samples:
                if time.monotonic() > deadline:
                    break
                try:
                    ctrl = auto.ControlFromPoint(int(x), int(y))
                except Exception:  # noqa: BLE001
                    ctrl = None
                if ctrl is None or bool(getattr(ctrl, "IsPassword", False)):
                    continue                         # never a password field
                if not reading.window:
                    reading.window = _window_at(x, y)
                thing = _thing(ctrl)
                if thing and thing not in reading.things:
                    reading.things.append(thing)
                if ctrl.ControlTypeName in ("ListItemControl", "DataItemControl") and ctrl.Name:
                    for named in (ctrl, ctrl.GetParentControl()):
                        if named is not None and named.ControlTypeName == "ListItemControl" and named.Name \
                                and named.Name not in names:
                            names.append(named.Name)
                pattern = source.at(ctrl, int(x), int(y))
                text = _text_at(pattern, int(x), int(y), unit) if pattern is not None else ""
                if text and not any(text in t or t in text for t in texts):
                    texts.append(text)
            if reading.focus.kind in ("circle", "sweep") and time.monotonic() < deadline:
                l, t, r, b = reading.focus.rect
                inset = 6
                try:
                    corner = auto.ControlFromPoint(int(l + inset), int(t + inset))
                    pattern = source.at(corner, int(l + inset), int(t + inset)) if corner is not None else None
                    span = _text_between(pattern, (int(l + inset), int(t + inset)),
                                         (int(r - inset), int(b - inset)), auto) if pattern is not None else ""
                except Exception:  # noqa: BLE001
                    span = ""
                if len(span) > len(" ".join(texts)):
                    texts = [span]
            reading.text = " … ".join(texts)[:_MAX_TEXT]
            if names:
                # 11 Oct: "turn this Word document into a PDF" over a file in
                # Explorer - the agent needs C:\...\10 Mei 2026.docx, not "10 Mei 2026".
                try:
                    from ai import screen_files
                    l, t, r, b = reading.focus.rect
                    reading.files = screen_files.files_at(names, screen_files.root_window_at((l + r) / 2, (t + b) / 2))
                except Exception:  # noqa: BLE001 - the names still say what it was
                    pass
    return out


def voice_line(readings: list[Reading]) -> str:
    """The line the voice gets when the shortcut comes up (TF-05)."""
    if not readings:
        return ""
    shown = "; ".join(f"({i}) {r.line()}" for i, r in enumerate(readings, 1)) if len(readings) > 1 \
        else readings[0].line()
    where = readings[0].window
    if any(r.files for r in readings):
        return (f"While asking, they traced with the mouse: {shown}" + (f' in "{where}"' if where else "") + ". "
                "'This', 'that' and 'these' mean these files. To do something with them, delegate_task - the "
                "paths go with it.")
    looks = any(r.is_picture() for r in readings)
    return (f"While asking, they traced with the mouse: {shown}" + (f' in "{where}"' if where else "") + ". "
            "'This', 'that', 'here' and 'these' mean what they traced" + (", in that order" if len(readings) > 1
                                                                         else "") + ". "
            + ("Part of it is a picture or has no text: look_at_screen sees the traced area."
               if looks else "Its words are above: answer from them - no need to look."))
