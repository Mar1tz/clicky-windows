"""
Two ways of looking at the screen.

  fast  gemini-3.8-flash with thinking switched off. The first look at
        nearly every question: measured on 16 everyday screens, 2-3 s and
        about $0.0014, and the right thing drawn on 15 of them.

  deep  gemini-3.1-pro-preview. The careful look: dense reading (code,
        small print, formulas) and a move in a game go here first; anything
        else only when the fast look says it is not sure, or has nothing to
        draw for a what-to-do question. 4-7 s, about $0.008 (UD-01,
        docs/SHOW_THE_NEXT_MOVE.md).

Both return the same shape:

    {answer, actions[], observations[]}

`actions` is what makes the drawing work. Each one carries a box in Gemini's
own convention — [ymin, xmin, ymax, xmax], normalized 0-1000 — which converts
to the centre point Tora's arrows aim at and the rectangle it rings. That
is the pipe between "the model can see it" and "the user sees an arrow
pointing at it".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ai import gemini
from ai.gemini import Models

_log = logging.getLogger("tora.vision")

# Questions where a quick answer is the wrong answer. These ask for judgement
# — the best option, the right move, what to do next — and the flash pass
# reliably produces something plausible and shallow. Straight to the deep
# model.
_ADVICE_RE = re.compile(
    r"\b(best|better|optimal|strongest|should\s+i|which\s+(one|should|is)|"
    r"recommend|what'?s?\s+wrong|why\s+(is|does|did|isn'?t|doesn'?t)|"
    r"how\s+do\s+i|how\s+to|explain|analyse|analyze|review|check\s+my|"
    r"next\s+move|winning|solve|fix)\b",
    re.IGNORECASE,
)

# Things that need reading rather than glancing at.
_DENSE_RE = re.compile(
    r"\b(chess|board|graph|chart|plot|diagram|equation|formula|table|"
    r"spreadsheet|code|stack\s*trace|error|log|handwrit|proof|circuit|"
    r"schematic|score|sheet\s*music|contract|clause)\b",
    re.IGNORECASE,
)

# "What should I do?" - the best move, the next step, which one to pick. The
# answer to these is an action, drawn: one arrow, one line. On 5 Oct a user
# asked for their best chess move and got the board read back to them.
_NEXT_ACTION_RE = re.compile(
    r"\b(best|next|good|winning|strongest|right)\s+(move|play|step)|"
    r"\bwhat\s+(should|do|can)\s+i\s+(do|play|move|pick|choose|click|press)|"
    r"\bwhich\s+(one\s+)?(should|do)\s+i\b|\bwhere\s+(should|do)\s+i\s+(move|go|click|put|place|play)|"
    r"\bmy\s+(next|best)\s+move\b|\bhow\s+do\s+i\s+(win|move)\b",
    re.IGNORECASE,
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": ("Spoken aloud: ONE short sentence, no markdown. For a what-to-do "
                            "question it states the action itself."),
        },
        "actions": {
            "type": "array",
            "description": "Things on screen worth pointing at or ringing.",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "1-3 words."},
                    "box": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "[ymin,xmin,ymax,xmax] normalized 0-1000.",
                    },
                    "kind": {
                        "type": "string",
                        "description": ("point | circle | rect | move. move: something goes from box "
                                        "to to_box - a piece to its square, a file to a folder."),
                    },
                    "to_box": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "For a move: where it goes, [ymin,xmin,ymax,xmax] 0-1000.",
                    },
                },
                "required": ["label", "box"],
            },
        },
        "observations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What is actually on screen that the answer rests on.",
        },
        "sure": {
            "type": "boolean",
            "description": ("true only when you could clearly read what was asked about and are confident "
                            "in the answer; false hands it to a stronger look."),
        },
    },
    "required": ["answer", "actions", "sure"],
    # Coordinates are emitted BEFORE the prose. If the reply is ever cut
    # off, it is the tail of a sentence that is lost, not the pointer.
    "propertyOrdering": ["actions", "answer", "observations"],
}

# Thinking tokens count against maxOutputTokens on this API. Unbounded
# (think=None) let the pro model reason for ~3,500 of its 4,096 tokens and
# then get cut off mid-answer — 40 s for nothing, three times in one
# session. This is a cap, not a floor: trivial questions use far less.
_DEEP_THINK = 1536
_DEEP_TIMEOUT_S = 35.0

_SYSTEM = """You are the eyes of Tora, a visual tutor that points at things
on the user's screen while it talks.

  • answer is SPOKEN aloud: ONE short plain sentence, no markdown, no lists.
    Never describe your own process: no "let me", no recounting, no counts
    the user did not ask for.
  • When they ask what to do - the best move, the next step, which one to
    pick, where to click - answer with the action itself ("Knight from b1 to
    c3, to develop a piece.") and put exactly ONE thing in actions: a move as
    kind "move" with box on the piece or thing where it is now and to_box on
    where it goes; otherwise the one thing to click or pick.
  • Describe only what is genuinely in the image. If you cannot read the
    thing being asked about clearly, say so in one sentence rather than
    guessing.
  • For other questions, put every element the user needs to look at in
    actions, with a tight box around it, in the order to look at them.
  • observations are the specific things you read off the screen that your
    answer depends on — piece positions, axis values, the exact error text.
    They are kept for checking and are never read to the user.
  • sure: false when you cannot read the thing clearly, or the judgement is
    genuinely hard (a strong chess move, a subtle bug) - a stronger look
    then takes over. Never guess to seem sure."""


@dataclass
class Action:
    label: str
    box: tuple[int, int, int, int]   # ymin, xmin, ymax, xmax — 0-1000
    kind: str = "point"
    # Where a move goes. A move used to be impossible to draw: one box per
    # action, every kind a labelled circle, so "knight b1 to c3" reached the
    # screen as two circles and no arrow (5 Oct, a user's chess game).
    to_box: Optional[tuple[int, int, int, int]] = None

    @property
    def is_move(self) -> bool:
        return self.to_box is not None and self.kind in ("move", "arrow")

    @property
    def center(self) -> tuple[int, int]:
        """(x, y) normalized 0-1000 — what an arrow aims at."""
        ymin, xmin, ymax, xmax = self.box
        return (xmin + xmax) // 2, (ymin + ymax) // 2

    @property
    def to_center(self) -> tuple[int, int]:
        ymin, xmin, ymax, xmax = self.to_box or self.box
        return (xmin + xmax) // 2, (ymin + ymax) // 2

    @property
    def radius(self) -> int:
        """Ring radius that comfortably contains the box."""
        ymin, xmin, ymax, xmax = self.box
        return max(20, int(max(xmax - xmin, ymax - ymin) * 0.62))

    def as_tag(self) -> str:
        """The drawing tag the overlay already knows how to render - in
        Tora's own colour, the one the user picked for her cursor."""
        x, y = self.center
        if self.is_move:
            # One arrow, piece to square, and no label on the board.
            tx, ty = self.to_center
            return f"[ARROW:{x},{y}->{tx},{ty}:tora]"
        if self.kind == "rect":
            ymin, xmin, ymax, xmax = self.box
            return f"[RECT:{xmin},{ymin},{xmax},{ymax}:tora]"
        if self.kind == "underline":
            ymin, xmin, ymax, xmax = self.box
            return f"[UNDERLINE:{xmin},{ymax},{xmax - xmin}:tora]"
        return f"[CIRCLE:{x},{y},{self.radius}:{self.label}:tora]"


@dataclass
class Look:
    answer: str = ""
    actions: list[Action] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    model: str = ""
    deep: bool = False
    usage: dict = field(default_factory=dict)     # tokens, summed over every call it took (SN-01)
    sure: bool = True
    path: str = ""                                # "fast", "deep", "fast→deep" (UD-01)
    cost: float = 0.0                             # US dollars, every call it took (UD-06)

    def __bool__(self) -> bool:
        return bool(self.answer)

    def tags(self, limit: int = 4) -> str:
        return "".join(a.as_tag() for a in self.actions[:limit])


_CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "done": {"type": "boolean"},
        "why": {"type": "string", "description": "One short reason."},
    },
    "required": ["done"],
}


