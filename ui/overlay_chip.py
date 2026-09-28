"""The step chip: what it says (OD-04) and where it goes (OD-05).

One chip beside the target replaces the classic look's two labels (the
blue "found it!" bubble and the dark status card). It says the action and
the thing - "Click Export", "Press Ctrl + S" - taken from the step, never a
random phrase, and it never covers what it points at.

Pure logic, no painting (ui/overlay_paint.py draws it), so both halves are
tested directly: every step kind's words, and placement over a grid of
targets on several monitor layouts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from PyQt6.QtCore import QPointF, QRectF, QSizeF

from ui import overlay_theme as t

# (words that start an instruction, the verb the chip shows)
_VERBS = (
    (("double-click", "double click", "doubleclick"), "Double-click"),
    (("right-click", "right click", "rightclick"), "Right-click"),
    (("click", "tap", "hit", "press the", "press on"), "Click"),
    (("type", "enter", "write", "fill in", "put"), "Type"),
    (("press", "hold"), "Press"),
    (("choose", "select", "pick"), "Choose"),
    (("open", "go to", "navigate to"), "Open"),
    (("drag", "move"), "Drag"),
    (("scroll",), "Scroll"),
)
_MODS = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "windows": "Win",
         "cmd": "Cmd", "command": "Cmd"}
_KEYS = {"enter": "Enter", "return": "Enter", "esc": "Esc", "escape": "Esc", "tab": "Tab", "delete": "Delete",
         "del": "Delete", "backspace": "Backspace", "space": "Space", "spacebar": "Space", "home": "Home",
         "end": "End", "pageup": "Page Up", "pagedown": "Page Down"}
_COMBO = re.compile(r"(?i)\b((?:(?:ctrl|control|alt|shift|win|windows|cmd|command)\s*\+\s*)+"
                    r"(?:f\d{1,2}|[a-z0-9]|enter|return|esc|escape|tab|delete|del|space|home|end|pageup|pagedown"
                    r"|backspace|up|down|left|right))\b")
_ALONE = re.compile(r"(?i)\b(?:press|hit|tap)\s+(?:the\s+)?(enter|return|esc|escape|tab|delete|backspace|space"
                    r"|spacebar|f\d{1,2}|home|end)\b")
_QUOTED = re.compile(r"[\"“‘']([^\"”’']{1,40})[\"”’']")
# "Type your name in the Name box" -> "your name"
_TYPE_WHAT = re.compile(r"(?i)^(?:type|enter|write|fill\s+in|put)\s+(.+?)(?:\s+(?:in|into|on|to|at|for|under)\b|[.,;:!]|$)")


@dataclass
class Chip:
    badge: str = ""                                       # "2/5", "3" while the plan is written, "" outside a guide
    parts: list[tuple[str, str]] = field(default_factory=list)   # ("text"|"strong"|"key", words)

    @property
    def text(self) -> str:
        """The chip as plain words: "2/5 Press Ctrl+S"."""
        out: list[str] = [self.badge] if self.badge else []
        keys: list[str] = []
        for kind, words in self.parts:
            if kind == "key":
                keys.append(words)
                continue
            if keys:
                out.append("+".join(keys))
                keys = []
            out.append(words)
        if keys:
            out.append("+".join(keys))
        return " ".join(out)

    def __bool__(self) -> bool:
        return bool(self.parts)


def _clip(s: str, room: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= room else s[:max(1, room - 1)].rstrip() + "…"


def keys_in(instruction: str) -> list[str]:
    """The keys a step asks for, as caps: "Press Ctrl+Shift+N" -> [Ctrl, Shift, N]."""
    m = _COMBO.search(instruction or "")
    if m:
        out = []
        for k in re.split(r"\s*\+\s*", m.group(1)):
            low = k.lower()
            out.append(_MODS.get(low) or _KEYS.get(low) or (k.upper() if len(k) <= 3 else k.title()))
        return out
    m = _ALONE.search(instruction or "")
    if m:
        low = m.group(1).lower()
        return [_KEYS.get(low) or low.upper()]
    return []


def chip_for(instruction: str = "", target: str = "", *, current: int = 0, total: int = 0) -> Chip:
    """What the chip says for a step (or, with no step, for plain pointing)."""
    in_guide = total != 0
    badge = (f"{current}/{total}" if total > 0 else f"{current}") if in_guide and current else ""
    target = " ".join((target or "").split()).rstrip(".")
    words = " ".join((instruction or "").split())
    low = words.lower()
    keys = keys_in(words) if in_guide else []
    if keys:
        return Chip(badge, [("text", "Press")] + [("key", k) for k in keys])
    verb = ""
    if in_guide:
        for starts, shown in _VERBS:
            if any(low.startswith(s) for s in starts):
                verb = shown
                break
    if verb == "Type":
        if "password" in low:
            thing = "your password"                         # never the text itself
        else:
            q = _QUOTED.search(words)
            said = _TYPE_WHAT.match(words)
            short = said.group(1) if said and len(said.group(1).split()) <= 4 else ""
            thing = q.group(1) if q else (short or target or "")
        room = t.CHIP_MAX_CHARS - len(verb) - 1
        return Chip(badge, [("text", verb), ("strong", _clip(thing, room))] if thing else [("text", verb)])
    if verb == "Scroll":
        direction = next((d for d in ("up", "down", "left", "right") if f" {d}" in f" {low}"), "down")
        return Chip(badge, [("text", "Scroll"), ("strong", direction)])
    if not target:
        return Chip(badge, [("text", verb)] if verb else [])
    if not verb:
        return Chip(badge, [("strong", _clip(target, t.CHIP_MAX_CHARS))])
    return Chip(badge, [("text", verb), ("strong", _clip(target, t.CHIP_MAX_CHARS - len(verb) - 1))])


# -- where it goes -------------------------------------------------------------------------------------

EDGE = 4.0          # never closer than this to the screen's edge


def target_ring(tip: QPointF, bounds: QRectF | None = None) -> QRectF:
    """The area the ring marks: the element's bounds when known, else a circle round the tip."""
    if bounds is not None and bounds.isValid():
        return bounds.adjusted(-t.RING_PAD, -t.RING_PAD, t.RING_PAD, t.RING_PAD)
    return QRectF(tip.x() - t.RING_R, tip.y() - t.RING_R, 2 * t.RING_R, 2 * t.RING_R)


