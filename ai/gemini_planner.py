"""
The guide planner — gemini-3.1-pro-preview.

When the user says "show me how to export this" the live model does not try
to improvise a five-step tutorial mid-sentence. It calls plan_guide, and this
breaks the goal into ordered steps, each with the thing on screen to aim at
and the condition that means the step is finished.

It is deliberate rather than quick — the pro model thinks, and on a real goal
with a screenshot attached that has been measured between about ten and
twenty seconds. That is why the tool description tells the live model to say
"let me work that out" before calling it: the user hears something immediately
and the silence is explained.

The steps come back structured, so "next" can be answered from memory without
another model call, and each step knows what it was pointing at.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ai import gemini
from ai.gemini import Models

_log = logging.getLogger("tora.planner")

_SCHEMA = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "feasible": {
            "type": "boolean",
            "description": "False if this cannot be done from what is on screen.",
        },
        "preamble": {
            "type": "string",
            "description": "One sentence to say before step one. No list.",
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "What to do, one or two sentences, spoken aloud.",
                    },
                    "target": {
                        "type": "string",
                        "description": "The UI element this step acts on, or '' if none.",
                    },
                    "done_when": {
                        "type": "string",
                        "description": "How the user knows this step worked.",
                    },
                    "possible_actions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Two or three DIFFERENT ways a user might do this "
                            "step (click the icon, a keyboard shortcut, the "
                            "menu). Used to help when someone gets stuck."
                        ),
                    },
                    "expected_state": {
                        "type": "object",
                        "description": (
                            "The OUTCOME of this step, as observable signals. "
                            "Fill in every one that will be true afterwards — "
                            "ANY of them is accepted as success, so the user "
                            "can reach it their own way (clicking Save or "
                            "pressing Ctrl+S both count). Leave a field empty "
                            "if it does not apply. Prefer process and "
                            "window_title: they describe the result, not the "
                            "gesture."
                        ),
                        "properties": {
                            "process": {
                                "type": "string",
                                "description": "Executable that should then be running/frontmost, e.g. explorer.exe, notepad.exe.",
                            },
                            "window_title": {
                                "type": "string",
                                "description": "A distinctive word from the resulting window title.",
                            },
                            "control_appears": {"type": "string"},
                            "control_gone": {"type": "string"},
                            "focused": {"type": "string"},
                            "url_contains": {"type": "string"},
                            "visual": {
                                "type": "string",
                                "description": "Only if nothing above applies: what a person would SEE.",
                            },
                        },
                    },
                    "completion": {
                        "type": "object",
                        "description": (
                            "A machine-checkable sign the step is done, so the "
                            "tutor can tell WITHOUT asking the user. Pick the "
                            "single most reliable observable."
                        ),
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "window_title", "control_appears",
                                    "control_gone", "focused", "url_contains",
                                    "visual", "none",
                                ],
                                "description": (
                                    "window_title: the active window's title "
                                    "will contain `value`. control_appears / "
                                    "control_gone: a control named `value` "
                                    "shows up / disappears. focused: the "
                                    "control named `value` gets keyboard "
                                    "focus. url_contains: the browser address "
                                    "bar will contain `value`. visual: only a "
                                    "look at the screen can tell (describe it "
                                    "in `value`). none: nothing observable."
                                ),
                            },
                            "value": {
                                "type": "string",
                                "description": "The title/name/url fragment or visual description.",
                            },
                            "control_type": {
                                "type": "string",
                                "description": "Optional control type for 'focused', e.g. 'edit'.",
                            },
                        },
                        "required": ["kind", "value"],
                    },
                },
                "required": [
                    "instruction", "target", "done_when",
                    "possible_actions", "expected_state", "completion",
                ],
            },
        },
    },
    "required": ["goal", "feasible", "preamble", "steps"],
}

_SYSTEM = """You plan hands-on computer tutorials for a voice tutor that
speaks one step at a time and points at things on screen.