async def confirm(question: str, screenshot_b64: str, *, timeout: float = 30.0):
    """Yes/no: is `question` true on the screen right now? None if it failed.

    This is how a guide step whose only completion signal is visual — "the
    photo you picked is highlighted", something no accessibility check can
    see — gets detected. Deliberately cheap (the fast model, thinking off);
    the caller throttles how often it runs.
    """
    if not screenshot_b64 or not question.strip():
        return None
    try:
        data = await gemini.generate_json(
            Models.VISION_FAST,
            [{"role": "user", "parts": [
                gemini.image_part(screenshot_b64),
                gemini.text_part(f"Is this true on the screen right now? {question}"),
            ]}],
            _CONFIRM_SCHEMA,
            system=(
                "You verify whether something is currently true on the user's "
                "screen. Judge only from what is visible. Answer done=true only "
                "when you can actually see it is the case."
            ),
            max_tokens=512,
            think=0,
            timeout=timeout,
        )
    except Exception as e:
        _log.warning("confirm failed: %s", e)
        return None
    if not data:
        return None
    return bool(data.get("done"))


_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "done": {"type": "boolean"},
        "confidence": {"type": "number", "description": "0 to 1."},
        "why": {"type": "string", "description": "One short sentence: the visible evidence."},
    },
    "required": ["done", "confidence", "why"],
}


