"""Six routes and why (OR-03, OR-04, the Massive Direction Plan).

Every request goes one of six ways:

    answer   say it                     "what does this error mean?"
    draw     show where, on screen      "where is the export button?"
    clarify  ask one question first    "delete that" - with nothing shown
    guided   walk them through it       "how do I set up two-factor?"
    direct   do one thing now           "next song", "open Bluetooth settings"
    job      a piece of work, in the    "build me a landing page",
             background                 "research flights to Lisbon"

The live model chooses its tools itself; this table is the deterministic
account of what it should choose, testable without a model (OR-03), and
each choice carries a reason code said in plain words (OR-04). Each turn
logs the table's route beside the one the model took (model_route), so
how often they agree is measured from tora.log, and the labelled set in
tests/agents/test_e2e_routes.py reports accuracy and how often it asks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ROUTES = ("answer", "draw", "clarify", "guided", "direct", "job")
REASONS = {
    "explain": "a question to answer",
    "show_me": "they want to be shown where something is",
    "traced": "they showed something on screen and asked about it",
    "how_to": "a task with steps to walk them through",
    "do_now": "one quick action in an app",
    "needs_confirmation": "it sends, deletes, pays or posts - it asks first",
    "target_ambiguous": "\"this\" or \"that\" with nothing shown - ask which",
    "long_deliverable": "a piece of work to make in the background",
}

_W = re.IGNORECASE
_LOCATE = re.compile(r"\b(where\s+(is|are|do|can|should|would)|point\s+(at|to)|show\s+me\s+(the|where)|"
                     r"find\s+the|locate\s+the|highlight\s+the|which\s+button|circle\s+the)\b", _W)
_HOW_TO = re.compile(r"\b(how\s+(do|can|would)\s+i|how\s+to|walk\s+me\s+through|show\s+me\s+how|teach\s+me|"
                     r"step\s+by\s+step|guide\s+me)\b", _W)
_DIRECT = re.compile(
    r"\b(?:next|previous|skip|pause|resume|play)\b[^.?!]{0,24}\b(?:song|track|music|playlist|video)\b|"
    r"\b(?:turn|put)\s+(?:the\s+)?(?:volume|sound|music)\s+(?:up|down|off)\b|\b(?:mute|unmute)\b|"
    r"\bopen\s+(?:the\s+|my\s+)?[\w\- ]{1,30}?\b(?:settings|app|folder|downloads|documents)\b|"
    r"\b(?:minimi[sz]e|maximi[sz]e|close)\s+(?:this|the)\s+window\b|"
    r"\b(?:open|launch|start)\s+(?:spotify|chrome|edge|outlook|word|excel|notepad|teams|slack|discord|vs ?code)\b",
    _W)
_SENSITIVE = re.compile(r"\b(send|delete|remove|erase|pay|buy|purchase|order|post|publish|transfer|"
                        r"submit|unsubscribe|cancel\s+my)\b", _W)
_MAKE = re.compile(r"\b(build|create|make|write|draft|research|analy[sz]e|compare|plan|organi[sz]e|"
                   r"summari[sz]e\s+(?:all|every|these)|put\s+together|set\s+up\s+a|design)\b", _W)
_THING = re.compile(r"\b(app|website|site|page|report|document|doc|essay|letter|email|presentation|deck|"
                    r"spreadsheet|sheet|plan|itinerary|list|summary|script|game|landing|invoice|budget|"
                    r"comparison|analysis|project|flights?|hotels?|options)\b", _W)
_DEICTIC = re.compile(r"\b(this|that|these|those|it)\b", _W)
_QUESTION = re.compile(r"^\s*(what|why|who|when|which|is|are|does|do|can|could|should|would|will|how\s+"
                       r"(much|many|long|come|does|is|are))\b|\?\s*$", _W)


@dataclass(frozen=True)
class Decision:
    route: str
    reason: str

    @property
    def words(self) -> str:
        return REASONS[self.reason]

    def __str__(self) -> str:
        return f"{self.route} ({self.reason})"


def decide(text: str, *, traced: bool = False, selection: bool = False, pointed: bool = False) -> Decision:
    """The route for a request. `traced`: they showed something with the
    shortcut held; `selection`: text is selected; `pointed`: Tora pointed
    at something a moment ago (so "that" has a referent)."""
    t = (text or "").strip()
    shown = traced or selection or pointed
    question = bool(_QUESTION.search(t))
    sensitive = bool(_SENSITIVE.search(t))
    deictic = bool(_DEICTIC.search(t))

    if sensitive and deictic and not shown and not question:
        return Decision("clarify", "target_ambiguous")
    if _MAKE.search(t) and _THING.search(t) and not question and not _HOW_TO.search(t):
        return Decision("job", "long_deliverable")
    if sensitive and not question and not _HOW_TO.search(t):
        return Decision("direct", "needs_confirmation")
    if _DIRECT.search(t) and not _HOW_TO.search(t) and not _LOCATE.search(t):
        return Decision("direct", "do_now")
    if _HOW_TO.search(t) and not _LOCATE.search(t):
        return Decision("guided", "how_to")
    if _LOCATE.search(t):
        return Decision("draw", "show_me")
    if traced:
        return Decision("answer", "traced")
    return Decision("answer", "explain")


# OR-18: a request that corrects the last answer - counted per day, the
# correction rate beside the agreement between table and model.
_CORRECTION = re.compile(r"^\s*(no[,.!]?\s+(i\s+meant|the\s+other|not\s+(that|this|the))|not\s+that\s+one|"
                         r"that'?s\s+(not\s+(it|right|what)|wrong)|wrong\s+(one|button|place)|i\s+said\b)", _W)


def is_correction(text: str) -> bool:
    return bool(_CORRECTION.search(text or ""))


# What the live model did, from the tools it called this turn.
_TOOL_ROUTE = {"point_at": "draw", "draw": "draw", "show_me_how": "guided", "guide_step": "guided",
               "desktop_action": "direct", "write_for_me": "direct", "fill_form": "direct",
               "reply_to_email": "direct", "delegate_task": "job", "send_email": "direct",
               "connect_account": "direct"}


def model_route(tools: list[str]) -> str:
    """The route the model took: its first routing tool, else an answer."""
    for name in tools:
        if name in _TOOL_ROUTE:
            return _TOOL_ROUTE[name]
    return "answer"
