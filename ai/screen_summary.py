"""
What Tora tells the model about the screen, in one line of text.

This is the single biggest thing standing between a slow assistant and a fast
one. Sending a screenshot with every question costs a screen grab, a JPEG
encode, a couple of hundred kilobytes uploaded and a vision model actually
looking at it — well over a second before the model has even heard the
question, paid on every turn whether the question was about the screen or not.

Almost none of that is necessary. Windows already knows which app is in front,
what its window is called, what has keyboard focus and what every button on it
is called, and it will say so in a few hundred milliseconds without sending a
single pixel anywhere. That is enough to answer most of what people actually
ask:

    "where's the Conversations tab?"   → the name is in the list; point_at
                                          resolves it locally in ~5 ms
    "what app am I in?"                → the window title
    "how do I make this bold?"         → "Bold (Ctrl+B)" is right there

Pixels are only needed when the question is genuinely about what the screen
LOOKS like — a game, a chart, a photo, a chessboard, an error nobody labelled.
For those the model calls look_at_screen and pays the cost once, deliberately,
having said "let me look" first.

Metadata only, never content: control NAMES, never values, never anything
typed, never a password field's contents. A button's caption is what the user
is already looking at.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from tutor import (
    active_window_title, active_process_name, app_key, app_class, look_hint,
    is_sensitive_window,
)

_log = logging.getLogger("tora.screen")

# The tree does not change between two questions asked seconds apart in the
# same window, and walking it is the expensive part. Short enough that
# switching tabs is picked up, long enough that a quick follow-up is free.
_CACHE_TTL_S = 4.0

PRIVATE = "a private application"


@dataclass
class Summary:
    line: str = ""
    names: list[str] = field(default_factory=list)
    # (cx, cy, name, type) in physical px — the same walk the names come from,
    # kept so "the one to its left" resolves without a second tree walk.
    pointables: list = field(default_factory=list)
    title: str = ""
    sensitive: bool = False
    cls: str = "named"        # web | opaque | named — see tutor.app_class

    def __bool__(self) -> bool:
        return bool(self.line)


_cache: dict[str, tuple[float, Summary]] = {}


def _focus_phrase() -> str:
    from ai.hybrid_pointer import focused_element
    focused = focused_element()
    if not focused:
        return ""
    ctrl_type, name, is_password = focused
    if is_password:
        return "; focused: a password field"
    kind = ctrl_type.replace("Control", "").lower() or "control"
    if name:
        return f'; focused: {kind} "{name[:60]}"'
    return f"; focused: {kind}"


def describe(*, privacy_guard: bool = True, use_cache: bool = True) -> Summary:
    """One line about the screen. Blocking — call it in a thread.

    Safe to send on every turn: it never contains pixels or typed content, so
    it does not need the screenshot's privacy handling. A blocked app is
    reported as existing and off limits, never by name.
    """
    title = active_window_title()

    if privacy_guard and is_sensitive_window(title):
        # The model is told there is something there and that it is off
        # limits — never what it is.
        return Summary(line=f"Screen: {PRIVATE}.", title=title, sensitive=True)

    key = app_key(title) + "|" + title
    if use_cache:
        hit = _cache.get(key)
        if hit and (time.monotonic() - hit[0]) < _CACHE_TTL_S:
            return hit[1]

    if not title:
        return Summary(line="Screen: no application window is in the foreground.")

    parts = [f'Screen: "{title[:90]}"']
    try:
        parts.append(_focus_phrase())
    except Exception:
        _log.debug("focus lookup failed", exc_info=True)

    names: list[str] = []
    pointables: list = []
    try:
        from ai.hybrid_pointer import list_pointables
        pointables = list_pointables()
        # De-dup names in first-seen order for the model-facing list.
        names = list(dict.fromkeys(p[2] for p in pointables))[:28]
    except Exception:
        _log.debug("control enumeration failed", exc_info=True)

    parts.append(".")
    if names:
        # ⚠ This sentence is why pointing is fast. Without it the model does
        # not know "Conversations" is a tab it can name, so it reaches for
        # look_at_screen and a vision call where point_at would have
        # answered instantly.
        parts.append(
            " Controls you can point at by name: " + ", ".join(names) + "."
        )

    # ⚠ And this sentence is why looking is fast: it tells the model when the
    # names are enough and when it genuinely has to look at pixels, so it stops
    # paying for a vision call on questions a name already answers.
    cls = "named"
    try:
        cls = app_class(active_process_name(), len(pointables))
        parts.append(look_hint(cls))
    except Exception:
        _log.debug("app classification failed", exc_info=True)

    summary = Summary(
        line="".join(parts), names=names, pointables=pointables,
        title=title, cls=cls,
    )
    _cache[key] = (time.monotonic(), summary)
    return summary


def invalidate() -> None:
    """Drop the cache — the screen has definitely changed."""
    _cache.clear()