async def judge_step(instruction: str, before_b64: str, after_b64: str,
                     *, timeout: float = 9.0) -> Optional[dict]:
    """Did the user do `instruction`? Compares the screen BEFORE the step was
    shown with the screen AFTER something changed.

    This replaces guessing an end state in advance. The model is not asked
    "does the screen look like X" — it is shown the actual before and after
    and asked whether the change is the step being done. It only runs when
    the watcher saw a material change, so it is not a poll.
    """
    if not instruction.strip() or not after_b64:
        return None
    parts = []
    if before_b64:
        parts += [gemini.text_part("BEFORE the user acted:"),
                  gemini.image_part(before_b64)]
    parts += [gemini.text_part("AFTER:"), gemini.image_part(after_b64),
              gemini.text_part(
                  f'The user was told to do this step: "{instruction}"\n'
                  "Comparing AFTER with BEFORE, did they do it? Judge only from "
                  "what is visible. done=true needs visible evidence of the "
                  "step's outcome, not merely that something changed. A menu "
                  "that opened, a dialog that appeared, text that was typed, a "
                  "page that loaded — those are outcomes. A tooltip, a hover "
                  "highlight or the cursor moving are not.")]
    try:
        return await gemini.generate_json(
            Models.VISION_FAST,
            [{"role": "user", "parts": parts}],
            _JUDGE_SCHEMA,
            system=("You verify whether a person following a step-by-step "
                    "guide has completed the current step, from two "
                    "screenshots. Be decisive and literal."),
            max_tokens=512,
            think=0,
            timeout=timeout,
        )
    except Exception as e:
        _log.warning("judge_step failed: %s", e)
        return None


def wants_deep(question: str) -> bool:
    """True when the glance should be skipped and the pro model asked directly."""
    q = question or ""
    return bool(_ADVICE_RE.search(q) or _DENSE_RE.search(q))


# Dense reading: here the fast model misreads, so the deep one goes first.
# Everything else - charts, boards, choices, "which one" - starts fast and
# escalates only if the fast look says it is not sure (UD-01).
_READING_RE = re.compile(
    r"\b(code|coding|stack\s*trace|traceback|log\s*file|logs|handwrit\w*|contract|clause|small\s+print|"
    r"fine\s+print|terms\s+and\s+conditions|sheet\s*music|circuit|schematic|proof|equation|formula)\b",
    re.IGNORECASE,
)


# A move in a game is judgement the fast model gets confidently wrong: on
# the UD-01 comparison it answered a mate-in-one with "you are checkmated"
# and said it was sure; the deep model played Ra8#. Every other screen it
# drew right (15 of 16), so only these go deep first.
_GAME_RE = re.compile(
    r"\b(move|moves|play|win|winning|chess|checkmate|mate|board|puzzle|game|sudoku|crossword|poker|"
    r"cards|solitaire|opening)\b",
    re.IGNORECASE,
)


def needs_reading(question: str) -> bool:
    """True when the question is dense reading, which the deep model reads first."""
    return bool(_READING_RE.search(question or ""))


def needs_judgement(question: str) -> bool:
    """True for a what-to-do question about a game - the best move, where to
    play to win - which the deep model answers first."""
    q = question or ""
    return bool(_NEXT_ACTION_RE.search(q) and _GAME_RE.search(q))


def is_next_action(question: str) -> bool:
    """True for "what should I do?" - the best move, the next step, which one
    to pick: the answer is one action, drawn, said in a line."""
    return bool(_NEXT_ACTION_RE.search(question or ""))


def _box(raw) -> Optional[tuple[int, int, int, int]]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = [max(0, min(1000, int(v))) for v in raw]
    except (TypeError, ValueError):
        return None
    if xmax <= xmin or ymax <= ymin:
        return None
    return ymin, xmin, ymax, xmax


