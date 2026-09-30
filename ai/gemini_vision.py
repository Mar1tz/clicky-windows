"""
Two ways of looking at the screen.

  fast  gemini-3.8-flash with thinking switched off. The cheap glance, and
        the first pass on any ordinary screen question. ~1.4 s.

  deep  gemini-3.1-pro-preview. The careful look: the model that can read a
        chessboard, a dense chart, a page of code, handwriting. Slower and
        worth it. Advice-shaped questions — "what's the best move", "which
        of these should I pick", "how do I…" — skip the glance and come
        straight here, because a fast answer to that kind of question is
        usually a wrong one.

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

_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "The spoken answer. At most 2 short sentences, no markdown.",
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
                        "description": "point | circle | rect | arrow",
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
    },
    "required": ["answer", "actions"],
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

  • answer is SPOKEN aloud: 1-3 plain sentences, no markdown, no lists.
  • Describe only what is genuinely in the image. If you cannot see the thing
    being asked about, say so plainly rather than guessing.
  • Put every element the user needs to look at in actions, with a tight box
    around it. Order them the way the user should look at them.
  • observations are the specific things you read off the screen that your
    answer depends on — piece positions, axis values, the exact error text.
    Be concrete; this is what lets the answer be checked."""


@dataclass
class Action:
    label: str
    box: tuple[int, int, int, int]   # ymin, xmin, ymax, xmax — 0-1000
    kind: str = "point"

    @property
    def center(self) -> tuple[int, int]:
        """(x, y) normalized 0-1000 — what an arrow aims at."""
        ymin, xmin, ymax, xmax = self.box
        return (xmin + xmax) // 2, (ymin + ymax) // 2

    @property
    def radius(self) -> int:
        """Ring radius that comfortably contains the box."""
        ymin, xmin, ymax, xmax = self.box
        return max(20, int(max(xmax - xmin, ymax - ymin) * 0.62))

    def as_tag(self) -> str:
        """The drawing tag the overlay already knows how to render."""
        x, y = self.center
        if self.kind == "rect":
            ymin, xmin, ymax, xmax = self.box
            return f"[RECT:{xmin},{ymin},{xmax},{ymax}:blue]"
        if self.kind == "underline":
            ymin, xmin, ymax, xmax = self.box
            return f"[UNDERLINE:{xmin},{ymax},{xmax - xmin}:blue]"
        return f"[CIRCLE:{x},{y},{self.radius}:{self.label}:blue]"


@dataclass
class Look:
    answer: str = ""
    actions: list[Action] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    model: str = ""
    deep: bool = False

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


def _parse(data: dict, model: str, deep: bool) -> Look:
    actions = []
    for a in data.get("actions", []):
        box = a.get("box") or []
        if len(box) != 4:
            continue
        try:
            vals = [max(0, min(1000, int(v))) for v in box]
        except (TypeError, ValueError):
            continue
        ymin, xmin, ymax, xmax = vals
        if xmax <= xmin or ymax <= ymin:
            continue
        actions.append(Action(
            label=(a.get("label") or "").strip()[:40] or "here",
            box=(ymin, xmin, ymax, xmax),
            kind=(a.get("kind") or "point").strip().lower(),
        ))
    return Look(
        answer=(data.get("answer") or "").strip(),
        actions=actions,
        observations=[o for o in data.get("observations", []) if o],
        model=model,
        deep=deep,
    )


async def look(
    question: str,
    screenshot_b64: str,
    *,
    deep: Optional[bool] = None,
    context: str = "",
    timeout: float = 90.0,
) -> Look:
    """Look at the screen and answer.

    deep=None picks the model from the question; pass True or False to force
    one. A deep look that fails falls back to the fast one rather than
    returning nothing — a quick answer beats an error.
    """
    if not screenshot_b64:
        return Look()

    use_deep = wants_deep(question) if deep is None else deep
    model = Models.VISION_DEEP if use_deep else Models.VISION_FAST

    ask = question
    if context:
        ask = f"{context}\n\n{question}"
    contents = [{"role": "user", "parts": [
        gemini.image_part(screenshot_b64),
        gemini.text_part(ask),
    ]}]

    data = None
    try:
        data = await gemini.generate_json(
            model, contents, _SCHEMA,
            system=_SYSTEM,
            max_tokens=4096,
            # The glance skips its reasoning pass entirely; the deep look
            # gets a bounded budget so it always has room left to answer.
            think=_DEEP_THINK if use_deep else 0,
            timeout=min(timeout, _DEEP_TIMEOUT_S) if use_deep else timeout,
        )
    except Exception as e:
        _log.warning("%s look failed: %s", "deep" if use_deep else "fast", e)

    if data is None and use_deep:
        # Was: only an EXCEPTION fell back. A truncated or unparseable reply
        # returned an empty Look and the user heard nothing after 40 s.
        _log.info("deep look gave nothing usable — answering with the fast model")
        return await look(
            question, screenshot_b64, deep=False,
            context=context, timeout=timeout,
        )
    return _parse(data, model, use_deep) if data else Look()


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
