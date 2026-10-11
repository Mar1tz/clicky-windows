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


def _text_at(ctrl, x: int, y: int, unit: int, auto) -> str:
    """The text at a point, from the nearest element up the tree that has it."""
    node = ctrl
    for _ in range(8):
        if node is None:
            return ""
        try:
            pattern = node.GetPattern(auto.PatternId.TextPattern)
        except Exception:  # noqa: BLE001
            pattern = None
        if pattern is not None:
            try:
                rng = pattern.RangeFromPoint(x, y)
                if rng is None:
                    return ""
                rng.ExpandToEnclosingUnit(unit, waitTime=0)
                return " ".join((rng.GetText(_MAX_TEXT) or "").split())
            except Exception:  # noqa: BLE001 - a provider that will not say
                return ""
        try:
            node = node.GetParentControl()
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _text_between(ctrl, a: tuple, b: tuple, auto) -> str:
    """The text from one point to another - everything inside a circle or a
    sweep, read as one span, where a few sample points miss most of it."""
    node = ctrl
    for _ in range(8):
        if node is None:
            return ""
        try:
            pattern = node.GetPattern(auto.PatternId.TextPattern)
        except Exception:  # noqa: BLE001
            pattern = None
        if pattern is not None:
            try:
                start, end = pattern.RangeFromPoint(*a), pattern.RangeFromPoint(*b)
                if start is None or end is None:
                    return ""
                start.MoveEndpointByRange(auto.TextPatternRangeEndpoint.End, end,
                                          auto.TextPatternRangeEndpoint.End, waitTime=0)
                return " ".join((start.GetText(_MAX_TEXT) or "").split())
            except Exception:  # noqa: BLE001
                return ""
        try:
            node = node.GetParentControl()
        except Exception:  # noqa: BLE001
            return ""
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
                    try:
                        top = ctrl.GetTopLevelControl()
                        reading.window = ((top.Name if top is not None else "") or "")[:90]
                    except Exception:  # noqa: BLE001
                        pass
                thing = _thing(ctrl)
                if thing and thing not in reading.things:
                    reading.things.append(thing)
                if ctrl.ControlTypeName in ("ListItemControl", "DataItemControl") and ctrl.Name:
                    for named in (ctrl, ctrl.GetParentControl()):
                        if named is not None and named.ControlTypeName == "ListItemControl" and named.Name \
                                and named.Name not in names:
                            names.append(named.Name)
                text = _text_at(ctrl, int(x), int(y), unit, auto)
                if text and not any(text in t or t in text for t in texts):
                    texts.append(text)
            if reading.focus.kind in ("circle", "sweep") and time.monotonic() < deadline:
                l, t, r, b = reading.focus.rect
                inset = 6
                try:
                    corner = auto.ControlFromPoint(int(l + inset), int(t + inset))
                    span = _text_between(corner, (int(l + inset), int(t + inset)),
                                         (int(r - inset), int(b - inset)), auto) if corner is not None else ""
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