def _parse(data: dict, model: str, deep: bool) -> Look:
    actions = []
    for a in data.get("actions", []):
        box = _box(a.get("box"))
        if box is None:
            continue
        kind = (a.get("kind") or "point").strip().lower()
        to_box = _box(a.get("to_box")) if kind in ("move", "arrow") else None
        actions.append(Action(
            label=(a.get("label") or "").strip()[:40] or "here",
            box=box,
            kind=kind if (to_box or kind not in ("move", "arrow")) else "point",
            to_box=to_box,
        ))
    return Look(
        answer=(data.get("answer") or "").strip(),
        actions=actions,
        observations=[o for o in data.get("observations", []) if o],
        model=model,
        deep=deep,
    )


async def _ask(question: str, screenshot_b64: str, *, deep: bool, context: str, timeout: float) -> Optional[Look]:
    """One call to one model. None when it failed or came back unusable."""
    model = Models.VISION_DEEP if deep else Models.VISION_FAST
    ask = f"{context}\n\n{question}" if context else question
    contents = [{"role": "user", "parts": [
        gemini.image_part(screenshot_b64),
        gemini.text_part(ask),
    ]}]
    usage: dict = {}
    try:
        data = await gemini.generate_json(
            model, contents, _SCHEMA,
            system=_SYSTEM,
            max_tokens=4096,
            # The fast look skips its reasoning pass entirely; the deep look
            # gets a bounded budget so it always has room left to answer.
            think=_DEEP_THINK if deep else 0,
            timeout=min(timeout, _DEEP_TIMEOUT_S) if deep else timeout,
            usage=usage,
        )
    except Exception as e:
        _log.warning("%s look failed: %s", "deep" if deep else "fast", e)
        return None
    if not data:
        return None
    out = _parse(data, model, deep)
    out.usage = usage
    out.cost = cost_usd(model, usage)
    out.sure = data.get("sure") is not False
    return out


# US dollars per million tokens, in and out (thinking is billed as out) - the
# same table HeyTora's server bills with (supabase/functions/heytora/rules.ts).
PRICES = {"flash-lite": (0.15, 0.6), "flash": (0.5, 3.0), "pro": (2.5, 15.0)}


def cost_usd(model: str, usage: dict) -> float:
    """What one call cost, from its usageMetadata (UD-06)."""
    m = (model or "").lower()
    pin, pout = PRICES["flash-lite" if "flash-lite" in m else "flash" if "flash" in m else "pro"]
    u = usage or {}
    return (u.get("promptTokenCount", 0) * pin
            + (u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)) * pout) / 1e6


def _summed(*usages: dict) -> dict:
    out: dict = {}
    for u in usages:
        for k, v in (u or {}).items():
            if isinstance(v, (int, float)):
                out[k] = out.get(k, 0) + v
    return out


async def look(
    question: str,
    screenshot_b64: str,
    *,
    deep: Optional[bool] = None,
    context: str = "",
    timeout: float = 90.0,
) -> Look:
    """Look at the screen and answer: fast first, deep only when needed (UD-01).

    The deep model (Pro, with up to 1,536 thinking tokens) costs about 23
    times the fast one and takes 8-20 s to its ~1.4 s. It used to be chosen
    by the question's words - 11 of 20 everyday questions went to it - and
    by the voice's `careful` flag. Now:

      deep=None   fast first; the deep model only when the fast look is
                  not sure, came back unusable, or answered a what-to-do
                  question with nothing to draw - once. Dense reading (code,
                  small print, formulas) and a move in a game go deep first.
      deep=True   the deep model (a fast answer if it fails)
      deep=False  the fast model only
    """
    if not screenshot_b64:
        return Look()

    if deep is True or (deep is None and (needs_reading(question) or needs_judgement(question))):
        first = await _ask(question, screenshot_b64, deep=True, context=context, timeout=timeout)
        if first:
            first.path = "deep"
            return first
        # Was: only an EXCEPTION fell back. A truncated or unparseable reply
        # returned an empty Look and the user heard nothing after 40 s.
        _log.info("deep look gave nothing usable — answering with the fast model")
        fast = await _ask(question, screenshot_b64, deep=False, context=context, timeout=timeout)
        if fast:
            fast.path = "deep→fast"
        return fast or Look()

    fast = await _ask(question, screenshot_b64, deep=False, context=context, timeout=timeout)
    if deep is False:
        if fast:
            fast.path = "fast"
        return fast or Look()
    if fast and fast.answer and fast.sure and not (is_next_action(question) and not fast.actions):
        fast.path = "fast"
        return fast
    why = ("gave nothing usable" if not fast or not fast.answer else
           "was not sure" if not fast.sure else "had nothing to draw")
    _log.info("fast look %s — asking the deep model", why)
    careful = await _ask(question, screenshot_b64, deep=True, context=context, timeout=timeout)
    if careful and careful.answer:
        careful.path = "fast→deep"
        careful.usage = _summed(fast.usage if fast else {}, careful.usage)
        careful.cost += fast.cost if fast else 0.0
        return careful
    if fast and fast.answer:
        fast.path = "fast (deep failed)"
        return fast
    return Look()


