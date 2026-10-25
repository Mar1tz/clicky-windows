"""Marks on the phrase that names them (PT-14, the Massive Direction Plan).

Pointing is decided before the sentence is spoken: the model calls
point_at, then talks. So "first File, then Save" put both arrows up before
she had said "File". A mark now waits for her voice to reach a word that
names it - the same clock the answer card keeps time with (the share of
the received audio heard so far, applied to the words received) - and
appears with it. A label she never says aloud ("that one") goes up at
MAX_WAIT_S, late rather than never.

Pure: the manager feeds it her words and the voice clock, and polls.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

MAX_WAIT_S = 2.5
LEAD_S = 0.15                       # a hair before the word is heard, as the card does
_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def names(label: str, spoken: str) -> bool:
    """Has `spoken` said a word that names `label`? Its longer words count
    (a control called "Search or enter address" is "the address bar");
    a short label must be said whole."""
    said = set(_words(spoken))
    words = _words(label)
    if not words:
        return True
    strong = [w for w in words if len(w) >= 4]
    return any(w in said for w in strong) if strong else all(w in said for w in words)


class PhraseGate:
    def __init__(self) -> None:
        self.text = ""                                   # her words this answer, as received
        self.pending: list[tuple[str, Callable[[], None], float]] = []

    def new_answer(self) -> None:
        """A new question: her words start again; what was waiting goes up now."""
        self.flush()
        self.text = ""

    def said(self, chunk: str) -> None:
        self.text += chunk or ""

    def spoken(self, clock: Optional[tuple[float, float]]) -> str:
        """The words her voice has reached, by the clock (heard, received)."""
        if not clock or clock[1] <= 0:
            return ""
        heard, received = clock
        share = min(1.0, max(0.0, (heard + LEAD_S) / received))
        return self.text[:int(len(self.text) * share)]

    def wait(self, label: str, fire: Callable[[], None], now: float) -> None:
        self.pending.append((label, fire, now + MAX_WAIT_S))

    def poll(self, clock: Optional[tuple[float, float]], now: float) -> int:
        """Put up every mark whose words have been said, or whose wait is
        over. How many went up."""
        if not self.pending:
            return 0
        spoken = self.spoken(clock)
        due, keep = [], []
        for item in self.pending:
            label, _fire, deadline = item
            (due if names(label, spoken) or now >= deadline else keep).append(item)
        self.pending = keep
        for _label, fire, _deadline in due:
            fire()
        return len(due)

    def flush(self) -> None:
        due, self.pending = self.pending, []
        for _label, fire, _deadline in due:
            fire()