Rules:
  • Each step is ONE action the user performs. Never merge two clicks.
  • Write instructions to be SPOKEN: no markdown, no numbering, no "click
    on the button labelled" padding. "Open the File menu." is a step.
  • target names a real UI element visible on screen when the step begins,
    exactly as it is labelled. Use "" when the step has no on-screen target.
  • done_when is what the user will SEE when the step worked, so the tutor
    can confirm before moving on.
  • completion is how a PROGRAM detects the step is done, so the tutor can
    advance on its own without the user saying "next". Prefer, in order:
    window_title (a new window/page opened), url_contains (a browser
    navigated), focused (a field got focus), control_appears / control_gone
    (a panel or dialog opened or closed). Use `visual` only when none of
    those apply, and `none` only for a final "you're done" step. The value
    must be text that will really be observable — a distinctive word from the
    resulting title, not a whole sentence.
  • expected_state is the heart of it: describe the CHANGE, not the click and
    not the room the user is already standing in. Every signal you give must
    be FALSE at the moment the step is spoken and TRUE once it is done. The
    tutor watches these signals to advance on its own, and a signal that is
    already true tells it nothing — it cannot separate "the user did it" from
    "it always looked like that", so that step will sit there.
    So: do NOT name the app the user is already in, the window they are
    already looking at, or a control already on screen. If the user is in
    Chrome, "process: chrome.exe" is worthless on every step; name the NEW
    thing — the dialog that opens, the field that takes focus, the url the
    address bar navigates to, the panel that disappears.
    Fill in every signal that will newly become true, because any one of them
    is accepted — that is what lets someone press Ctrl+S instead of clicking
    Save and still be counted as successful.
  • possible_actions lists two or three genuinely different routes to the
    same outcome, so the tutor can offer another way if someone gets stuck.
  • Between three and seven steps. If it truly needs more, group them.
  • If the goal cannot be done from this screen, set feasible false, leave
    steps empty, and say why in preamble."""


@dataclass
class StepCondition:
    """A machine-checkable sign a step is done. Evaluated by ai.guide_state.

    kind is one of: window_title, control_appears, control_gone, focused,
    url_contains, visual, none. value is the text to look for; control_type
    optionally refines 'focused'. This is what lets the guide advance on its
    own (M4) instead of waiting for the user to say "next".
    """
    kind: str = "none"
    value: str = ""
    control_type: str = ""

    @property
    def checkable(self) -> bool:
        """True when a program can decide this without looking at pixels."""
        return self.kind in (
            "window_title", "control_appears", "control_gone",
            "focused", "url_contains",
        ) and bool(self.value)


@dataclass
class Step:
    instruction: str
    target: str = ""
    done_when: str = ""
    completion: StepCondition = None
    # Several independent signals that each mean this step succeeded, so the
    # user can reach the outcome their own way. See ai.verification.
    expected: object = None
    possible_actions: list = None

    def __post_init__(self):
        if self.completion is None:
            self.completion = StepCondition()
        if self.possible_actions is None:
            self.possible_actions = []
        if self.expected is None:
            from ai.verification import Expectation
            self.expected = Expectation()
        # A plan from an older schema (or a model that skipped the field) gets
        # its single condition promoted into the multi-signal form, so
        # verification has exactly one shape to deal with.
        if not self.expected and self.completion and self.completion.value:
            from ai.verification import Expectation
            kind, value = self.completion.kind, self.completion.value
            if kind in ("window_title", "control_appears", "control_gone",
                        "focused", "url_contains", "visual"):
                self.expected = Expectation(**{kind: value})

    def spoken(self, index: int, total: int) -> str:
        line = f"Step {index + 1} of {total}: {self.instruction}"
        if self.done_when:
            line += f" You'll know it worked when {self.done_when[0].lower()}{self.done_when[1:]}"
        return line


@dataclass
class Guide:
    goal: str
    preamble: str = ""
    steps: list[Step] = None
    feasible: bool = True

    def __post_init__(self):
        if self.steps is None:
            self.steps = []

    def __bool__(self) -> bool:
        return self.feasible and bool(self.steps)


_VALID_KINDS = {
    "window_title", "control_appears", "control_gone", "focused",
    "url_contains", "visual", "none",
}


def _parse_expected(raw):
    from ai.verification import Expectation
    if not isinstance(raw, dict):
        return Expectation()
    def g(k):
        return str(raw.get(k) or "").strip()
    return Expectation(
        process=g("process"),
        window_title=g("window_title"),
        control_appears=g("control_appears"),
        control_gone=g("control_gone"),
        focused=g("focused"),
        url_contains=g("url_contains"),
        visual=g("visual"),
    )


def _parse_condition(raw) -> StepCondition:
    if not isinstance(raw, dict):
        return StepCondition()
    kind = str(raw.get("kind") or "none").strip().lower()
    if kind not in _VALID_KINDS:
        kind = "none"
    return StepCondition(
        kind=kind,
        value=str(raw.get("value") or "").strip(),
        control_type=str(raw.get("control_type") or "").strip().lower(),
    )


async def plan(
    goal: str,
    *,
    screenshot_b64: str = "",
    window_title: str = "",
    timeout: float = 60.0,
    fast: bool = True,
) -> Guide:
    """Break `goal` into steps. Returns an empty Guide when it cannot.

    fast=True (the default) plans with the quick vision model and no thinking
    pass. That is a deliberate trade, measured: the pro model took **17.6 s**
    on a real goal while the fast one took 3.8 s and produced MORE usable
    steps (4 granular ones vs 2 coarse), with equally valid completion
    conditions. Seventeen seconds of silence before a guide starts is not a
    better guide — it is a broken one. The pro model stays available via
    fast=False as the fallback when the quick plan comes back unusable.
    """
    if not goal.strip():
        return Guide(goal=goal, feasible=False)

    parts: list[dict] = []
    if screenshot_b64:
        parts.append(gemini.image_part(screenshot_b64))
    ask = f"Goal: {goal}"
    if window_title:
        ask += f'\nThe user is currently in: "{window_title}"'
    # Naming the starting state is what stops the planner describing it back
    # as the "outcome" of step one — the defect that made every first step
    # look finished before the user had touched anything.
    try:
        from tutor import active_process_name
        proc = active_process_name() or ""
        if proc:
            ask += (f"\nThe foreground process is already {proc} and that "
                    f"window is already open — those facts are NOT outcomes "
                    f"of any step, so do not use them as signals.")
    except Exception:
        _log.debug("could not read the foreground process", exc_info=True)
    parts.append(gemini.text_part(ask))

    try:
        data = await gemini.generate_json(
            Models.VISION_FAST if fast else Models.PLANNER,
            [{"role": "user", "parts": parts}],
            _SCHEMA,
            system=_SYSTEM,
            max_tokens=6144,
            # Unbounded pro thinking starved the plan itself (see
            # gemini_vision._DEEP_THINK for the same defect and fix).
            think=0 if fast else 2048,
            timeout=timeout,
        )
    except Exception as e:
        # str(TimeoutError()) is '' — the log said 'planner failed: ' and
        # nothing else. Name the type.
        _log.warning("planner failed: %s %s", type(e).__name__, e)
        return Guide(goal=goal, feasible=False)

    if not data:
        return Guide(goal=goal, feasible=False)

    return Guide(
        goal=data.get("goal") or goal,
        preamble=data.get("preamble", ""),
        feasible=bool(data.get("feasible", True)),
        steps=[
            Step(
                instruction=s.get("instruction", ""),
                target=s.get("target", ""),
                done_when=s.get("done_when", ""),
                completion=_parse_condition(s.get("completion")),
                expected=_parse_expected(s.get("expected_state")),
                possible_actions=[
                    a for a in (s.get("possible_actions") or []) if a
                ][:4],
            )
            for s in data.get("steps", [])
            if s.get("instruction")
        ],
    )