# ── Pointing ─────────────────────────────────────────────────────────────────

@dataclass
class Detected:
    """A located element, in LOGICAL screen pixels — the same space as
    QCursor.pos(), which is what the overlay draws in."""
    x: int
    y: int
    screen_index: int
    label: str = ""


_LOCATE_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "label": {"type": "string", "description": "1-3 words naming it."},
        "box": {
            "type": "array",
            "items": {"type": "integer"},
            # Exactly four. Unbounded, a reply can run on past the box until
            # max_tokens: twice on 24 Sep a locate came back as
            # '"box": [964,' and finish=MAX_TOKENS, and the arrow was lost.
            "minItems": 4,
            "maxItems": 4,
            "description": "[ymin,xmin,ymax,xmax] normalized 0-1000.",
        },
    },
    "required": ["found", "label", "box"],
}

_LOCATE_SYSTEM = """You locate one UI element in a screenshot.

Return a TIGHT box around the single element the user is asking about — the
button, link, menu item, icon or field itself, not the panel containing it.
Coordinates are [ymin, xmin, ymax, xmax] normalized 0-1000, where x=0 is the
left edge and y=0 is the top.

Set found false with an empty box when the element is not visible, or when
the question is conceptual and points at nothing in particular. Guessing is
worse than saying no — a wrong arrow sends the user to the wrong place."""


async def locate(
    question: str,
    *,
    hint: str = "",
    screenshot_jpeg_b64: str,
    original_width: int,
    original_height: int,
    screen_index: int = 1,
    physical_width: Optional[int] = None,
    physical_height: Optional[int] = None,
    physical_left: int = 0,
    physical_top: int = 0,
    dpi_scale: float = 1.0,
    timeout: float = 30.0,
) -> Optional[Detected]:
    """Find the element `question` is about.

    One call. The model returns the box directly — Gemini 3 grounds boxes
    natively, so the old two-pass trick of drawing a numbered grid over the
    screenshot and asking which cell, twice, is gone. That was two round
    trips and two image encodings for a worse answer.
    """
    if not screenshot_jpeg_b64:
        return None

    try:
        data = await gemini.generate_json(
            Models.VISION_FAST,
            [{"role": "user", "parts": [
                gemini.image_part(screenshot_jpeg_b64),
                gemini.text_part(f'Where is: "{question}"' + (
                    f'\nIt is for this step: "{hint}"' if hint else "")),
            ]}],
            _LOCATE_SCHEMA,
            system=_LOCATE_SYSTEM,
            # Room for the answer even if the model deliberates first: at
            # 1024 two replies were cut off right at the box (24 Sep).
            max_tokens=2048,
            think=0,
            timeout=timeout,
        )
    except Exception as e:
        _log.warning("locate failed: %s", e)
        return None

    if not data or not data.get("found"):
        return None
    box = data.get("box") or []
    if len(box) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = [max(0, min(1000, int(v))) for v in box]
    except (TypeError, ValueError):
        return None
    if xmax <= xmin or ymax <= ymin:
        return None

    # normalized → physical monitor px → logical screen px
    if physical_width is None:
        physical_width = original_width
    if physical_height is None:
        physical_height = original_height

    # Scaled from the MONITOR's corner, which Qt keeps where Windows has it
    # (screen/monitors.py) - not from the desktop's origin, which put the
    # point on the wrong spot, or monitor, anywhere but the main screen.
    s = dpi_scale if dpi_scale > 0 else 1.0
    lx = physical_left + ((xmin + xmax) / 2000.0) * physical_width / s
    ly = physical_top + ((ymin + ymax) / 2000.0) * physical_height / s

    return Detected(
        x=int(round(lx)),
        y=int(round(ly)),
        screen_index=screen_index,
        label=(data.get("label") or "").strip()[:40],
    )