def place_chip(ring: QRectF, size: QSizeF, screen: QRectF, avoid: list[QRectF] | None = None) -> QRectF:
    """Right of the ring, else left, below, above: the first place that is
    wholly on the target's screen and covers neither the ring nor anything in
    `avoid` (the island, the arrow). If none is, the nearest screen corner
    that does not cover the target."""
    w, h, g = size.width(), size.height(), t.CHIP_GAP
    cy, cx = ring.center().y(), ring.center().x()
    candidates = [QRectF(ring.right() + g, cy - h / 2, w, h),
                  QRectF(ring.left() - g - w, cy - h / 2, w, h),
                  QRectF(cx - w / 2, ring.bottom() + g, w, h),
                  QRectF(cx - w / 2, ring.top() - g - h, w, h)]
    inside = screen.adjusted(EDGE, EDGE, -EDGE, -EDGE)
    blocks = [ring] + list(avoid or [])

    def ok(r: QRectF) -> bool:
        return inside.contains(r) and not any(r.intersects(b) for b in blocks)

    for c in candidates:
        if ok(c):
            return c
    for c in candidates:                       # slide along the edge it ran off, if that clears it
        moved = QRectF(c)
        moved.moveLeft(min(max(moved.left(), inside.left()), inside.right() - w))
        moved.moveTop(min(max(moved.top(), inside.top()), inside.bottom() - h))
        if ok(moved):
            return moved
    corners = [QRectF(inside.left(), inside.top(), w, h), QRectF(inside.right() - w, inside.top(), w, h),
               QRectF(inside.left(), inside.bottom() - h, w, h), QRectF(inside.right() - w, inside.bottom() - h, w, h)]
    corners.sort(key=lambda r: (r.center().x() - cx) ** 2 + (r.center().y() - cy) ** 2)
    for c in corners:
        if not any(c.intersects(b) for b in blocks):
            return c
    for c in corners:                          # never over the target itself
        if not c.intersects(ring):
            return c
    return corners[-1]
