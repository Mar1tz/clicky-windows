"""What each mark Tora draws means (PT-11, the Massive Direction Plan).

Six marks, one meaning each, so a glance says how sure she is and what she
wants you to look at:

    ring      this exact control - the accessibility tree found it
    reticle   this exact spot - a point with no control round it
    frame     somewhere in here - a region, or her best guess (dashed)
    arrow     from here to there - a direction, a move, a connection
    pill      the name of what is marked, beside it (never on it)
    wash      everything else, dimmed - this area only

Every drawing maps to one (mark_of): pointing marks carry their name in
shape["mark"]; a lesson's strokes are read by their shape. The overlay
paints a frame dashed and a reticle with its crosshair, so a guess can
never be mistaken for a ring.
"""
from __future__ import annotations

VOCABULARY = {
    "ring": "this exact control",
    "reticle": "this exact spot",
    "frame": "somewhere in here",
    "arrow": "from here to there",
    "pill": "the name of what is marked",
    "wash": "this area only",
}

# A lesson's shapes, by what they say.
_BY_KIND = {
    "rect": "frame",
    "poly": "frame",
    "underline": "frame",          # this line of text
    "circle": "ring",
    "line": "arrow",
    "arrow": "arrow",
    "angle": "arrow",              # a turn, from one way to another
    "text": "pill",
    "wash": "wash",
}


def mark_of(shape: dict) -> str:
    """The vocabulary entry a drawing is."""
    named = shape.get("mark")
    if isinstance(named, str) and named in VOCABULARY:
        return named
    return _BY_KIND.get(shape.get("kind", ""), "frame")
