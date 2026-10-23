"""
Central state machine for Tora Windows.

Two routes through a question, picked per turn:

  REALTIME (default)
      hotkey → screenshot + context pushed to an already-open websocket
      → mic audio streams up while the user is still talking
      → gemini-3.8-live answers in its own voice, calling Tora's tools
        (point, draw, look closer, plan a guide, search, recall) as it goes

  CASCADE (fallback)
      hotkey → record → gemini-3.5-transcribe-live → screen capture
      → web search + pointing in parallel → gemini-3.8-flash or
        gemini-3.1-pro-preview → gemini-3.1-flash-tts-preview

The cascade is what runs when the websocket cannot be opened, when the user
pins TORA_CONVERSATION=cascade, and for the replies that never had audio in
them to begin with. Everything downstream of the answer — drawings, journal,
lesson steps, collab, recording — is shared by both.
"""

import asyncio
import contextvars
import logging
import math
import os
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from config import cfg
from ai.base_provider import BaseLLMProvider, Message
from ai import gemini, gemini_memory, gemini_planner, gemini_vision
from ai.gemini import Models
from audio.ambient_listener import AmbientListener
from screen import monitors
from screen.capture import capture_all_screens, capture_region, grab_frame, monitor_frames
from ai import convert_pdf, email_reply, look_board, look_chess, look_region, net_quality, screen_files, speed, trace as tracing, trace_read, write_for_me
from ui.panel import AppState
from tutor import (
    active_window_title, app_key,
    is_locate, is_multistep, is_next, is_stop, is_sensitive_window,
    is_repeat, is_journal_today, is_journal_week, is_quiz_review,
    is_identity_question, is_resume,
)
from tutor_features import (
    journal, pdf_context, ocr, code_mode, lesson_recorder,
    workflow_capture, collab,
)
import skills as skills_pkg

_log = logging.getLogger("tora.manager")

# Which question the code running now is answering (0: none in particular).
# Set by the slow route for its own task, so a state change it makes after
# the user has asked something new is ignored (CompanionManager._emit_state).
_ANSWERING: contextvars.ContextVar[int] = contextvars.ContextVar("tora_answering", default=0)

# How many turns the screen context may say "same screen" before it is
# spelled out again. Context compression can drop the turn that carried
# the control list, so it is repeated often enough to survive that.
CONTEXT_REPEAT_TURNS = 6


def _finish_kind(done_by: str) -> str:
    """Which signal finished a step, from the watcher's done_by text.

    "click on target" -> click; "vision: …" -> vision;
    "expectation: typed_text (…)" -> typed_text. The last used to be counted
    as just "expectation", which hid whether a step finished on a planner
    signal, on the typed text or on its target disappearing - the thing
    GW-11 exists to measure.
    """
    text = (done_by or "").strip()
    if not text:
        return "unknown"
    if text.startswith("click"):
        return "click"
    head, _, rest = text.partition(":")
    if head.strip() == "expectation" and rest.strip():
        return rest.strip().split(" (")[0].split()[0]
    return head.strip() or "unknown"


def _duration(seconds: float) -> str:
    """42 s · 1 min 42 s · 1 h 5 min - how long a walkthrough took."""
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s" if s % 60 else f"{s // 60} min"
    return f"{s // 3600} h {s % 3600 // 60} min"


def _walkthrough_summary(goal: str, steps: int, seconds: float) -> str:
    """The one line a finished walkthrough leaves on screen."""
    goal = " ".join(str(goal or "").split()).rstrip(".") or "Walkthrough"
    return (f"{goal[0].upper()}{goal[1:]} · {steps} step{'s' if steps != 1 else ''}"
            f" · {_duration(seconds)}")


# How long after a walkthrough finishes the live model is still told about
# it - "what was the second step again?" comes right after the end, and the
# steps were never in its conversation (they are Tora's own voice).
FINISHED_CONTEXT_S = 300.0


_TYPING_VERBS = ("type", "enter ", "write", "paste", "fill in", "input")
_SUBMIT_WORDS = ("press enter", "hit enter", "press return", "hit return",
                 "and submit", "then submit")


def _is_typing_step(instruction: str) -> bool:
    """A step that asks for text and does not also ask to submit it."""
    low = " ".join(str(instruction or "").lower().split())
    if not low.startswith(_TYPING_VERBS):
        return False
    return not any(w in low for w in _SUBMIT_WORDS)


class _VisualCond:
    """Adapts an Expectation's `visual` text to the (kind, value) shape
    _visual_done already understands."""
    kind = "visual"
    control_type = ""

    def __init__(self, value: str):
        self.value = value


def _build_system_prompt(
    window_title: str = "",
    lesson_step: int = 0,
    total_steps: int = 0,
    quiz_mode: bool = False,
    detected_coord: Optional[tuple] = None,
    code_active: bool = False,
    language_code: str = "en",
    extra: str = "",
    realtime: bool = False,
) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    ctx_lines = [f"TODAY'S DATE: {today}."]
    if window_title:
        ctx_lines.append(f'ACTIVE WINDOW: "{window_title}"')
    if detected_coord:
        x, y, label = detected_coord
        ctx_lines.append(
            f"DETECTED ELEMENT (pre-computed by the pointing engine — use "
            f"this coordinate verbatim in your [POINT] tag): x={x}, y={y}, "
            f"label='{label}'. (Already normalized 0-1000.)"
        )
    if total_steps > 1:
        ctx_lines.append(
            f"LESSON PROGRESS: step {lesson_step + 1} of {total_steps}. "
            "Explain ONLY this step, then end with \"Say 'next' when ready.\""
        )

    # ── Quiz mode: dominant prompt that completely replaces normal behaviour ──
    if quiz_mode:
        return f"""You are Tora, an interactive QUIZ TUTOR. The user has
turned on Quiz Mode and wants to be tested, NOT explained to.

{chr(10).join(ctx_lines)}

ABSOLUTE QUIZ RULES (override everything else):
  • NEVER answer the user's question directly. NEVER point at UI elements.
    NEVER emit [POINT:...] tags. NEVER explain how things work.
  • If the user is greeting / starting ("hello", "what's on my screen", "begin",
    "quiz me", anything), START the quiz: ask ONE short, specific question
    about what's visible on screen — name a button, recognise an icon, predict
    what a click would do, identify the active app, etc.
  • If the user's last message looks like an ANSWER (a noun, a short phrase, a
    yes/no), evaluate it in ≤1 sentence ("Correct!" / "Close — actually..."),
    then immediately ask the NEXT question.
  • Questions should be progressively harder. Vary topic across UI literacy,
    keyboard shortcuts, what's currently visible, predicting outcomes.
  • Keep it warm and encouraging. Never lecture.
  • Format every turn as:  <one-line evaluation if applicable>  <one question>

STYLE: short, friendly, never more than 2 sentences. End every turn with a
question mark."""

    from config import _REALTIME_RULES, _TECHNICAL_RULES
    base = cfg.system_prompt.strip()
    # The user's own standing instructions, added to her prompt - never in
    # place of it (config._instructions_from_env).
    own = (cfg.custom_instructions or "").strip()
    if own:
        base += "\n\nThe user's own standing instructions - follow them on every answer:\n" + own
    # Who the user is, from the first launch: their name, and what they use
    # the PC for - so answers start from their world.
    name, uses = cfg.profile()
    if name or uses:
        about = []
        if name:
            about.append(f"The user's name is {name}; use it now and then, naturally.")
        if uses:
            about.append("They mainly use this PC for: " + ", ".join(uses) + ".")
        ctx_lines = [*ctx_lines, " ".join(about)]
    base = base.replace("{{CONTEXT}}", chr(10).join(ctx_lines))
    base = base.replace("{{TODAY}}", today)
    # The realtime model calls tools; the cascade writes tags into its text.
    # Giving either one the other's rules produces an answer full of literal
    # [ARROW:...] read aloud, or a tool the model was never told it has.
    rules = _REALTIME_RULES if realtime else _TECHNICAL_RULES
    return base + rules + _code_addendum(code_active) + extra


def _plan_words(detail: str) -> str:
    """The server's own sentence about a used-up allowance, without the
    "Gemini 402 [model]:" an error string starts with."""
    text = (detail or "").strip()
    if text.lower().startswith("gemini ") and ": " in text:
        text = text.split(": ", 1)[1]
    return text


def _code_addendum(active: bool) -> str:
    if not active:
        return ""
    from tutor_features.code_mode import code_system_prompt_addendum
    return code_system_prompt_addendum()


_ACT_RE = re.compile(
    r"\b(?:click|press|tap|select|choose|pick|hit|toggle|open|use|type\s+[^.]{1,40}?\s+(?:in|into))\s+"
    r"(?:on\s+)?(?:the\s+)?(?P<t>[^.,;:!?]{2,60})", re.IGNORECASE)
_ACT_END = re.compile(r"(?:(?<!next)\s+to|\s+(?:and|for|so|which|because|then|button|link|icon|tab|option))\b.*$",
                      re.IGNORECASE)


def _norm_question(q: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (q or "").lower()).split())


def _target_from_answer(said: str) -> str:
    """What an answer tells them to act on: "Click Checkout to pay" ->
    "Checkout", "Pick the Arctic Fan for $39" -> "Arctic Fan". "" when it
    names nothing to act on - then the safety net does not point (UD-04)."""
    m = _ACT_RE.search(said or "")
    if not m:
        return ""
    t = _ACT_END.sub("", m.group("t")).strip(" \"'")
    words = t.split()
    return " ".join(words[:5]) if words else ""


def _guess_label(transcript: str) -> str:
    """Extract a 1-3 word label from a locate query for the speech bubble.
       'where is the search bar' → 'search bar' """
    t = transcript.lower().strip().rstrip("?.!")
    for kw in ("where is the ", "where's the ", "show me the ",
              "find the ", "locate the ", "click the ", "click on the ",
              "how do i click ", "how do i find ", "how do i open ",
              "point at the ", "point to the ", "highlight the "):
        if kw in t:
            tail = t.split(kw, 1)[1]
            words = tail.split()
            return " ".join(words[:3]) or "here"
    return "right here!"


def _speakable(text: str) -> str:
    """Make LLM text safe for TTS: models emit LaTeX ("\\( a \\)",
    "\\[ a^2 + b^2 = c^2 \\]") and markdown that edge-tts reads aloud
    verbatim as gibberish. Convert to spoken math / plain words."""
    t = text
    t = re.sub(r'\\(?:left|right)\b', '', t)
    t = re.sub(r'\\sqrt\s*\{([^{}]*)\}', r'the square root of \1', t)
    t = re.sub(r'\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}', r'\1 over \2', t)
    t = (t.replace('\\times', ' times ').replace('\\cdot', ' times ')
          .replace('\\pi', ' pi ').replace('\\theta', ' theta ')
          .replace('\\alpha', ' alpha ').replace('\\beta', ' beta '))
    t = re.sub(r'\\[\[\(\]\)]', '', t)              # \( \) \[ \] delimiters
    t = re.sub(r'\^\s*\{?2\}?', ' squared', t)
    t = re.sub(r'\^\s*\{?3\}?', ' cubed', t)
    t = re.sub(r'\^\s*\{?(\d+)\}?', r' to the power \1', t)
    t = t.replace('²', ' squared').replace('³', ' cubed')
    t = re.sub(r'\\[a-zA-Z]+', ' ', t)              # any leftover \commands
    t = re.sub(r'[{}]', '', t)
    t = re.sub(r'[*_#`]+', '', t)                   # markdown emphasis/headers
    t = re.sub(r'\s+', ' ', t).strip()
    return t


_FILES_RE = re.compile(r"\b(this|these|that|those|the|it|them|selected|open)\b.{0,40}\b(file|files|document|"
                       r"documents|doc|docx|pdf|spreadsheet|workbook|sheet|presentation|deck|photo|photos|"
                       r"image|images|picture|pictures|folder)\b|\b(this|these|it|them)\b", re.IGNORECASE)


def _sound_devices() -> tuple[str, str]:
    """(speaker, microphone) as PortAudio sees the defaults now - for the log."""
    try:
        import sounddevice as sd
        return (sd.query_devices(kind="output")["name"], sd.query_devices(kind="input")["name"])
    except Exception:  # noqa: BLE001
        return ("?", "?")


def _files_meant(trace_readings, goal: str) -> list[str]:
    """The files a job is about, as paths: what they traced over this
    question, else - when the words point ("this document", "these files") -
    what is selected in File Explorer or open in Office (ai/screen_files)."""
    traced = [f for r in (trace_readings or []) for f in getattr(r, "files", [])]
    if traced:
        return list(dict.fromkeys(traced))
    if not _FILES_RE.search(goal or ""):
        return []
    try:
        return screen_files.in_view()
    except Exception:  # noqa: BLE001 - the job still runs, without paths
        return []


def _prefetched(gen):
    """(pieces, task): `gen` read ahead into a queue from now on, so a
    stream starts while something else gets ready - an email reply is
    written while Gmail opens its reply box. Cancel the task to stop it."""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for piece in gen:
                await queue.put(piece)
        finally:
            queue.put_nowait(None)

    task = asyncio.ensure_future(pump())

    async def pieces():
        while (piece := await queue.get()) is not None:
            yield piece
    return pieces(), task


def _one_line(text: str, limit: int = 240) -> str:
    """What a screen look hands the voice to say: its first sentence (and a
    second when the first is only a few words), plain, never a list - see
    docs/SHOW_THE_NEXT_MOVE.md, SN-02."""
    t = re.sub(r"(?m)^\s*(?:[-*•]+|\d+[.)])\s+", "", text or "")
    t = re.sub(r"[*_#`•]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    parts = re.split(r"(?<=[.!?])\s+", t)
    out = parts[0]
    if len(out) < 40 and len(parts) > 1:
        out = f"{out} {parts[1]}"
    return out if len(out) <= limit else out[:limit].rsplit(" ", 1)[0] + "…"


POINT_RE = re.compile(r'\[POINT:(\d+),(\d+):([^:\]]+):screen(\d+)\]')
# A partial "[POINT..." prefix that hasn't closed yet — hold it back from display
# until the next chunk so we never leak a half tag.
POINT_PARTIAL_RE = re.compile(r'\[(?:P|PO|POI|POIN|POINT|POINT:[^\]]*)?$')

# ── Teaching / drawing tags ──────────────────────────────────────────────────
# ALL coordinates are normalized 0-1000 relative to the screenshot the model
# saw (x: 0=left edge, 1000=right edge; y: 0=top, 1000=bottom). The manager
# converts to logical screen pixels via _denorm(). Trailing :color is optional
# on every shape: without one a shape is "tora" - the colour the user picked
# for her cursor (ui/overlay.annotation_colour).
_C = r'(?::([a-z]+))?'                       # optional trailing color group
LINE_RE      = re.compile(r'\[LINE:(\d+),(\d+)->(\d+),(\d+)' + _C + r'\]')
ARROW_RE     = re.compile(r'\[ARROW:(\d+),(\d+)->(\d+),(\d+)' + _C + r'\]')
CIRCLE_RE    = re.compile(r'\[CIRCLE:(\d+),(\d+),(\d+)(?::([^:\]]*))?' + _C + r'\]')
RECT_RE      = re.compile(r'\[RECT:(\d+),(\d+),(\d+),(\d+)' + _C + r'\]')
POLY_RE      = re.compile(r'\[POLY:((?:\d+,\d+[ ]*)+)' + _C + r'\]')
TEXT_RE      = re.compile(r'\[TEXT:(\d+),(\d+):([^:\]]+)' + _C + r'(?::(s|m|l))?\]')
ANGLE_RE     = re.compile(r'\[ANGLE:(\d+),(\d+),(\d+)(?:,(-?\d+))?' + _C + r'\]')
UNDERLINE_RE = re.compile(r'\[UNDERLINE:(\d+),(\d+),(\d+)' + _C + r'\]')
LABEL_RE     = re.compile(r'\[LABEL:(\d+),(\d+):([^:\]]+)' + _C + r'\]')
CLEAR_RE     = re.compile(r'\[CLEAR\]')
# Anchor forms — element resolved by name via the hybrid pointer (UIA), so
# the model never guesses coordinates for real UI: [CIRCLE:@Save button]
CIRCLE_AT_RE    = re.compile(r'\[CIRCLE:@([^:\]]+?)' + _C + r'\]')
UNDERLINE_AT_RE = re.compile(r'\[UNDERLINE:@([^:\]]+?)' + _C + r'\]')

ANY_TAG_RE   = re.compile(
    r'\[(?:POINT|ARROW|CIRCLE|UNDERLINE|LABEL|LINE|RECT|POLY|TEXT|ANGLE|CLEAR)'
    r'(?::[^\]]*)?\]'
)
ANY_PARTIAL_RE = re.compile(r'\[[A-Z]{0,9}(?::[^\]]*)?$')

# Questions that ask Tora to locate / click UI elements — these are what
# put the pointing pipeline in front of the answer.
POINT_TRIGGER_RE = re.compile(
    r"\b(where\s+(is|do|can)|how\s+do\s+i\s+(click|find|open|access|use)|"
    r"point\s+(at|to)|show\s+me\s+(the|where)|click\s+(the|on)|find\s+the)\b",
    re.IGNORECASE,
)


def _retrieve(task) -> None:
    """Mark a finished background task's outcome as seen: a lookup that lost
    a race, or whose step ended, has nobody left to read its exception."""
    if not task.cancelled():
        task.exception()


class CompanionManager(QObject):
    """Thread-safe signals for Qt UI updates from async/audio threads."""

    sig_state_changed       = pyqtSignal(object)          # AppState
    sig_response_chunk      = pyqtSignal(str)
    sig_response_done       = pyqtSignal(str)
    sig_audio_level         = pyqtSignal(float)
    sig_point_at            = pyqtSignal(float, float, str)
    sig_point_hold          = pyqtSignal(bool)            # True → dwell forever until release
    sig_point_release       = pyqtSignal()                # end dwell + fly buddy back
    # A walkthrough step's arrow (LG-02): quick, and held until the step changes.
    sig_guide_point         = pyqtSignal(float, float, str)
    sig_guide_release       = pyqtSignal()
    sig_error               = pyqtSignal(str)
    sig_models_refreshed    = pyqtSignal(str, int)        # (provider, count)
    sig_arrow               = pyqtSignal(float, float, float, float)
    sig_circle              = pyqtSignal(float, float, float)
    sig_underline           = pyqtSignal(float, float, float)
    sig_label               = pyqtSignal(float, float, str)
    sig_draw                = pyqtSignal(dict)            # generic teaching shape → overlay
    sig_clear_drawings      = pyqtSignal()                # wipe all teaching shapes
    sig_recording_state     = pyqtSignal(bool, str)       # (is_recording, output_dir)
    sig_guide_progress      = pyqtSignal(int, int, str)   # (current, total, instruction)
    sig_activity            = pyqtSignal(str, str)        # (state_key, human message)
    sig_agent_event         = pyqtSignal(object)          # agents.core.Event → Active Agents UI
    sig_agent_notice        = pyqtSignal(str, str)        # (title, body) → tray toast
    sig_agents_ready        = pyqtSignal()                # workforce built (or failed); wire the UI now
    sig_account_problem     = pyqtSignal(str, str)        # (kind, plain words); ("", "") = over
    sig_hands_free          = pyqtSignal(bool)            # listening with no key held: the flow bar's ✕ ✓
    sig_trace               = pyqtSignal(str)             # the shortcut's mist: "on", "keep" (its haze) or "drop"

    def __init__(self):
        super().__init__()
        # SC-05: whenever she puts something on screen, so a look knows
        # whether there is anything to rub out before its picture.
        for drew in (self.sig_draw, self.sig_point_at, self.sig_guide_point, self.sig_arrow,
                     self.sig_circle, self.sig_underline, self.sig_label):
            drew.connect(self._note_drawn)
        self._state: AppState = AppState.IDLE
        self._history: List[Message] = []
        self._current_model: Optional[str] = None
        self._web_search_enabled = cfg.switch("web_search")
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Providers (lazy)
        self._llm: Optional[BaseLLMProvider] = None
        self._stt = None
        self._tts = None

        # Realtime conversation. Opened at startup and kept open — the
        # handshake is most of a second, and a question should never wait for
        # one. _rt_audio is filled by the audio thread and drained by
        # _realtime_stream on the loop; _rt_streaming gates that forwarding
        # to the span between hotkey down and hotkey up.
        self._realtime = None
        self._rt_streaming = False
        self._rt_on_demand = False          # this question opened its own session
        self._last_talk_at = time.monotonic()   # FS-02: when they last talked to her
        # FS-09: what this PC is, once a session - so a slow user's log says
        # why. Off the start-up path: it samples the CPU for a moment.
        threading.Thread(target=lambda: _log.info(__import__("pcinfo").describe()),
                         name="pcinfo", daemon=True).start()
        self._rt_audio: deque[bytes] = deque()
        self._heard_buf: list[str] = []
        # Increments per realtime turn so a watchdog can tell whether the turn
        # it was guarding is still the current one.
        self._turn_seq: int = 0
        # The watchdog must not give up on a turn while a tool (a look, a
        # search, a plan) is still running on the model's behalf.
        self._tools_in_flight: int = 0
        self._tool_done_at: float = 0.0
        self._screen_names: list[str] = []

        # Current in-flight generation — tracked so Esc / stop can cancel
        self._current_task: Optional[asyncio.Future] = None
        self._cancel_flag = False

        # Push-to-talk bookkeeping. A capture can be ended either by releasing
        # the hotkey (hold) or by the silence watchdog (tap) — whichever gets
        # there first claims it, so an utterance is never processed twice.
        self._hotkey_pressed_at: float = 0.0
        self._end_claimed = False
        self._end_lock = threading.Lock()
        # Starting a capture needs the same mutual exclusion as ending one.
        # The hotkey runs on the keyboard-hook thread and the wake word on the
        # sounddevice callback thread; both used to test `_state == IDLE` and
        # then call _begin_capture(), which only set LISTENING afterwards. Two
        # threads could pass that test on the same utterance and open two
        # captures, producing two LLM calls and two TTS streams playing over
        # each other with no gap between them.
        self._begin_lock = threading.Lock()

        # "next" / "repeat" are one or two words. Anything longer than this
        # was a real question that Whisper mangled, not a command.
        self._MAX_COMMAND_SECONDS = 2.5

        self._last_rms: float = 0.0

        # Per-app memory: { window_title: [Message, ...] }
        self._app_memory: dict[str, List[Message]] = {}
        # Screenshots from the current turn — needed to map the LLM's
        # normalized 0-1000 tag coordinates back to logical screen pixels.
        self._screens_ctx: list = []
        self._screens_at: float = 0.0        # when _screens_ctx was captured
        # Figures detected on screen this turn (normalized vertices) — used
        # for prompt injection and for snapping sloppy stroke endpoints.
        self._figures_ctx: list = []
        # Current lesson: pending steps for multi-step tutorials. _lesson_plan
        # holds the planner's structured version when there is one — the
        # plain list is kept alongside it because the prompt and the "next"
        # counter only ever need the instructions.
        self._lesson_steps: list[str] = []
        self._lesson_plan = None
        self._lesson_step_idx: int = 0
        # True after Tora has told the user the current step doesn't look
        # done yet — so a second "next" advances anyway.
        self._lesson_nagged = False
        # The autonomous guide runner, when one is walking the user through a
        # multi-step task. None otherwise.
        self._guide_runner = None
        # A guide stopped part-way: (guide, step_index), so "continue" resumes.
        self._resume = None
        # The last walkthrough to FINISH: (guide, monotonic time, saved).
        self._guide_last = None
        # A guide whose plan is still streaming in (GW-08): step one is
        # running, the rest has not arrived. None otherwise.
        self._plan_in_progress = None
        # Prefetched step speech, per step index, filled while the previous
        # step is being performed so presenting is instant. Pointer
        # coordinates are deliberately NOT prefetched (see _prefetch_step);
        # _step_point remains only so the clearing paths stay uniform.
        self._step_audio: dict = {}
        self._step_point: dict = {}
        # Tray switch. Off = steps only advance on 'next'. The detector is
        # new (tutor_features/step_watch.py); this is the escape hatch.
        self._guide_auto_advance: bool = cfg.switch("auto_advance")
        self._last_found = None        # what _point_to last resolved
        # True while the overlay has been told to hold its pointer dwell.
        self._pointing_held = False
        # Whether an arrow has landed this turn — drives the guaranteed-
        # pointing safety net, so a locate question never ends in words alone.
        self._pointed_this_turn = False
        # Counts the things the user has said to Tora; see _begin_capture.
        # _guide_moved_for is the request that last moved a walkthrough, so
        # one "next" said is one step moved however often the model calls.
        self._request_serial = 0
        self._guide_moved_for = -1
        # Per-walkthrough counts for the one-line summary GW-03 logs at the
        # end. None when no walkthrough is running.
        self._guide_stats = None
        # Per-turn control snapshot (cx, cy, name, type) for directional
        # coreference — filled from the same walk that lists control names.
        self._pointables: list = []
        # The last thing pointed at (label + logical px + when). This is how
        # "that", "the same", "go back there" and "the one to its left"
        # resolve — the referent of the user's next sentence.
        self._last_ref = None
        # (session epoch, last screen line sent, turns since it was spelled
        # out) — see _context_head.
        self._ctx_memo: tuple[int, str, int] = (-1, "", 0)
        # Toggles - as the user last left them (config.SWITCHES, APP-03)
        self._slow_mode = cfg.switch("slow_mode")
        self._quiz_mode = False            # started on purpose, never restored
        self._privacy_guard = cfg.switch("privacy_guard")
        self._code_mode_auto = cfg.switch("code_mode")   # auto-detect IDE windows
        self._devlink_wanted = cfg.switch("devlink")     # code editors read as data (devlink/)
        self._multilang = False            # English-only; kept for the tray toggle
        self._journal_enabled = cfg.switch("journal")    # log every Q&A to SQLite
        self._ocr_enabled = cfg.switch("ocr")            # read fine print on request
        self._last_response = ""           # for "say it again" voice command
        self._attached_docs: list[tuple[str, str]] = []   # (filename, text)

        # Optional subsystems (lazy-init to keep startup fast)
        self._recorder: Optional[lesson_recorder.LessonRecorder] = None
        self._collab: Optional[collab.CollabSession] = None
        self._workflow: Optional[workflow_capture.WorkflowCapture] = None

        # Load user-created skills from skills/ + ~/.tora/skills/
        try:
            skills_pkg.load_all()
        except Exception:
            pass

        # Always-on ambient listener
        self._listener = AmbientListener(
            on_level=self._handle_level,
            on_wake=self._handle_wake,
            device=cfg.mic_device(),         # by name: device numbers move (config.py)
            ambient=cfg.ambient_mic(),
            wake_transcriber=self._transcribe_blocking,
            wake_local=self._local_wake(),
            wake_mode=cfg.wake_engine(),
        )

        # Autonomous agent workforce (agents/). Built in start(), never here:
        # a broken agent package must not stop the companion from running.
        self._agents = None

        # Background asyncio loop
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    _voice_opening = False

    def open_voice_early(self) -> None:
        """ST-01: open the voice while the rest of the app is still being
        built. On 19 Oct it was ready 12.9 s after launch: 7.9 s to get on
        screen, and only then the voice pass (1.3 s) and Google's session
        (2.1 s) - network waits that need nothing on the screen. main.py
        calls this the moment the hub exists; start() does it otherwise."""
        if self._voice_opening:
            return
        self._voice_opening = True
        # Out of credit, or a refused key, from any request anywhere: shown,
        # because it cannot be spoken - the voice uses the same account.
        gemini.on_problem(self._account_problem)
        # The HTTP pool, and the live conversation itself: the first question
        # then costs exactly what the tenth does.
        self._submit(self._warm_up())
        self._submit(self._account_warm_up())
        self._submit(self._realtime_connect())
        self._submit(self._open_speaker())

    def start(self):
        try:
            self._listener.start()
        except Exception as e:
            self.sig_error.emit(f"Mic error: {e}")
        self._start_agents()
        if self._voice_opening and self._problem_kind:
            # Found while the screen was still being built, with nothing
            # listening for it: said again now.
            kind = self._problem_kind
            self.sig_account_problem.emit(
                kind, self._problem_detail if kind == "plan" else self._PROBLEM_WORDS.get(kind, ""))
        self.open_voice_early()
        # Sleep/wake watchdog — restarts mic + loop after system resume
        self._start_sleep_watchdog()
        self._watch_audio_devices()
        self._submit(self._start_devlink())
        self._submit(self._keep_session_fresh())
        self._submit(self._keep_signed_in())
        # Speed, measured (ai/speed.py): how late the loop wakes, and what
        # Tora costs the CPU while nobody talks to her.
        self._submit(speed.watch_loop(asyncio.sleep))
        self._submit(speed.watch_cpu(asyncio.sleep, lambda: self._state == AppState.IDLE))
        # Refresh a stale model cache in the background (weekly at most).
        self._submit(self._refresh_stale_models())

    def on_access_changed(self) -> None:
        """Signed in or out of HeyTora, or switched between an account and an
        own key (the first launch, Settings): take it up now, no restart -
        the live voice, the plan, and the agents' providers."""
        self._problem_kind = ""
        self._problem_detail = ""
        self._problem_told = {}
        self.sig_account_problem.emit("", "")
        conv = self._realtime
        if conv is not None and self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(conv.close(), self._loop)
            except Exception:  # noqa: BLE001
                pass
        self._submit(self._account_warm_up())
        self._submit(self._realtime_connect())
        mgr = self._agents
        if mgr is not None:
            try:
                from agents.bootstrap import rewire_providers
                live = rewire_providers(mgr)
                _log.info("agents rewired for the new access (%s)", "live" if live else "no provider")
            except Exception:  # noqa: BLE001
                _log.exception("could not rewire the agents after an access change")

    async def _account_warm_up(self) -> None:
        """HeyTora account: read the plan once at start-up, so whether talk
        is counted is known before the first question (and a lapsed sign-in
        is said straight away, not on the first question)."""
        if gemini.own_key():
            return
        try:
            from account.service import account
            if account().signed_in():
                await account().asummary(force=True)
                if not self._metered_talk():
                    await self._realtime_connect()      # Pro / Max: open it now, as before
        except Exception as e:  # noqa: BLE001
            gemini.report_problem(e)

    SIGN_IN_CHECK_S = 120.0

    async def _keep_signed_in(self) -> None:
        """HeyTora account: the sign-in refreshed before it runs out, off any
        question's path (AC-06). A lapsed one is said by the question that
        needs it, as before."""
        while True:
            await asyncio.sleep(self.SIGN_IN_CHECK_S)
            if gemini.own_key():
                continue
            try:
                from account.service import account
                await asyncio.to_thread(account().refresh_ahead)
            except Exception:  # noqa: BLE001 - offline, or signed out: the next question says so
                _log.debug("background sign-in refresh failed", exc_info=True)

    async def _open_speaker(self) -> None:
        """Her speaker, opened at start-up rather than on her first word
        (HP-04). On the loop, where the conversation is made - never two of
        them - and the opening itself on a worker."""
        if cfg.realtime() and cfg.has_key():
            await asyncio.to_thread(self._get_realtime().open_speaker)
            _log.info("speaker: %s", _sound_devices()[0])

    async def _warm_up(self):
        try:
            await gemini.warm_up()
        except Exception:
            pass   # a cold first question is the only cost

    async def _refresh_stale_models(self):
        try:
            from ai.model_registry import refresh_all_stale
            results = await refresh_all_stale()
            for prov, count in results.items():
                if count > 0:
                    self.sig_models_refreshed.emit(prov, count)
        except Exception:
            pass   # silent — not user-facing on startup

    # A wake-word check refused by the plan (today's fair use of them) is not
    # asked again for this long - not every few seconds for the rest of the day.
    WAKE_REFUSED_PAUSE_S = 15 * 60
    _wake_refused_until = 0.0

    @staticmethod
    def _local_wake():
        """The on-PC wake word (HP-08), unless the cloud is chosen."""
        if cfg.wake_engine() == "cloud":
            return None
        try:
            from audio.wake_local import LocalWake
            return LocalWake()
        except Exception:  # noqa: BLE001 - numpy missing or the like: the cloud check
            return None

    def _transcribe_blocking(self, pcm: bytes) -> str:
        """Wake-word transcription, called from the listener's worker thread.

        Bridges onto the manager's loop so the shared HTTP client and its
        warm connection are reused rather than a fresh one per check. Bounded
        so a slow network cannot pile up wake-word threads.

        On a HeyTora plan a check is its own kind ("wake", migration 0006):
        never a dictation, so the dictation count is the user's dictations.
        """
        if not self._loop or not self._loop.is_running():
            return ""
        if time.monotonic() < self._wake_refused_until:
            return ""

        async def check() -> str:
            with gemini.request_kind("wake"):
                return await self._get_stt().transcribe(pcm)
        try:
            fut = asyncio.run_coroutine_threadsafe(check(), self._loop)
            return fut.result(timeout=12) or ""
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "LimitReached":
                self._wake_refused_until = time.monotonic() + self.WAKE_REFUSED_PAUSE_S
                _log.info("wake word paused for %d min: %s", self.WAKE_REFUSED_PAUSE_S // 60, e)
            return ""

    def _start_agents(self):
        """Bring up the agent workforce without holding the GUI thread.

        Building it costs ~650 ms (imports, SQLite, registries). That is
        the difference between the tray icon appearing at once and the
        app looking frozen for most of a second, so the build runs on a
        worker thread and `self._agents` is set when it is ready. Both
        delegation paths already treat None as "not yet available".

        Entirely optional: if the package is missing or throws, delegation
        is simply off and every other feature behaves exactly as before.
        """
        def _build():
            try:
                from agents.bootstrap import build_agent_manager
                from agents.core import EventKind
                mgr = build_agent_manager().start()
                mgr.events.subscribe(self.sig_agent_event.emit)
                mgr.events.subscribe(
                    self._on_agent_event,
                    kinds=[EventKind.TASK_STATUS, EventKind.APPROVAL_REQUIRED])
                self._agents = mgr
                _log.info("agent workforce ready — workspace %s", mgr.workspace_root)
            except Exception:
                _log.exception("agent workforce failed to start — delegation disabled")
                self._agents = None
            finally:
                # main.py wires the tray, orbs and approvals on this signal,
                # which Qt delivers on the GUI thread. Emitted on failure too,
                # so the tray items can say "unavailable" instead of nothing.
                self.sig_agents_ready.emit()

        threading.Thread(target=_build, name="agents-boot", daemon=True).start()

    # ── Telling the user what the agents are doing ────────────────────────────
    #
    # A digital employee that finishes a job in silence is one you check
    # on; one that says "MyApp is ready" is one you delegate to. So every
    # top-level task announces its end, and an approval request is spoken
    # rather than left as a dialog on a screen the user may not be facing.

    def _on_agent_event(self, ev) -> None:
        """Runs on the agent runtime thread. Decide, then hand off."""
        try:
            from agents.core import EventKind, TaskStatus
            mgr = self._agents
            if mgr is None:
                return
            task = mgr.get_task(ev.task_id or "")
            if task is None:
                return
            if (task.metadata or {}).get("parent_id"):
                return                       # the project lead speaks for its children
            agent = mgr.get_agent(task.agent_id or "")
            who = agent.spec.name if agent else "An agent"

            if ev.kind is EventKind.TASK_STATUS:
                status = TaskStatus(ev.data.get("status", ""))
                if not status.is_terminal:
                    return
                if status is TaskStatus.COMPLETED:
                    title, body = "Done", f"{who} finished: {task.title}."
                    spoken = f"{who} is done — {task.title} is ready."
                elif status is TaskStatus.FAILED:
                    reason = (task.error or "").split("\n")[0][:120]
                    title, body = "Agent stopped", f"{who} could not finish {task.title}: {reason}"
                    spoken = f"{who} hit a problem with {task.title}: {reason}"
                else:
                    return                   # cancelled: the user did that
            elif ev.kind is EventKind.APPROVAL_REQUIRED:
                req = ev.data.get("request") or {}
                call = (req.get("call") or {}).get("tool_name", "an action")
                what = "the plan" if call == "approve_plan" else call.replace("_", " ")
                title, body = "Approval needed", f"{who} wants to run {what}. {ev.message}"
                spoken = (f"{who} needs your approval to {what}. "
                          f"Say approve or deny, or use the dialog.")
            else:
                return
            self.sig_agent_notice.emit(title, body)
            self._say_when_idle(spoken)
        except Exception:
            _log.debug("agent event announcement failed", exc_info=True)
            return
        if ev.kind is EventKind.APPROVAL_REQUIRED:
            try:
                self._submit(self._remind_approval(task.id, f"{who} still needs your approval to {what}."))
            except Exception:  # noqa: BLE001 - the first notice went out; a reminder is extra
                _log.debug("approval reminder not set", exc_info=True)

    # AG-10: 8 of 21 jobs were cancelled, some after sitting on an approval
    # nobody heard about - Tora was busy when it came, so it was a toast only.
    # Once more after APPROVAL_REMIND_S, said at the next quiet moment.
    APPROVAL_REMIND_S = 120.0
    REMIND_QUIET_WAIT_S = 180.0

    async def _remind_approval(self, task_id: str, words: str) -> None:
        from agents.core import TaskStatus
        await asyncio.sleep(self.APPROVAL_REMIND_S)
        deadline = time.monotonic() + self.REMIND_QUIET_WAIT_S
        while True:
            mgr = self._agents
            task = mgr.get_task(task_id) if mgr is not None else None
            if task is None or task.status is not TaskStatus.WAITING_APPROVAL:
                return                           # answered, or gone
            if self._state is AppState.IDLE or time.monotonic() > deadline:
                break
            await asyncio.sleep(5.0)
        self.sig_agent_notice.emit("Still waiting", f"{task.title} is waiting for your approval.")
        self._say_when_idle(words)

    def _say_when_idle(self, text: str) -> None:
        """Speak only if Tora is not mid-conversation; the toast covers the
        rest. Interrupting an answer to announce a finished build would be
        worse than the silence this replaces."""
        if self._state is not AppState.IDLE:
            return
        if not self._loop or not self._loop.is_running():
            return
        try:
            self._submit(self._reply_local(text))
        except Exception:
            _log.debug("agent announcement not spoken", exc_info=True)

    def agent_context_line(self) -> str:
        """One line for the model's context so it can answer 'is my app
        ready?' and mention progress unprompted."""
        mgr = self._agents
        if mgr is None:
            return ""
        try:
            from agents.core import AgentStatus
            active = [a for a in mgr.list_agents(active_only=True)
                      if not (mgr.get_task(a.current_task_id or "") or type("T", (), {"metadata": {}})()).metadata.get("parent_id")]
            recent = [t for t in mgr.list_tasks() if t.status.is_terminal
                      and not (t.metadata or {}).get("parent_id")][:2]
        except Exception:
            return ""
        parts = []
        if active:
            parts.append("Agents working right now: " + "; ".join(
                f"{a.spec.name} on '{a.current_task_title}' — {int(a.progress * 100)}%"
                + (f", {a.current_action}" if a.current_action else "")
                + (" (PAUSED)" if a.status is AgentStatus.PAUSED else "")
                for a in active))
        if recent:
            parts.append("Recently finished: " + "; ".join(
                f"'{t.title}' {t.status.value}" for t in recent))
        line = ""
        if parts:
            line = (" ".join(parts) + ". If the user asks how it is going, answer from this; "
                    "to pause, resume, cancel or approve, call manage_agents.")
        skills = self.skills_line()
        return f"{line} {skills}".strip()

    def skills_line(self) -> str:
        """The user's skills, one line each (docs/agents/SKILLS.md): what
        lets the model start a job the user's way, or walk them through
        it. Only names and when-lines - a skill's text is read when used."""
        lib = getattr(self._agents, "skills", None) if self._agents is not None else None
        if lib is None:
            return ""
        try:
            catalogue = lib.catalogue()
        except Exception:  # noqa: BLE001
            _log.debug("could not read the skills", exc_info=True)
            return ""
        if not catalogue:
            return ""
        return ("The user's skills - how they want these jobs done:\n" + catalogue + "\n"
                "When they ask you to DO one of these jobs, call delegate_task with skill set to its id. "
                "When they ask HOW to do one, call read_skill and guide them through it on screen.")

    @property
    def agents(self):
        """The AgentManager, or None when the agent system is unavailable."""
        return self._agents

    def shutdown(self):
        speed.log_scorecard()
        # Kill any audio that was playing when the user clicked Quit
        try:
            from audio.playback import stop_audio
            stop_audio()
        except Exception:
            pass
        # Stop agents before the loop dies, bounded so a busy tool cannot
        # delay quitting. Their work is already on disk and in SQLite.
        if self._agents is not None:
            try:
                self._agents.stop(timeout=3)
            except Exception:
                _log.debug("agent shutdown failed", exc_info=True)
            self._agents = None
        self.set_devlink_running(False)
        self._listener.stop()
        self._rt_streaming = False
        # Close the websocket and the HTTP pool while the loop is still
        # alive — once it stops, neither coroutine can ever run. Bounded so
        # a hung socket cannot delay quitting.
        if self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._close_connections(), self._loop
                ).result(timeout=4)
            except Exception:
                _log.debug("closing connections on shutdown failed", exc_info=True)
        self._llm = None
        self._realtime = None
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    async def _close_connections(self):
        if self._realtime is not None:
            try:
                await self._realtime.close()
            except Exception:
                pass
        await gemini.aclose()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()

    # ── Sleep/wake watchdog ───────────────────────────────────────────────────

    def _start_sleep_watchdog(self):
        """Background thread that detects system resume after sleep/hibernate
        and restarts the mic stream + asyncio loop so the panel stays live."""
        def _watch():
            HEARTBEAT = 5.0          # check every 5 s
            DRIFT_THRESHOLD = 15.0   # if we wake and >15 s have passed, resume occurred
            last_tick = time.monotonic()
            while True:
                time.sleep(HEARTBEAT)
                now = time.monotonic()
                drift = now - last_tick - HEARTBEAT
                last_tick = now
                if drift > DRIFT_THRESHOLD:
                    # System was sleeping — restart subsystems (if Windows'
                    # own resume message has not already, ST-06)
                    self.on_power_resume()

        t = threading.Thread(target=_watch, daemon=True)
        t.start()

    # ST-06: Windows says when it resumes (WM_POWERBROADCAST, main.py's
    # _PowerEvents) - the mic and voice come back then, not when the 5 s
    # heartbeat above notices the gap and a second more. Either way, once.
    RESUME_ONCE_S = 30.0
    _resumed_at = 0.0

    def on_power_resume(self) -> None:
        now = time.monotonic()
        if now - self._resumed_at < self.RESUME_ONCE_S:
            return
        self._resumed_at = now
        _log.info("the PC woke from sleep - the microphone and voice are reopened")
        threading.Thread(target=self._on_system_resume, name="resume", daemon=True).start()

    def _on_system_resume(self):
        """Called automatically after the laptop wakes from sleep."""
        # 1. Restart the mic stream (sounddevice handles become stale on resume)
        try:
            self._listener.stop()
        except Exception:
            pass
        time.sleep(1.0)   # give Windows audio stack time to reinit
        try:
            self._listener.start()
        except Exception as e:
            self.sig_error.emit(f"Mic restart after sleep failed: {e}")

        # 2. If the asyncio loop thread died, restart it
        if not self._thread.is_alive():
            self._loop_ready.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

        # 3. Reset state to IDLE so the panel shows the correct status
        if self._state != AppState.IDLE:
            self._emit_state(AppState.IDLE)

    def _submit(self, coro):
        # start() runs a few lines after __init__ spawns the loop thread, and
        # used to lose the race: with self._loop still None the coroutine was
        # created and silently dropped, and the thing being scheduled was the
        # startup connect to the live session. A first question then found
        # no session and took the slow route.
        if not self._loop_ready.wait(timeout=5.0) or not self._loop:
            _log.warning("event loop not ready — dropping %r", coro)
            coro.close()
            return
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def _observe(f):
            if f.cancelled():
                return                  # stopped on purpose (a new question): not a failure
            try:
                f.result()
            except Exception as e:
                # A swallowed exception here used to leave the UI stuck on
                # "Listening..." forever. Surface it and
                # always return to idle.
                _log.exception("background task failed: %s", e)
                try:
                    self.sig_error.emit(str(e))
                    self._emit_state(AppState.IDLE)
                except Exception:
                    pass
        fut.add_done_callback(_observe)

    def _retire_llm(self) -> None:
        """Drop the active provider and close its pooled HTTP client.

        Providers now keep a persistent httpx.AsyncClient (up to 8 pooled
        connections). Setting self._llm = None only drops the reference, so
        the pool stayed open until garbage collection got round to it.

        The close is deferred until nothing is being generated: aclose() tears
        down the connection pool, so running it mid-stream would abort the
        answer the user is currently listening to. Scheduled directly rather
        than through _submit(), which reports failures to the user and forces
        the UI back to idle — neither is wanted for background cleanup.
        """
        old, self._llm = self._llm, None
        closer = getattr(old, "close", None)
        if old is None or not callable(closer):
            return
        if not self._loop or not self._loop.is_running():
            return

        async def _close_when_idle():
            deadline = time.monotonic() + 30
            while self._state != AppState.IDLE and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            try:
                await old.close()
            except Exception:
                _log.debug("closing retired provider failed", exc_info=True)

        try:
            asyncio.run_coroutine_threadsafe(_close_when_idle(), self._loop)
        except Exception:
            pass

    # ── Provider lazy init ────────────────────────────────────────────────────
    #
    # One provider now, so these exist only to keep construction lazy — the
    # first question should not pay for importing httpx, websockets and the
    # audio stack at startup.

    def _get_llm(self) -> BaseLLMProvider:
        if self._llm is None:
            from ai.gemini_provider import GeminiProvider
            self._llm = GeminiProvider()
        return self._llm

    def _get_stt(self):
        if self._stt is None:
            from audio.stt.gemini_stt import GeminiSTT
            self._stt = GeminiSTT()
        return self._stt

    def _get_tts(self):
        if self._tts is None:
            from audio.tts.gemini_tts import GeminiTTS
            self._tts = GeminiTTS(cfg.voice)
            self._tts.on_fallback = self._voice_fell_back
        return self._tts

    # ── LG-09: every walkthrough measured ──────────────────────────────────
    #
    # What "live" is judged by (LIVE_GUIDE_PLAN): from a step going up to its
    # arrow landing, to its first word, to the next step going up. One line
    # per step while it happens, and a scorecard when the guide ends - what
    # the next "it was slow" is diagnosed from, instead of from guesses.

    def _clock_step(self, idx: int, what: str, via: str = "") -> None:
        stats = self._guide_stats
        if stats is None or idx < 0:
            return
        clock = stats.setdefault("clock", {}).setdefault(idx, {})
        if what == "shown":
            clock.clear()                   # shown again: a repeat or a back
        if what not in clock:
            clock[what] = time.monotonic()
            if via:
                clock["via"] = via

    def _log_step_timeline(self, idx: int, step) -> None:
        stats = self._guide_stats
        clock = ((stats or {}).get("clock") or {}).get(idx) or {}
        shown = clock.get("shown")
        if shown is None:
            return
        parts = []
        if "arrow" in clock:
            gap = clock["arrow"] - shown
            parts.append("arrow +%.1fs (%s)" % (gap, clock.get("via") or "?"))
            stats.setdefault("arrows", []).append(gap)
            if str(clock.get("via", "")).startswith("ahead"):
                stats["ahead"] = stats.get("ahead", 0) + 1
        elif getattr(step, "target", ""):
            parts.append("no arrow")
            stats["no_arrow"] = stats.get("no_arrow", 0) + 1
        parts.append("voice +%.1fs" % (clock["voice"] - shown) if "voice" in clock
                     else "not said")
        _log.info("guide step %d timeline: %s", idx + 1, ", ".join(parts))

    @staticmethod
    def _scorecard(stats: dict) -> str:
        def pct(values, q):
            values = sorted(values)
            return values[min(len(values) - 1, int(q * len(values)))] if values else None

        arrows, gaps = stats.get("arrows") or [], stats.get("next_gaps") or []
        bits = []
        if arrows:
            bits.append("arrow p50 %.1fs p90 %.1fs (%d, %d found ahead)"
                        % (pct(arrows, 0.5), pct(arrows, 0.9), len(arrows),
                           stats.get("ahead", 0)))
        bits.append("no arrow %d" % stats.get("no_arrow", 0))
        bits.append("not said %d" % stats.get("unsaid", 0))
        if stats.get("windows_voice"):
            bits.append("Windows voice")
        if gaps:
            bits.append("next step up p50 %.1fs" % pct(gaps, 0.5))
        return ", ".join(bits)

    def _voice_fell_back(self, kind: str, secs: float) -> None:
        """Google stopped speaking for Tora; the Windows voice took over
        (LG-04). Said once, in words that match what happened - a daily limit
        is not "out of credit"."""
        from datetime import datetime, timedelta
        back = (datetime.now() + timedelta(seconds=secs)).strftime("%H:%M")
        why = {"quota": "Google's daily limit for Tora's voice is used up on this key",
               "billing": "Google refused Tora's voice: the Gemini credits have run out",
               "key": "Google refused Tora's voice: check the API key in Setup",
               "service": "HeyTora's voice is having trouble on our side - not yours"}.get(
                   kind, "Google did not answer for Tora's voice")
        if self._guide_stats is not None:
            self._guide_stats["windows_voice"] = True
        self.sig_agent_notice.emit(
            "Tora's voice", f"{why}. She is using the Windows voice until about {back}.")

    # ── Realtime conversation ─────────────────────────────────────────────────

    def _get_realtime(self):
        if self._realtime is None:
            from ai.gemini_realtime import RealtimeConversation
            self._realtime = RealtimeConversation(
                tools=self._realtime_tools(),
                on_heard=self._on_realtime_heard,
                on_said=self._on_realtime_said,
                on_speaking=self._on_realtime_speaking,
                on_turn_end=self._on_realtime_turn_end,
                on_lost=self._on_realtime_lost,
                recap=self._recent_exchanges,
                voice=cfg.voice,
            )
        return self._realtime

    def _recent_exchanges(self, turns: int = 6, limit: int = 1600) -> str:
        """The last few exchanges, for a session that could not be resumed.

        The live model keeps the conversation; when the server retires a
        session and the resumption handle is gone with it, this is what
        stops "open that one" from becoming "open what?".
        """
        history = self._app_memory.get(app_key(active_window_title()), [])
        if not history:
            for messages in self._app_memory.values():
                history = history or messages
        lines = []
        for message in history[-turns * 2:]:
            who = "User" if message.role == "user" else "You"
            text = " ".join((message.content or "").split())
            if text:
                lines.append(f"{who}: {text[:240]}")
        return chr(10).join(lines)[-limit:]

    def _metered_talk(self) -> bool:
        """HeyTora account on a plan that counts talk messages (Free)?"""
        if gemini.own_key():
            return False
        try:
            from account.service import account
            return account().talk_metered()
        except Exception:  # noqa: BLE001
            return True

    @staticmethod
    def _is_new_question() -> bool:
        """A new talk message? Every time the user talks, on a HeyTora plan."""
        return not gemini.own_key()

    async def _open_talk_window(self) -> None:
        """Count the question now (its talk window), not at its first call to
        the server - a question answered by voice alone makes none. A refusal
        (Free's talk messages used up) is said like any other."""
        try:
            from account.service import AccountError, account
            await account().aticket("talk")
        except AccountError as e:
            gemini.report_problem(e)
        except Exception:  # noqa: BLE001 - counting must never stop the answer
            _log.debug("could not open the talk window", exc_info=True)

    @staticmethod
    def _new_talk() -> None:
        """Tell the account a new question starts: on HeyTora's server it is a
        new talk window - a talk message - however the answer is made."""
        try:
            from account.service import account
            account().new_talk()
        except Exception:  # noqa: BLE001 - own key, or no account module
            pass

    # On a metered plan the session closes this long after an answer, so the
    # next question takes a new voice pass - a new talk message. A quick
    # follow-up inside the window rides on the same one.
    METERED_IDLE_CLOSE_S = 20.0

    async def _close_metered_session(self, turn_seq: int) -> None:
        await asyncio.sleep(self.METERED_IDLE_CLOSE_S)
        conv = self._realtime
        if (conv is None or self._turn_seq != turn_seq or conv.speaking
                or self._state != AppState.IDLE or not self._metered_talk()):
            return
        _log.info("metered plan: closing the idle voice session")
        await conv.close(keep_speaker=True)       # the speaker stays ready for the next answer

    async def _realtime_connect(self, on_demand: bool = False) -> bool:
        """Open the conversation before anyone asks anything.

        Called at startup and after a drop. The handshake and setup exchange
        are the better part of a second; paying that here means the first
        question does not. `on_demand`: the user is asking right now (how a
        metered plan opens it).
        """
        if not cfg.realtime() or not cfg.has_key():
            return False
        if not on_demand and self._metered_talk():
            # On a plan that counts talk (Free) a voice pass is a talk
            # message, so the session opens when the user asks something,
            # not at start-up or after a drop.
            return False
        try:
            return await self._get_realtime().connect(
                self._realtime_system_prompt()
            )
        except Exception as e:
            _log.info("realtime unavailable (%s) — cascade will be used", e)
            return False

    # ── SN-01: every screen answer measured ───────────────────────────────
    #
    # A question that needed a look - the last words heard, the look called,
    # the look back (which model, how many tokens), the first drawing, the
    # first word of the answer - as one line, and a running scorecard. The
    # chess complaint of 5 Oct came with no numbers at all; the next one
    # will not. See docs/SHOW_THE_NEXT_MOVE.md.

    _ans: Optional[dict] = None          # this answer's moments
    _ans_stats: Optional[dict] = None    # the session's, for the scorecard

    def _mark_answer(self, what: str, detail: str = "") -> None:
        a = self._ans
        if a is None:
            return
        if what not in a:
            a[what] = time.monotonic()
        if detail:
            a[what + ":"] = detail

    def _note_answer(self, **facts) -> None:
        """What the look was (its path, its cost) for this answer's line."""
        if self._ans is not None:
            self._ans.update(facts)

    def _answer_stats(self) -> dict:
        if self._ans_stats is None:
            self._ans_stats = {}
        return self._ans_stats

    def _note_voice(self, usage: Optional[dict]) -> None:
        """SP-03: what the live voice billed for a turn - the system prompt and
        the tools again on every generation, the audio out. On 6 Oct it was
        $0.0088 an answer against $0.0035 for the look (tools/voice_eval.py)."""
        if not usage:
            return
        from ai.gemini_live import live_cost_usd
        cost = live_cost_usd(usage)
        if not gemini.own_key():
            # On the plan, the voice is our bill: it goes into the month's use (0011).
            from account.service import account
            account().report_voice(usage)
        st = self._answer_stats()
        st["voice_cost"] = st.get("voice_cost", 0.0) + cost
        a = self._ans
        if a is not None:
            a["voice_in"] = a.get("voice_in", 0) + int(usage.get("promptTokenCount", 0))
            a["voice_out"] = a.get("voice_out", 0) + int(usage.get("responseTokenCount", 0))                 + int(usage.get("thoughtsTokenCount", 0))
            a["voice_cost"] = a.get("voice_cost", 0.0) + cost

    def _log_answer(self, said: str) -> None:
        """One timeline line for every answer - a look's in full."""
        a, self._ans = self._ans, None
        if not a:
            return
        if "tool" not in a:
            # FS-09: an answer with no look still says how long the first word
            # took, so a slow PC's log shows it on plain questions too.
            if "audio" in a:
                t0 = a.get("asked", a.get("heard", a["audio"]))
                line = "no look, first word +%.1fs, said %d chars" % (a["audio"] - t0, len(said or ""))
                if "voice_in" in a:
                    line += ", voice %d in / %d out $%.4f" % (a["voice_in"], a["voice_out"], a["voice_cost"])
                _log.info("answer timeline: %s", line)
            return
        t0 = a.get("asked", a.get("heard", a["tool"]))
        parts = ["look called +%.1fs" % (a["tool"] - t0)]
        if "look" in a:
            parts.append("look +%.1fs (%s)" % (a["look"] - t0, a.get("look:", "")))
        parts.append("drawn +%.1fs (%s)" % (a["drawing"] - t0, a.get("drawing:", ""))
                     if "drawing" in a else "nothing drawn")
        if "audio" in a:
            parts.append("voice +%.1fs" % (a["audio"] - t0))
        if "answer" in a and a["answer"] - a.get("audio", a["answer"]) > 0.05:
            # It said something first ("let me look"); this is the answer itself.
            parts.append("answer +%.1fs" % (a["answer"] - t0))
        parts.append("said %d chars" % len(said or ""))
        if "voice_in" in a:
            parts.append("voice %d in / %d out $%.4f" % (a["voice_in"], a["voice_out"], a["voice_cost"]))
        _log.info("answer timeline: %s", ", ".join(parts))
        st = self._answer_stats()
        st["looks"] = st.get("looks", 0) + 1
        if "drawing" in a:
            st.setdefault("drawn", []).append(a["drawing"] - t0)
            if "arrow" in a.get("drawing:", ""):
                st["arrows"] = st.get("arrows", 0) + 1
        if "audio" in a:
            st.setdefault("voice", []).append(a["audio"] - t0)
        if "cost" in a:
            st.setdefault("cost", []).append(float(a["cost"]))
        if a.get("path"):
            paths = st.setdefault("paths", {})
            paths[a["path"]] = paths.get(a["path"], 0) + 1

        def p50(xs):
            return sorted(xs)[len(xs) // 2] if xs else None
        bits = [f"{st['looks']} look(s)"]
        if st.get("drawn"):
            bits.append("drawn p50 %.1fs" % p50(st["drawn"]))
        bits.append(f"{st.get('arrows', 0)} arrow(s), {st['looks'] - len(st.get('drawn', []))} with nothing drawn")
        if st.get("voice"):
            bits.append("voice p50 %.1fs" % p50(st["voice"]))
        # UD-06: what the looks cost, and how often each path was taken - so a
        # release can show it did not make a message dearer.
        if st.get("cost"):
            bits.append("look cost avg $%.4f" % (sum(st["cost"]) / len(st["cost"])))
        if st.get("paths"):
            bits.append("(" + ", ".join(f"{k} {v}" for k, v in sorted(st["paths"].items())) + ")")
        # SP-03: per question asked, looked or not - the whole message's cost.
        if st.get("voice_cost") and st.get("questions"):
            voice = st["voice_cost"] / st["questions"]
            bits.append("voice cost avg $%.4f, answer cost avg $%.4f"
                        % (voice, voice + sum(st.get("cost", [])) / st["questions"]))
        _log.info("answer scorecard: %s", ", ".join(bits))

    def _on_realtime_heard(self, text: str) -> None:
        now = time.monotonic()
        if not self._heard_buf:
            self._rearm_desktop_control()
            self._count_question()
            self._ans = {"heard": now}
            st = self._answer_stats()
            st["questions"] = st.get("questions", 0) + 1
        if self._ans is not None and "tool" not in self._ans:
            self._ans["asked"] = now          # the last words before Tora acted
        self._heard_buf.append(text)

    def _count_question(self) -> None:
        """Tora has heard the user: if this question's talk message is not
        counted yet (no pass opened it), count it now - so the plan moves the
        moment someone talks, and a press with nothing said counts nothing."""
        try:
            from account.service import account
            if not gemini.own_key() and account().talk_pending():
                self._submit(self._open_talk_window())
        except Exception:  # noqa: BLE001 - counting must never stop the answer
            _log.debug("could not count the question", exc_info=True)

    @staticmethod
    def _rearm_desktop_control() -> None:
        """Esc trips the desktop kill switch and cancels what is running.
        Asking for something new is consent to act again - otherwise the
        first Esc of the day silently disarms every later request.
        """
        try:
            from agents.tools.computer_tools import kill_switch
        except Exception:  # noqa: BLE001 - the workforce is optional
            return
        if kill_switch.tripped:
            kill_switch.reset()
            _log.info("desktop control re-armed by a new request")

    def _on_realtime_said(self, text: str) -> None:
        # Output transcription arrives in fragments as the model speaks, so
        # the panel fills in at roughly the pace of the voice.
        self.sig_response_chunk.emit(text)

    def voice_clock(self):
        """Where Tora's live voice is: (seconds heard, seconds received) of the
        answer she is speaking, or None when she is not. The top caption reads
        it on the Qt thread so its words keep time with the voice."""
        conv = self._realtime
        if conv is None:
            return None
        try:
            return conv.voice_clock()
        except Exception:  # noqa: BLE001 - a caption must never break the voice
            return None

    def _on_realtime_speaking(self) -> None:
        speed.mark("audio")
        self._mark_answer("audio")
        if self._ans is not None and "tool" in self._ans:
            self._mark_answer("answer")          # the first word after the look
        self._activity("")          # it's talking now — nothing to report
        self._emit_state(AppState.SPEAKING)

    def _on_realtime_turn_end(self, turn) -> None:
        """One complete answer. Called from the pump, on the loop thread."""
        heard = (turn.heard or "").strip()
        said = (turn.said or "").strip()

        # Guaranteed pointing: a "where is…" / "how do I get to…" turn must
        # end with the buddy on the target. If the model already pointed (or
        # looked), _pointed_this_turn is set and there is nothing to do;
        # otherwise resolve it ourselves and point. The pointer hold is left
        # in place for the safety net to manage, so it is NOT released here
        # when a locate turn produced no arrow.
        # UD-04: a what-to-do answer whose action names something ("click
        # Checkout") gets its arrow too, when the voice only said it.
        needs_point = not self._pointed_this_turn and (
            is_locate(heard) or (gemini_vision.is_next_action(heard) and _target_from_answer(said)))
        if needs_point:
            self._submit(self._ensure_pointed(heard, said))
        elif self._pointing_held:
            self.sig_point_release.emit()
            self._pointing_held = False

        if said:
            self.sig_response_done.emit(said)
            self._last_response = said
            self._record_turn(heard, said)
        try:
            self._note_voice(getattr(turn, "usage", None))
            a = self._ans
            # "Let me look" and then the look: that turn ended before the
            # answer was said, so the line waits for the turn that says it.
            if not (a and "tool" in a and "answer" not in a and getattr(turn, "tools_used", None)):
                self._log_answer(said)
        except Exception:  # noqa: BLE001 - measuring must never break an answer
            _log.debug("answer timeline failed", exc_info=True)
        self._heard_buf = []
        speed.finish("answered")
        self._emit_state(AppState.IDLE)
        if self._metered_talk():
            # Metered plan: the next question takes a new voice pass.
            self._submit(self._close_metered_session(self._turn_seq))

    # FS-02: a conversation nobody has spoken in for this long is over. The
    # next question starts a new one, opening the voice as it is asked; a
    # session that drops before then is not reopened in the background. On
    # 8-9 Oct an idle session was reopened every ten minutes all night - on
    # a plan each of those is a voice pass - and kept the day before's talk.
    IDLE_FRESH_S = 30 * 60
    _last_talk_at: Optional[float] = None    # None: not known - keep the voice warm

    def _idle_s(self) -> float:
        return 0.0 if self._last_talk_at is None else time.monotonic() - self._last_talk_at

    def _forget_conversation(self) -> None:
        """FS-02: what was said before a long silence is not this conversation."""
        _log.info("a new conversation: nothing said for %d min", self._idle_s() // 60)
        self._app_memory.clear()
        self._last_look = None
        if self._realtime is not None:
            self._realtime.forget()

    def _on_realtime_lost(self, reason: str) -> None:
        """The session dropped. Reconnect quietly, in the background - while
        they are talking to her, or a walkthrough needs her voice."""
        _log.info("realtime session lost: %s", reason)
        if self._state in (AppState.THINKING, AppState.SPEAKING):
            # Its answer is gone with it. Not while they are still talking:
            # the end of the question finds no session and takes the slow
            # route with the whole recording - a weak signal dropping the
            # socket mid-sentence used to throw the question away.
            self._emit_state(AppState.IDLE)
        guide = getattr(self, "_guide_runner", None)
        if self._idle_s() > self.IDLE_FRESH_S and not (guide is not None and guide.running):
            _log.info("not reopened while idle - it opens when they next talk")
            return
        self._submit(self._realtime_reconnect())

    # FS-12: while they are talking, a session is renewed before the server's
    # ten-minute close, at a quiet moment - so a question almost never finds
    # one about to close (and, when it does, _begin_capture replaces it).
    RENEW_AT_S = 7.5 * 60
    RENEW_CHECK_S = 20.0

    async def _keep_session_fresh(self) -> None:
        while True:
            await asyncio.sleep(self.RENEW_CHECK_S)
            try:
                if self._renew_due():
                    conv = self._realtime
                    _log.info("live session renewed at %.1f min, before the server closes it",
                              conv.age_s / 60)
                    conv.retire()
                    await self._realtime_connect()
            except Exception:  # noqa: BLE001 - a renewal must never stop the loop
                _log.debug("session renewal failed", exc_info=True)

    def _renew_due(self) -> bool:
        conv = self._realtime
        if conv is None or not conv.ready or conv.age_s < self.RENEW_AT_S:
            return False
        if self._idle_s() > self.IDLE_FRESH_S:
            return False                  # nobody is talking: let it close (FS-02)
        busy = (self._state != AppState.IDLE or getattr(self, "_rt_streaming", False)
                or conv.speaking or getattr(self, "_tools_in_flight", 0) > 0)
        return not busy

    async def _realtime_reconnect(self) -> None:
        await asyncio.sleep(1.0)
        await self._realtime_connect()

    # ── When Google is the problem (APP-02) ──────────────────────────────────
    #
    # 22 September: 77 requests failed with "your prepayment credits are
    # depleted" over eleven hours and Tora just went quiet - she could not
    # even say why, because her voice runs on the same account. So these
    # are SHOWN: in the caption every time a request fails, and as a toast
    # and a tray warning at most once every ten minutes.

    _PROBLEM_WORDS = {
        "billing": ("Tora can't reach Google: your Gemini credits have run out. "
                    "Top them up at ai.studio/projects and she'll work again."),
        "key": ("Tora can't reach Google: it refused the API key. Check it in "
                "Setup - API keys."),
        "network": "Tora can't reach Google - check the internet connection.",
        "busy": ("Google's servers are overloaded right now - ask again in a "
                 "moment."),
        # A limit on one model, not the account (LG-04): nothing to top up.
        "quota": ("Google's daily limit for one of Tora's models is used up on "
                  "this key. It resets within a day; everything else still works."),
        # HeyTora account (2026-10-01). "plan" is shown with the server's own
        # words, which say which allowance and how much (_problem_detail).
        "plan": ("This month's allowance on your HeyTora plan is used up. "
                 "See Settings → Plans & billing."),
        # FS-14: Google refused HeyTora's own account - a HeyTora plan's
        # billing, key or quota. Nothing for the user to do.
        "service": ("HeyTora is having trouble on our side right now - it's not you, and "
                    "there's nothing to top up. Ask again in a few minutes."),
        "signin": ("Sign in to HeyTora to keep talking: tray icon → Sign in to "
                   "HeyTora, or use your own Gemini key."),
    }
    _PROBLEM_RENOTIFY_S = 600.0
    _problem_kind = ""
    _problem_detail = ""          # the server's words for a "plan" problem
    _problem_told = None          # kind -> when it was last shown

    def _account_problem(self, kind: str, detail: str) -> None:
        """An account problem was reported (gemini.report_problem), or - with
        kind "" - is over. Safe from any thread: it only emits a signal."""
        if not kind:
            if self._problem_kind:
                _log.info("account problem over: Google is answering again")
                self._problem_kind = ""
                self._problem_told = {}
                self.sig_account_problem.emit("", "")
            return
        self._problem_kind = kind
        if kind == "plan":
            # The server says which allowance and how much; Google's prefix off.
            self._problem_detail = _plan_words(detail) or self._PROBLEM_WORDS["plan"]
        told = self._problem_told
        if told is None:
            told = self._problem_told = {}
        now = time.monotonic()
        if now - told.get(kind, -1e9) < self._PROBLEM_RENOTIFY_S:
            return
        told[kind] = now
        _log.warning("account problem (%s): %s", kind, (detail or "")[:200])
        words = self._problem_detail if kind == "plan" else self._PROBLEM_WORDS.get(kind, detail)
        self.sig_account_problem.emit(kind, words)

    def _show_failure(self, error) -> bool:
        """Put a failed request's REASON on screen, in words a person can act
        on. True when it was one of the known kinds; the caller shows the
        raw error otherwise."""
        kind = gemini.problem_of(error)
        words = (_plan_words(str(error)) or self._PROBLEM_WORDS.get(kind)) if kind == "plan" \
            else self._PROBLEM_WORDS.get(kind)
        if not words:
            return False
        self.sig_response_chunk.emit(words)
        self.sig_response_done.emit(words)
        return True

    # HP-07: the audio thread wakes the pump as each block lands, rather than
    # the pump looking every 20 ms - up to 20 ms on every block on its way
    # to Google, and fifty wake-ups a second while listening.
    _rt_wake: Optional[asyncio.Event] = None
    RT_WAKE_MAX_S = 0.05            # still looks this often: the release, the screen line

    def _feed_realtime(self, block: bytes) -> None:
        """Audio-thread callback. Must be cheap and must never raise.

        Two jobs: park the bytes for the pump, and flush playback the instant
        the user talks over the answer. The barge-in is done here rather than
        on the loop because here is where the audio actually is — it is a
        buffer clear, not a network call.
        """
        self._rt_audio.append(block)
        wake, loop = self._rt_wake, getattr(self, "_loop", None)
        if wake is not None and loop is not None:
            try:
                loop.call_soon_threadsafe(wake.set)      # HP-07: the pump sends it now
            except RuntimeError:                         # the loop is closing
                pass
        if self._last_rms > self._SPEECH_RMS:
            conv = self._realtime
            if conv is not None and conv.speaking:
                conv.barge_in()

    async def _realtime_stream(self) -> None:
        """Forward mic audio for one question, then stop.

        The screen summary rides along: one line of text - no screenshot, no
        upload - sent as soon as it is built, while the audio already flows
        (FS-04).
        """
        conv = self._realtime
        if conv is None:
            return
        question = self._question_no
        if not conv.ready and not await self._await_session(conv):
            return
        # FS-04: the question goes out at once. The screen line is an
        # accessibility walk - 0.2-0.5 s on the owner's PC (9 Oct), seconds
        # on a slow one with a busy browser - and the audio used to wait for
        # it, so Google heard every question that much late. It is built
        # alongside now and sent the moment it is ready.
        started = time.monotonic()
        context = asyncio.ensure_future(self._screen_context())
        sent = False

        async def send_context() -> None:
            nonlocal sent
            sent = True
            took = time.monotonic() - started
            try:
                line = context.result()
            except Exception:  # noqa: BLE001 - no screen line: she can still look or point by name
                _log.debug("screen context failed", exc_info=True)
                return
            if took > 0.5:
                _log.info("screen line took %.1fs - sent while they spoke", took)
            await conv.send_context(line)

        held: list[bytes] = []
        quiet_since: Optional[float] = None
        wake = self._rt_wake = asyncio.Event()
        while self._rt_streaming:
            if not sent and context.done():
                await send_context()
            if self._rt_audio:
                chunk = b"".join(self._rt_audio)
                self._rt_audio.clear()
                if self._key_down:
                    # Held: a pause is not the end. The server decides a turn
                    # ended by HEARING silence, so it is not sent the silence -
                    # it is kept back, and goes ahead of the next words. At the
                    # release the trace goes first, then the end of the question.
                    if self._last_rms > self._SPEECH_RMS:
                        quiet_since = None
                        if held:
                            chunk = b"".join(held) + chunk
                            held.clear()
                    else:
                        now = time.monotonic()
                        quiet_since = quiet_since or now
                        if now - quiet_since > self.HOLD_QUIET_S:
                            held.append(chunk)
                            continue
                await conv.push_audio(chunk)
            else:
                wake.clear()
                if not self._rt_audio:                   # nothing landed since the look
                    try:
                        await asyncio.wait_for(wake.wait(), self.RT_WAKE_MAX_S)
                    except asyncio.TimeoutError:
                        pass
        if self._rt_wake is wake:
            self._rt_wake = None
        if self._rt_audio:
            chunk = b"".join(self._rt_audio)
            self._rt_audio.clear()
            await conv.push_audio(chunk)
        if not sent:
            # The question is over: the screen line has a moment more, then
            # the answer goes ahead without it rather than waiting.
            try:
                await asyncio.wait_for(asyncio.shield(context), self.CONTEXT_WAIT_S)
            except Exception:  # noqa: BLE001 - late or failed: go without it
                pass
            if context.done():
                await send_context()
            else:
                context.cancel()
                _log.info("screen line not ready %.1fs after the question - answered without it",
                          time.monotonic() - started)

        if question != self._question_no:
            # A new question began while this one was being finished off: its
            # trace and its trailing silence would land in the middle of it.
            return

        # What they traced while asking (TF-05): the trace is only whole now.
        await self._send_trace_line(conv)

        # Let the server hear the question end. See end_user_audio — this is
        # what makes the first word arrive immediately rather than a second
        # and a half later.
        speed.mark("sent")
        await conv.end_user_audio(stop=lambda: question != self._question_no)
        if question == self._question_no and self._rt_said_nothing != question:
            # The new question has been sent whole: whatever comes now is its
            # answer, even if the server sent no transcript of it. (A press
            # that said nothing leaves the answer it cut off silenced.)
            conv.release_superseded()

    CONTEXT_WAIT_S = 1.0        # FS-04: how long a finished question waits for the screen line

    # A session still opening is waited for, the question buffered meanwhile
    # and sent whole once it opens. On a slow connection opening takes
    # several seconds, and the slow route - transcribe, think, then speak,
    # every step its own upload - is slower still there; it used to take any
    # question whose session was not open within 3 s (17 times, 1-17 Oct).
    # Only a session that fails to open sends the question the slow way.
    SESSION_WAIT_S = 15.0       # the handshake itself allows 20 s
    _rt_awaiting = 0            # the question waiting for its session (0: none)
    _rt_said_nothing = 0        # the last question that ended with nothing said
    _rt_question_pcm: Optional[bytes] = None    # its audio, kept for the slow route

    async def _await_session(self, conv) -> bool:
        """Wait for the live session to open; True when it has. False: this
        question goes the slow way - at the release if the user is still
        talking, from here if they have finished - or it was abandoned."""
        question = self._question_no
        started = time.monotonic()
        self._rt_awaiting = question
        try:
            while not conv.ready and time.monotonic() - started < self.SESSION_WAIT_S:
                if question != self._question_no or self._rt_said_nothing == question:
                    return False                 # a newer question, or nothing to send
                if not conv.connecting and time.monotonic() - started > 0.5:
                    break                        # nothing is opening it: it failed
                await asyncio.sleep(0.05)
        finally:
            if self._rt_awaiting == question:
                self._rt_awaiting = 0
        if question != self._question_no:
            return False                         # its audio is the new question's now
        if conv.ready:
            waited = time.monotonic() - started
            if waited > 0.3:
                speed.mark("session", waited=f"{waited:.1f}s for the session")
            if waited > 1.0:
                _log.info("waited %.1fs for the live session to open", waited)
            if waited > net_quality.SLOW_OPEN_S:
                net_quality.note_slow(f"the voice took {waited:.0f}s to open")
            return True
        _log.info("the live session did not open - this question goes the slow way")
        speed.route("slow")
        if self._rt_streaming:
            self._rt_streaming = False           # still talking: the release takes the slow route
            return False
        pcm, self._rt_question_pcm = self._rt_question_pcm, None
        if pcm:
            self._turn_seq += 1                  # the live route's watchdog stands down
            await self._cascade_turn(pcm)
        return False

    LESSON_STALE_S = 600.0
    _lesson_key = None                    # (the lesson, its step) as last seen
    _lesson_key_at = 0.0                  # since when

    def _lesson_stale(self) -> bool:
        """A lesson nobody has moved on for LESSON_STALE_S is over. It is
        dropped, so it is no longer "in progress" in every turn after - and
        a "next" an hour later does not teach its next step."""
        key = (tuple(self._lesson_steps), self._lesson_step_idx)   # not id(): Python reuses those
        now = time.monotonic()
        if self._lesson_key != key:
            self._lesson_key, self._lesson_key_at = key, now
            return False
        if now - self._lesson_key_at < self.LESSON_STALE_S:
            return False
        if self._guide_runner is None or not self._guide_runner.running:
            _log.info("an untouched lesson (step %d of %d) dropped",
                      self._lesson_step_idx + 1, len(self._lesson_steps))
            self._lesson_steps, self._lesson_plan, self._lesson_step_idx = [], None, 0
        return True

    def _context_head(self, line: str) -> str:
        """The screen line, or a note that nothing has changed.

        The control list is the same twenty-odd names turn after turn while
        the user stays in one app, and the Live API re-bills the whole
        conversation every turn — so repeating it is paid for once to send
        and then again on every turn that follows. Saying "same screen"
        instead costs four tokens and tells the model exactly as much.

        It is sent in full again when the screen changes, after a handful of
        turns (in case compression has dropped the original), and whenever
        the session started blank, which is when the model really has not
        been told.
        """
        conv = self._realtime
        epoch = getattr(conv, "epoch", 0) if conv is not None else 0
        last_epoch, last_line, age = self._ctx_memo
        if (line == last_line and epoch == last_epoch
                and age < CONTEXT_REPEAT_TURNS - 1):
            self._ctx_memo = (epoch, last_line, age + 1)
            return "[context] Same screen as a moment ago."
        self._ctx_memo = (epoch, line, 0)
        return f"[context] Today is {datetime.now():%A, %B %d, %Y}. {line}"

    async def _screen_context(self) -> str:
        """One line about the screen. No pixels."""
        from ai import screen_summary
        summary = await asyncio.to_thread(
            screen_summary.describe, privacy_guard=self._privacy_guard
        )
        self._screen_names = summary.names
        # Cache the control snapshot so directional coreference ("the one to
        # its left") reuses this turn's walk instead of walking again. When
        # and where it was taken decide whether it may still be trusted -
        # see _match_snapshot (APP-04).
        self._pointables = summary.pointables
        self._pointables_at = time.monotonic()
        self._pointables_title = active_window_title()
        self._note_uia(self._pointables_title, len(summary.pointables or ()))
        bits = [self._context_head(summary.line)]
        ref = self._last_ref
        if self._ref_fresh():
            bits.append(
                f'You just pointed at "{ref.label}". If the user says "that", '
                f'"it", "the same" or "go back there" they mean it; "the one '
                f'to its left/right/above/below" means the control next to it. '
                f'Either way just call point_at — Tora resolves the reference.'
            )
        if self._guide_runner is not None and self._guide_runner.running:
            plan = self._lesson_plan
            cur = self._lesson_step_idx
            cur_line = ""
            if plan is not None and 0 <= cur < len(plan.steps):
                s = plan.steps[cur]
                if cur > 0:
                    # Steps are spoken in Tora's own voice, not the live
                    # model's, so they are in no conversation history -
                    # resumed session or not. Asked "what have I done so
                    # far?", the model made history up ("added your logo")
                    # 6 times in 6 (GW-15 live check).
                    first = max(0, cur - 3)
                    behind = "; ".join(
                        f'{i + 1}. "{plan.steps[i].instruction[:80]}"'
                        for i in range(first, cur))
                    more = f" (and {first} before those)" if first else ""
                    cur_line = (f" Steps already behind them (done or skipped): "
                                f"{behind}{more}")
                cur_line += f' The current step is: "{s.instruction}"'
                if s.target:
                    # "The arrow goes on X" read as an instruction: on a bare
                    # "okay" the model called point_at 4 times in 5. Saying
                    # it is already there: 0 in 5 (GW-14 live check).
                    cur_line += f" Tora's arrow is already on \"{s.target}\"."
            if plan is not None and plan is self._plan_in_progress:
                # "Step 1 of 1, 0 left" while the plan streams in would tell
                # the model this is the last step (GW-08).
                count = (f" — step {cur + 1} (the rest of the plan is still "
                         f"being written). Tora ")
            else:
                count = (f" — step {cur + 1} of {len(self._lesson_steps)}, "
                         f"{max(0, len(self._lesson_steps) - cur - 1)} left. Tora ")
            bits.append(
                "A guided walkthrough is running"
                + (f' toward: "{plan.goal}"' if getattr(plan, "goal", "") else "")
                + count +
                f"speaks and points at each step and advances on its own as "
                f"the user completes it — do NOT read the steps and do NOT say "
                f"'say next'. Call guide_step ONLY for a command: next (done / "
                f"I did it / carry on), skip, back, repeat (say that again), "
                f"stop, pause, where (where is it / I can't find it), help "
                f"(I'm stuck / it's not working), do_it (do it for me). "
                f"If they ask for something DIFFERENT - another task, another "
                f"app, open something, how to do something else - call "
                f"guide_step with action stop FIRST, then do what they asked; "
                f"the walkthrough is over. A question or remark about THIS "
                f"walkthrough or this screen: answer it and leave the guide "
                f"alone; it keeps running underneath. 'Okay' and 'got it' are not commands: it "
                f"moves on by itself when the step is done. If they ask what "
                f"they're doing or what's coming, answer from this context "
                f"rather than starting over; for anything else on screen they "
                f"ask about, point_at still works.{cur_line}"
            )
        elif self._resume:
            stopped, at = self._resume
            goal = getattr(stopped, "goal", "") or ""
            bits.append(
                "A walkthrough" + (f' toward "{goal}"' if goal else "")
                + f" was stopped at step {at + 1} of {len(stopped.steps)}. If "
                "the user asks to resume or continue THAT walkthrough, call "
                "guide_step with action 'resume'."
            )
        elif self._lesson_steps and not self._lesson_stale():
            bits.append(
                f"A guide is in progress: step {self._lesson_step_idx + 1} of "
                f"{len(self._lesson_steps)}. If they say 'next', teach the "
                f"following step only."
            )
        elif (self._guide_last is not None
              and time.monotonic() - self._guide_last[1] < FINISHED_CONTEXT_S):
            done, _, saved = self._guide_last
            steps = "; ".join(f'{i + 1}. "{s.instruction[:80]}"'
                              for i, s in enumerate(done.steps[:8]))
            bits.append(
                f'A walkthrough toward "{done.goal}" just finished, all '
                f"{len(done.steps)} steps: {steps}."
                # "It is saved" read as the user's WORK being saved ("your
                # template has been saved to your journal") - say what is.
                + (" The walkthrough's steps are saved in their Tora journal, "
                   "so it can be gone through again." if saved else "")
                + " It is over - answer questions about it from this, and "
                "do not call guide_step."
            )
        waiting = self._pending_confirmation_line()
        if waiting:
            bits.append(waiting)
        if self._attached_docs:
            names = ", ".join(n for n, _ in self._attached_docs)
            bits.append(f"Documents they have attached: {names}. Use recall.")
        agents_line = self.agent_context_line()
        if agents_line:
            bits.append(agents_line)
        return " ".join(bits)

    def _pending_confirmation_line(self) -> str:
        """Tell the model something is waiting on a yes, and how to give it.

        Without this, a confirmation could only be answered with the mouse -
        which is a strange thing to require of a voice assistant, and the
        approve/deny machinery to do it by voice was already there in
        manage_agents. All that was missing was the model knowing.
        """
        mgr = self._agents
        if mgr is None:
            return ""
        try:
            pending = mgr.pending_approvals()
        except Exception:  # noqa: BLE001
            return ""
        if not pending:
            return ""
        req = pending[-1]
        # Only a clear yes or no answers the box. Anything else is something
        # NEW: "Do this INSTEAD of answering" made every later question an
        # answer to an old box, and Tora stuck on it (5 Oct 2026).
        if getattr(req, "sensitive", False):
            return (f'A box on screen is waiting for a yes: "{req.reason}". It is '
                    f'sensitive - it can ONLY be approved by typing {req.confirm_word} '
                    f'in the box. If they say yes to it, tell them that; never '
                    f'approve it for them. Anything else they say is new: answer that.')
        return (f'A box on screen is waiting for a yes: "{req.reason}". ONLY if '
                f'their words are a yes or no to it (yes, go on, do it / no, '
                f'stop, cancel it) call manage_agents with action "approve" or '
                f'"deny". Anything else they say is a NEW request - answer it or '
                f'do it, and leave the box alone; do not bring it up.')

    def _realtime_system_prompt(self) -> str:
        """The session-level prompt. Deliberately free of per-turn detail.

        Changing this forces a reconnect, so anything that varies question to
        question — the window, the controls on it — goes through
        _screen_context and rides along as ordinary text instead.
        """
        return _build_system_prompt(
            quiz_mode=self._quiz_mode,
            language_code=cfg.response_language or "en",
            realtime=True,
        )

    # ── What the live model can do ────────────────────────────────────────────
    #
    # Every handler returns a small dict that goes straight back over the
    # websocket as the tool result, so it has to be JSON-shaped and it has to
    # be quick. Anything that fails returns {"error": ...} rather than
    # raising: the model can apologise for a tool that did not work, but a
    # raised exception ends the turn in silence.

    def _realtime_tools(self) -> dict:
        # Every handler is wrapped so the status island is ALWAYS cleared when
        # a tool finishes, however it finishes. Relying on each tool to clear
        # its own activity did not survive contact: look_at_screen, search and
        # planning all had early-return paths that skipped the clear, and the
        # island then sat on "Analysing your screen… / Still working on it…"
        # forever — the precise "is it stuck?" feeling the island exists to
        # prevent. One wrapper here is worth more than six remembered finallys.
        return {
            name: self._clears_activity(fn)
            for name, fn in self._tool_handlers().items()
        }

    def _clears_activity(self, fn):
        async def wrapped(args: dict) -> dict:
            self._tools_in_flight += 1
            try:
                return await fn(args)
            finally:
                self._tools_in_flight -= 1
                self._tool_done_at = time.monotonic()
                self._activity("")
        return wrapped

    def _tool_handlers(self) -> dict:
        return {
            "point_at":        self._tool_point_at,
            "look_at_screen":  self._tool_look_at_screen,
            "draw":            self._tool_draw,
            "clear_drawings":  self._tool_clear,
            "show_me_how":     self._tool_show_me_how,
            "guide_step":      self._tool_guide_step,
            "search_web":      self._tool_search,
            "recall":          self._tool_recall,
            "delegate_task":   self._tool_delegate_task,
            "read_skill":      self._tool_read_skill,
            "desktop_action":  self._tool_desktop_action,
            "manage_agents":   self._tool_manage_agents,
            "write_for_me":    self._tool_write_for_me,
            "reply_to_email":  self._tool_reply_to_email,
            "code_context":    self._tool_code_context,
        }

    # ── DevLink (devlink/): the code editor, read as data ────────────────────
    #
    # The design of 23 Oct: what the developer is doing, from VS Code's own
    # API through a connector, instead of from screenshots. The hub runs on
    # this loop; the connector finds it through devlink.json in the data
    # folder. Nothing is read until a workspace allows it.

    async def _start_devlink(self) -> None:
        if not self._devlink_wanted:
            return
        import devlink
        if devlink.current() is not None:
            return
        try:
            hub = devlink.Hub(cfg.data_dir())
            await hub.start()
        except Exception:  # noqa: BLE001 - the screen still works without it
            _log.warning("devlink did not start", exc_info=True)
            return
        devlink.set_current(hub)

    async def _stop_devlink(self) -> None:
        import devlink
        hub = devlink.current()
        devlink.set_current(None)
        if hub is not None:
            await hub.stop()

    def set_devlink_running(self, on: bool) -> None:
        """Settings → Code editors: start or stop the hub now (the switch
        itself is saved by the caller)."""
        self._devlink_wanted = bool(on)
        if not (self._loop and self._loop.is_running()):
            return
        try:
            asyncio.run_coroutine_threadsafe(self._start_devlink() if on else self._stop_devlink(),
                                             self._loop).result(timeout=4)
        except Exception:  # noqa: BLE001
            _log.debug("devlink %s failed", "start" if on else "stop", exc_info=True)

    async def _tool_code_context(self, args: dict) -> dict:
        from devlink import tools as devlink_tools
        self._activity("reading_code")
        started = time.monotonic()
        out = await devlink_tools.answer(args)
        _log.info("code_context %s: %s in %.2fs", args.get("action") or "overview",
                  f"{len(out.get('text', ''))} chars" if out.get("ok") else out.get("error", "")[:80],
                  time.monotonic() - started)
        return out

    async def _tool_reply_to_email(self, args: dict) -> dict:
        """The email on screen, read and answered in the app's own reply
        form (ai/email_reply.py) - typed in as it is written. Never sent:
        the user presses Send."""
        request = str(args.get("request") or "").strip() or "Reply to this email."
        app = email_reply.app_for(active_window_title())
        if app is None:
            return {"error": ("Replying works in Gmail in the browser for now. Ask them to open the email "
                              "there, then ask again.")}
        started = time.monotonic()
        self._activity("reading_email")
        keyboard = write_for_me.Keyboard()
        # Three things at once: the page is read (~0.3 s) while Gmail's reply
        # opens (~0.9 s), and the writing starts the moment the page is read
        # - its first words wait for the reply box, not the other way round.
        opening = asyncio.ensure_future(
            asyncio.to_thread(app.open_reply, email_reply.reply_all_wanted(request), keyboard))
        pump = None
        try:
            mail = await asyncio.to_thread(app.read)
            rewrite = self._wrote_into(mail.draft)
            if not rewrite:
                pieces, pump = _prefetched(email_reply.stream_reply(request, mail, mail.draft))
            draft, done = await opening
            if not rewrite and self._wrote_into(draft):
                pump.cancel()
                rewrite = True
        except Exception as e:  # noqa: BLE001 - say so, never half-guess an email
            if pump is not None:
                pump.cancel()
            if not opening.done():
                opening.add_done_callback(lambda f: f.exception())     # let it finish; nothing to type into
            if isinstance(e, email_reply.MailError):
                return {"error": f"{e}"}
            _log.info("email reply: could not read it or open the reply (%s)", e)
            return {"error": "The email could not be read, or its reply could not be opened. Ask them to try again."}
        if rewrite:
            # 23 Oct: "I didn't like it - it just added more text." Asked
            # again, the reply she wrote is changed in place - never a
            # second one typed above it.
            _log.info("email reply: the draft holds Tora's reply - changing it, not writing another")
            return await self._write_for_me(request, page=mail.text, edit_only=True)
        ready_s = time.monotonic() - started
        self._activity("writing")
        typist = write_for_me.Typist(keyboard)
        how, first = await self._type_as_written(pieces, typist, tail="\n\n" if draft.strip() else "")
        # Nothing of the email is kept: its length and what was done, no more.
        _log.info("email reply: %s, read the page (%d chars) and opened %s in %.1fs, first words at %s, "
                  "%d chars - %s, all in %.1fs", app.name, len(mail.text), ", ".join(done) or "the open reply",
                  ready_s, f"{first - started:.1f}s" if first else "-", len(typist.text.strip()), how or "nothing",
                  time.monotonic() - started)
        if not how:
            return {"error": "The reply did not come back. Ask them to try again."}
        self.sig_response_chunk.emit(typist.text.strip() + "\n\n")
        self._note_written(typist.text)
        if how == "clipboard":
            return {"ok": True, "result": ("They left the page while it was written: what was typed is in the "
                                           "draft and the rest is on their clipboard. It is NOT sent.")}
        return {"ok": True, "result": ("The reply is in the draft. It is NOT sent: tell them to check it and "
                                       "press Send. Never read it out.")}

    # ── Writing for them (ai/write_for_me.py) ─────────────────────────────────
    #
    # Asked for on 10 Oct: they talk about what they want written, loosely,
    # and Tora works out what they mean, writes it well and puts it in the
    # box they are typing in - as it is written, the first words in about a
    # second ("it needs to be instant"). The voice is never handed the text.

    async def _type_as_written(self, pieces, typist, tail: str = "") -> tuple[str, Optional[float]]:
        """Each piece typed as it arrives. (How it went - typed, pasted or
        clipboard, "" for nothing - and when the first words came.)"""
        first = None
        async for piece in pieces:
            if first is None:
                first = time.monotonic()
            typist.feed(piece)
        if tail and typist.text.strip():
            typist.feed(tail)
        return await asyncio.to_thread(typist.finish), first

    async def _tool_write_for_me(self, args: dict) -> dict:
        request = str(args.get("request") or "").strip()
        if not request:
            return {"error": "nothing was said about what to write"}
        return await self._write_for_me(request)

    async def _write_for_me(self, request: str, page: Optional[str] = None, *, edit_only: bool = False) -> dict:
        """Into the focused box: typed as it is written, or (EDIT) its text
        rewritten and swapped in. `page` given: what "this" means, already
        read; `edit_only`: they are changing what is there."""
        self._activity("writing")
        box = await asyncio.to_thread(write_for_me.focused_box)
        if box is None:
            return {"error": ("No text box has the focus. Ask them to click into the box they want it in, "
                              "then ask again.")}
        if box.password:
            return {"error": "That is a password box - Tora never types into one. Tell them so."}
        started = time.monotonic()
        if page is None:
            # "Reply to this": the page's own text says what "this" is - never a picture.
            sensitive = self._privacy_guard and is_sensitive_window(active_window_title())
            page = (await asyncio.to_thread(write_for_me.page_context)
                    if write_for_me.leans_on_screen(request) and not sensitive else "")
        pieces = write_for_me.stream_dictation(request, box, page, edit_only=edit_only).__aiter__()
        head = await anext(pieces, "")
        if head.startswith("DO"):
            # Never mix the two: a command is not typed into the box. The
            # app actions and agents ask the user before anything runs.
            command = head[3:].strip()
            _log.info("write for me: a command, not dictation - %r", command or request[:80])
            return {"ok": False, "result": (
                f"Nothing was typed: that is a command ({command or 'an action'}), not text "
                "for the box. Do it with desktop_action or delegate_task - they ask the user first.")}
        if head.startswith("EDIT"):
            return await self._edit_box(box, head == "EDIT", pieces, started)
        if head != "TYPE" or edit_only:
            return {"error": "The writing did not come back. Ask them to try again."}
        keyboard = write_for_me.Keyboard()
        await asyncio.to_thread(write_for_me.keys_let_go, keyboard)
        typist = write_for_me.Typist(keyboard, paste_at_end=write_for_me.pastes(box))
        how, first = await self._type_as_written(pieces, typist)
        text = typist.text.strip()
        _log.info("write for me: %d chars into %s %r in %r - %s, first words at %s, all in %.1fs%s", len(text),
                  box.kind, box.name, box.window, how or "nothing", f"{first - started:.1f}s" if first else "-",
                  time.monotonic() - started, ", with the page" if page else "")
        if not how:
            return {"error": "The writing did not come back. Ask them to try again."}
        # What was written, on the card (with its Copy) as she says it's in.
        self.sig_response_chunk.emit(text + "\n\n")
        self._note_written(text)
        if how == "clipboard":
            return {"ok": True, "result": ("They left the box while it was written: what was typed is in it and "
                                           "the rest is on their clipboard - Ctrl+V puts it where they want it.")}
        return {"ok": True, "result": f"Written into the box ({len(text)} characters). Say it's in - "
                                      "never read it out."}

    async def _edit_box(self, box, whole: bool, pieces, started: float) -> dict:
        """EDIT: the box's whole text - or (whole=False) the part they
        selected - rewritten and swapped in with one paste. Never added to."""
        old = box.text if whole else box.selected
        if not whole and not old.strip():
            return {"error": ("Nothing is selected in the box, so nothing was changed. Ask them to select the "
                              "part to change and say it again.")}
        if (whole and not box.whole) or len(old) > write_for_me.EDIT_MAX_CHARS:
            return {"error": ("Nothing was changed: Tora can't rewrite all of that box in one go (too long, not "
                              "all of it readable, or a document whose layout would be lost). Ask them to select "
                              "the part to change and say it again.")}
        new = "".join([p async for p in pieces])
        new = new.strip() if whole else write_for_me.keep_edges(old, new)
        keyboard = write_for_me.Keyboard()
        await asyncio.to_thread(write_for_me.keys_let_go, keyboard)
        how = await asyncio.to_thread(write_for_me.replace, new, box, whole=whole, keyboard=keyboard)
        _log.info("write for me: rewrote %s, %d chars -> %d, in %s %r in %r - %s, all in %.1fs",
                  "the box" if whole else "the selection", len(old), len(new.strip()), box.kind, box.name,
                  box.window, how, time.monotonic() - started)
        if how == "clipboard":
            return {"ok": True, "result": ("They left the box before the change went in: nothing in it was changed, "
                                           "and the new text is on their clipboard - Ctrl+V puts it in.")}
        if how == "emptied":
            return {"ok": True, "result": "Deleted. Say so in a few words."}
        self.sig_response_chunk.emit(new.strip() + "\n\n")
        self._note_written(new)
        return {"ok": True, "result": ("Changed in the box - the old text replaced, nothing added. Say one short "
                                       "line that it's changed; never read it out. Ctrl+Z in the box brings the old "
                                       "text back if they want it.")}

    # What Tora put in boxes lately, by its start: an email reply asked for
    # again is a change to hers, not a second reply above it (23 Oct).
    WRITTEN_KEPT_S = 3600.0
    _written: tuple = ()                     # ((when, its first 60 characters), ...)

    def _note_written(self, text: str) -> None:
        start = " ".join((text or "").split())[:60]
        if start:
            now = time.monotonic()
            kept = [(t, s) for t, s in self._written if now - t < self.WRITTEN_KEPT_S]
            self._written = tuple((kept + [(now, start)])[-5:])

    def _wrote_into(self, text: str) -> bool:
        """Does `text` (a draft) hold something Tora wrote this past hour?"""
        have = " ".join((text or "").split())
        now = time.monotonic()
        return bool(have) and any(now - t < self.WRITTEN_KEPT_S and s in have for t, s in self._written)

    # ── Agent delegation ──────────────────────────────────────────────────────
    #
    # Two routes reach the workforce. The live model calls delegate_task
    # itself (it can hear the whole request and judge intent); the cascade
    # route has only a transcript, so the classifier decides. Either way the
    # work happens on the agent runtime's thread, never on this one.

    _WORKER_FOR_KIND = {"coding": "coding", "file": "file", "terminal": "terminal",
                        "research": "research", "browser": "browser", "computer": "computer"}

    async def _tool_delegate_task(self, args: dict) -> dict:
        """Hand a build request to the agent workforce.

        Returns the plan rather than waiting for the work: the model reads
        the steps back while the agent is already running them.
        """
        if self._agents is None:
            return {"error": "the agent workforce is not available"}
        from agents.core import Task, TaskKind
        goal = str(args.get("goal") or "").strip()
        if not goal:
            return {"error": "goal is required"}
        skill = None
        skill_id = str(args.get("skill") or "").strip()
        if skill_id:
            skill = self._agents.skills.get(skill_id) if self._agents.skills is not None else None
            if skill is None:
                return {"error": f"there is no skill {skill_id!r} - check the list, or leave skill out"}
        title = str(args.get("title") or (skill.name if skill else goal[:60])).strip()
        try:
            kind = TaskKind(str(args.get("kind") or ("computer" if skill else "coding")).lower())
        except ValueError:
            kind = TaskKind.CODING
        meta = {"request": goal, "reply": f"Understood. Starting on {title} now."}
        workspace = self._agents.workspace_root
        # 11 Oct: "turn this Word document into a PDF" - the agent was given its
        # own workspace and no file, searched Tora Projects and failed twice.
        # The files they mean (traced over, selected in Explorer, open in
        # Office) go with the job as paths, and it works in their folder.
        files = await asyncio.to_thread(_files_meant, getattr(self, "_trace_readings", None), goal)
        if files and "pdf" in goal.lower() and screen_files.is_conversion(goal) and convert_pdf.can_convert(files):
            # "Make sure it converts it in the background" (11 Oct): no agent,
            # no window - the app that made it saves the PDF, unseen.
            open_after = bool(re.search(r"\bopen\b", goal, re.IGNORECASE))
            asyncio.ensure_future(self._convert_in_background(files, open_after))
            names = ", ".join(os.path.basename(f) for f in files)
            return {"ok": True, "result": (f"Converting {names} to PDF in the background - nothing opens on their "
                                           "screen. Say in one line that it has started; Tora says when it is done.")}
        if files:
            goal += "\n\nThe file(s) they mean - use these exact paths: " + "; ".join(files)
            meta["request"], meta["files"] = goal, files
            workspace = os.path.dirname(os.path.commonpath(files)) if len(files) > 1 else os.path.dirname(files[0])
            if screen_files.is_conversion(goal):
                # Converting needs the app that made it, from PowerShell: the
                # terminal agent. The file agent cannot run anything.
                kind = TaskKind.TERMINAL
                if skill is None and self._agents.skills is not None:
                    skill = self._agents.skills.get("convert-to-pdf") if "pdf" in goal.lower() else None
            _log.info("delegate_task: %d file(s) in question, working in %s", len(files), workspace)
        if skill is not None:
            meta["skill"] = skill.id
        hint = str(args.get("workspace_hint") or "").strip().strip("'\"")
        if hint and not files:
            meta["subdir"] = hint
        task = Task(title=title, goal=goal, workspace=workspace,
                    kind=kind, worker_type=self._WORKER_FOR_KIND.get(kind.value, "coding"),
                    metadata=meta)
        try:
            task = self._agents.submit_task(task)
        except Exception as e:
            _log.exception("delegate_task failed")
            return {"error": f"could not start the task: {e}"}
        await self._wait_for_plan(task)
        agent = self._agents.get_agent(task.agent_id)
        return {
            "ok": True,
            "task_id": task.id,
            "agent": agent.spec.name if agent else "Agent",
            "workspace": task.workspace,
            "steps": [s.title for s in task.steps][:10],
            "note": ("The agent is working in the background. Read the steps back "
                     "briefly and tell the user they can carry on with other things."),
        }

    async def _convert_in_background(self, files: list[str], open_after: bool) -> None:
        """Each file to a PDF beside it (ai/convert_pdf.py), then a word on
        where it is - spoken if Tora is free, on the card if she is not."""
        started = time.monotonic()
        made, failed = await asyncio.to_thread(convert_pdf.to_pdfs, files)
        _log.info("convert: %d made, %d failed in %.1fs", len(made), len(failed), time.monotonic() - started)
        if made and open_after:
            try:
                os.startfile(made[0])                       # they asked to see it
            except OSError:
                pass
        if made:
            where = os.path.dirname(made[0])
            msg = (f"Done - {os.path.basename(made[0])} is in {os.path.basename(where) or where}." if len(made) == 1
                   else f"Done - {len(made)} PDFs are in {os.path.basename(where) or where}.")
        else:
            msg = "I couldn't make the PDF."
        if failed:
            msg += " " + "; ".join(failed[:2])
        if self._state == AppState.IDLE:
            await self._reply_local(msg)
        else:
            self.sig_response_chunk.emit(msg)
            self.sig_response_done.emit(msg)

    async def _tool_read_skill(self, args: dict) -> dict:
        """One of the user's skills, in full - to guide them through it on
        screen, or to answer how they like something done."""
        lib = getattr(self._agents, "skills", None) if self._agents is not None else None
        if lib is None:
            return {"error": "skills are not available"}
        skill = lib.get(str(args.get("id") or "").strip())
        if skill is None:
            return {"error": "no such skill", "skills": [s.id for s in lib.enabled()]}
        return {"ok": True, "name": skill.name, "when": skill.when, "apps": skill.apps,
                "steps": skill.body, "ask_before": skill.ask_before,
                "note": "Guide them one step at a time; point at what to click."}

    async def _tool_desktop_action(self, args: dict) -> dict:
        """One app action, now: "Spotify, next song", "open this project in
        VS Code", "put this in Cursor's chat", "open Bluetooth settings".

        This is the fast path - no task, no agent, no plan - because a
        person asking for the next song is not asking for a project. It
        still confirms: anything that acts shows the approval dialog, and
        the user can wave through that one action for good - or all of them
        with Settings > Approvals, which still never covers an action that
        sends, posts, pays or deletes (Risk.SENSITIVE).
        """
        from agents.control import Risk, control
        from agents.control.args import read_arguments

        app = str(args.get("app") or "").strip()
        action = str(args.get("action") or "").strip()
        arguments = args.get("arguments")
        if not isinstance(arguments, dict):
            raw = args.get("arguments_json") or args.get("value") or ""
            # A parse failure used to become {} in silence, and the action
            # then ran on its defaults - which is how "reduce the volume"
            # became a volume increase. Now it is an error the model can
            # read and correct.
            arguments, how = read_arguments(raw)
            if arguments is None:
                _log.warning("desktop_action %s.%s: could not read arguments %r",
                             app, action, str(raw)[:120])
                return {"error": f"I could not read those arguments: {how}. "
                                 "Send arguments_json as a JSON object, e.g. "
                                 '{"direction": "down"}.'}
            if how:
                _log.info("desktop_action %s.%s arguments read %s", app, action, how)
        c = control()
        # An app whose controls failed to load must say so, not quietly let
        # the model reach for a browser instead.
        broken = c.broken()
        if app and c.resolve_app(app) in broken:
            name = c.resolve_app(app)
            return {"error": f"{name} is installed but its controls did not load "
                             f"this session ({broken[name]}). Say that plainly. Do "
                             f"NOT do it through a browser instead.",
                    "restart_may_fix": True}
        if not action or action in ("list", "list_actions", "what_can_you_do"):
            apps = c.apps()
            if app:
                found = c.actions(app=app)
                return {"ok": True, "app": c.resolve_app(app),
                        "actions": [a.describe() for a in found]} if found else {"error": c.suggestions(app)}
            return {"ok": True, "apps": [{"app": a["app"], "actions": a["actions"]} for a in apps],
                    "note": "Say the app and the action; I confirm before anything that acts."}
        app = self._browser_they_meant(c, app, action)
        found = c.find(app, action)
        if found is None:
            return {"error": c.suggestions(app, action),
                    "actions": [a.describe() for a in c.actions(app=app)][:20]}

        # Check the arguments BEFORE anyone is asked. They used to be checked
        # only when the approved action ran, so a misnamed one meant the user
        # said yes to something that then did nothing - 12 times in the field
        # log ("app_name" for "name" eight of them). Refused here, the model
        # hears why at once and calls again; the user is never involved.
        from agents.control.args import BindError, bind
        try:
            arguments = bind(found.key, found.params, arguments)
        except BindError as exc:
            _log.info("desktop_action %s refused before approval: %s", found.key, exc)
            return {"error": str(exc), "expected": exc.expected,
                    "note": ("The user has not been asked anything. Call "
                             "desktop_action again with the arguments named "
                             "exactly as listed - or ask the user for what is "
                             "missing.")}

        # A browser is the LAST way to do something an app can do itself.
        # The field log has "play Love Story" becoming an Edge tab pointed at
        # open.spotify.com/search, which is a web page, not a song playing.
        better = c.app_for_url(str(arguments.get("url") or arguments.get("query") or ""))
        if better and better != found.app:
            return {"error": f"{better} can do this directly - use app "
                             f"{better!r} instead of opening a web page for it. "
                             f"Call desktop_action again with app={better!r}.",
                    "actions": [a.describe() for a in c.actions(app=better)][:20]}
        if found.risk.needs_confirmation:
            mgr = self._agents
            if mgr is None or not hasattr(mgr, "request_confirmation"):
                return {"error": "I cannot ask for confirmation right now, so I will not do it"}
            reason = (f"{found.key} - {found.description} "
                      f"({found.tier.label}"
                      + (", ON YOUR SCREEN" if found.tier.disturbs_the_user else ", in the background")
                      + ")")
            future = mgr.request_confirmation(reason, tool_name="desktop_action",
                                              arguments={"app": found.app, "action": found.name,
                                                         **arguments},
                                              grant_key=f"app_action:{found.key}",
                                              may_auto_approve=found.risk < Risk.SENSITIVE)
            if not future.done():
                # DO NOT WAIT HERE. The live model is blocked until a tool
                # returns, so awaiting the dialog meant Tora went silent for
                # as long as the box was on screen - fourteen seconds in the
                # field log, which is what "it just gets stuck" was. Hand
                # the answer back now and finish the job in the background;
                # _after_approval says how it went when it knows.
                self._submit(self._after_approval(future, found, dict(arguments)))
                return {
                    "awaiting_approval": True,
                    "action": found.key,
                    "note": ("Say in ONE short sentence that you need a yes and "
                             "the box is on screen. Then stop talking and wait - "
                             "do not try another way, and do not say it is done."),
                }
            try:
                approved = future.result()
            except Exception:  # noqa: BLE001
                approved = False
            if not approved:
                return {"ok": False, "action": found.key,
                        "note": "The user did not approve it. Say so plainly and do not try another way."}
        out = await c.run(app, action, arguments)
        return self._desktop_result(out)

    _BROWSER_APPS = ("chrome", "edge", "brave", "opera", "firefox", "vivaldi")

    def _browser_they_meant(self, c, app: str, action: str) -> str:
        """A website in a browser the user did not name goes to the one they
        are using ("browser"). "Open Gmail" with Chrome open went to Edge on a
        tester's PC (5 Oct 2026): the model picked the browser it had been
        told about, not the one in front of the user."""
        if action not in ("open_url", "search"):
            return app
        asked = (app or "").strip().lower()
        key = c.resolve_app(app) if app else ""
        key = key if key in self._BROWSER_APPS else (asked if asked in self._BROWSER_APPS else "")
        if not key:
            return app
        try:
            heard = self._heard_buf or []
        except (AttributeError, RuntimeError):     # nothing heard yet (a bare manager)
            heard = []
        said = " ".join(heard).lower()
        if not said.strip():
            return app                    # what they said is not known here: as asked
        from agents.control.apps.browsers import NAMES
        display, stem = NAMES.get(key, (key, key))
        if any(re.search(r"\b" + re.escape(w.lower()) + r"\b", said) for w in {key, display, stem}):
            return app
        _log.info("desktop_action: %s was not named - the browser in use instead", key)
        return "browser"

    @staticmethod
    def _desktop_result(out) -> dict:
        return {"ok": out.ok, "action": out.action, "app": out.app,
                "how": out.tier.label if out.tier else "", "result": out.message,
                "note": ("Read the result back in a few words." if out.ok
                         else "Tell the user what stopped it; do not pretend it worked.")}

    # A box left unanswered this long has lapsed (agent_manager's own expiry
    # is 180 s): the user went on to something else.
    APPROVAL_LAPSED_S = 170.0

    async def _after_approval(self, future, found, arguments: dict) -> None:
        """Wait for the dialog, run the action, and say how it went.

        This runs with nothing blocked on it. The conversation carried on
        while the box was open, so the outcome has to be volunteered rather
        than returned - see RealtimeConversation.say_now.
        """
        from agents.control import control
        asked_at = time.monotonic()
        try:
            approved = await asyncio.wrap_future(future)
        except Exception:  # noqa: BLE001 - a broken dialog is a refusal
            _log.debug("approval future failed", exc_info=True)
            approved = False

        if not approved and time.monotonic() - asked_at >= self.APPROVAL_LAPSED_S:
            # Nobody answered the box: they moved on. Saying "you didn't
            # approve opening Gmail" minutes later, in the middle of
            # something else, is Tora stuck on the past (5 Oct 2026).
            _log.info("%s lapsed unanswered - dropped quietly", found.key)
            return
        if not approved:
            _log.info("%s was not approved", found.key)
            await self._speak_aside(
                f"[The user did not approve {found.key}. Say so in a few words, "
                f"plainly, and do not try another way.]")
            return

        out = await control().run(found.app, found.name, arguments)
        _log.info("%s after approval -> %s", found.key, "ok" if out.ok else out.message)
        if out.ok:
            note = (f"[The user approved it and {found.key} has now run: "
                    f"{out.message}. Tell them in a few words. Do not thank them "
                    f"for approving.]")
        else:
            note = (f"[The user approved it but {found.key} did not work: "
                    f"{out.message}. Tell them what stopped it, briefly. Do not "
                    f"pretend it worked.]")
        await self._speak_aside(note)

    async def _speak_aside(self, note: str) -> None:
        """Have Tora volunteer something, once there is a gap to say it in."""
        conv = self._realtime
        if conv is None or not conv.ready:
            return
        try:
            await conv.say_now(note)
        except Exception:  # noqa: BLE001 - never let a follow-up break a turn
            _log.debug("could not deliver the follow-up", exc_info=True)

    async def _tool_manage_agents(self, args: dict) -> dict:
        """Voice control of the workforce: status, pause, resume, cancel,
        approve, deny. `task` is a title fragment; omitted means the most
        relevant one (a pending approval, else the newest active task)."""
        mgr = self._agents
        if mgr is None:
            return {"error": "the agent workforce is not available"}
        from agents.core import TaskStatus
        action = str(args.get("action") or "status").strip().lower()
        wanted = str(args.get("task") or "").strip().lower()

        def _summ(t):
            a = mgr.get_agent(t.agent_id or "")
            return {"task": t.title, "agent": a.spec.name if a else "", "status": t.status.value,
                    "progress": int(t.progress * 100), "current": t.current_action,
                    "steps_done": sum(1 for s in t.steps if s.status.value == "done"),
                    "steps_total": len(t.steps)}

        top = [t for t in mgr.list_tasks() if not (t.metadata or {}).get("parent_id")]
        if action == "status":
            active = [_summ(t) for t in top if not t.status.is_terminal]
            recent = [_summ(t) for t in top if t.status.is_terminal][:3]
            pending = mgr.pending_approvals()
            return {"ok": True, "active": active, "recent": recent,
                    "awaiting_approval": [p.reason for p in pending],
                    "note": ("Tell the user briefly. Nothing is running." if not active and not pending
                             else "Summarise in one breath.")}

        if action in ("approve", "deny"):
            pending = mgr.pending_approvals()
            if not pending:
                return {"error": "nothing is waiting for approval"}
            req = pending[-1]
            if action == "approve" and getattr(req, "sensitive", False):
                # Paying suppliers or filing a return is never a spoken yes.
                return {"ok": False, "what": req.reason,
                        "note": (f"This one is sensitive: the user must type {req.confirm_word} in the "
                                 "approval dialog on screen. Tell them that; do not approve it yourself.")}
            mgr.approve(req.id, action == "approve")
            return {"ok": True, "action": action, "what": req.reason}

        candidates = [t for t in top if not t.status.is_terminal]
        if wanted:
            candidates = [t for t in candidates if wanted in t.title.lower()] or candidates
        if not candidates:
            return {"error": "no running task" + (f" matching {wanted!r}" if wanted else "")}
        target = candidates[0]
        done = {"pause": mgr.pause, "resume": mgr.resume, "cancel": mgr.cancel}.get(action)
        if done is None:
            return {"error": "action must be status, pause, resume, cancel, approve or deny"}
        ok = done(target.id)
        return {"ok": bool(ok), "action": action, "task": target.title,
                "status": (mgr.get_task(target.id) or target).status.value}

    async def _delegate_if_task(self, text: str):
        """Cascade route: start a background task if the transcript is one.

        None means ordinary chat — the caller carries on to the model. The
        classifier may become provider-backed (M17), so it runs off this
        loop to keep a slow call from stalling the companion.
        """
        if self._agents is None:
            return None
        try:
            loop = asyncio.get_running_loop()
            task = await loop.run_in_executor(None, self._agents.submit, text)
        except Exception:
            _log.exception("agent delegation failed")
            return None
        if task is not None:
            await self._wait_for_plan(task)
        return task

    @staticmethod
    async def _wait_for_plan(task, timeout: float = 1.5):
        """Template planning is near-instant; give it a beat so the spoken
        acknowledgement can list the real steps instead of promising them."""
        deadline = time.monotonic() + timeout
        while not task.steps and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    # Every walkthrough command, and the one thing each does (GW-14):
    #   next    move on one step
    #   skip    the same move - counted as a skip, not as the step done
    #   back    the step before, said again (on step one: step one again)
    #   repeat  this step, said again
    #   stop    end it; the step it was on is kept for resume
    #   pause   the same as stop, and the reply says how to pick it back up
    #   resume  a stopped walkthrough picks up at its step; a running one
    #           is left alone
    #   where   point at this step's target again
    #   help    point again, and the model explains it another way
    #   do_it   until GW-20: say Tora can't yet, and point again
    # where, help and do_it never move the guide.
    _GUIDE_COMMANDS = ("next", "skip", "back", "repeat", "stop", "pause",
                       "resume", "where", "help", "do_it")
    _GUIDE_STAYS = ("where", "help", "do_it")
    # (request serial, move) last refused as words the check did not know (LG-03).
    _guide_refused = None
    # Also set in __init__; the class default keeps every reader safe on a
    # manager that has not run it.
    _plan_in_progress = None

    async def _tool_guide_step(self, args: dict) -> dict:
        action = str(args.get("action") or "").strip().lower().replace(" ", "_")
        if action not in self._GUIDE_COMMANDS:
            return {"error": "action must be one of: " + ", ".join(self._GUIDE_COMMANDS)}
        runner = self._guide_runner
        running = runner if runner is not None and runner.running else None
        if action == "resume":
            return self._guide_resume(running)
        if running is None:
            return {"error": "no walkthrough is running"}
        if action in self._GUIDE_STAYS:
            return await self._guide_stay(action, runner)
        if action in ("stop", "pause"):
            # Never second-guessed: a refused stop is the worse mistake.
            self._count_said(action)
            runner.request(action)
            if action == "pause":
                return {"ok": True, "action": "pause",
                        "note": ("The walkthrough is paused on the step it was "
                                 "on. Say so in a few words, and that saying "
                                 "'resume' picks it back up.")}
            return {"ok": True, "action": "stop",
                    "note": "The walkthrough is stopped. Say so in a few words."}

        move = "next" if action == "skip" else action
        # Check the model's reading against what the person actually said.
        # Mid-walkthrough most speech is a question, and a question read as
        # "next" moves the guide on without them.
        checked = await self._check_guide_move(move, runner)
        if checked is not None:
            return checked

        # One move per thing the user SAID. The runner also refuses a
        # second move before the new step has been spoken, but that window
        # is only as long as the speech: a model looping every 0.6 s would
        # still land a move every few seconds. What the user meant by one
        # "next" is one step, however many times the model calls this.
        serial = self._request_serial
        on_first = runner.index <= 0
        if self._guide_moved_for == serial:
            accepted = False
        else:
            accepted = runner.request(move)
            if accepted:
                self._guide_moved_for = serial
                self._count_said(action)

        # Report where the guide is GOING, not where it was. This used to
        # read runner.index straight after request(), before the loop had
        # consumed the command, so a "next" on step 1 of 2 came back as
        # "step 1 of 2" - the model was told it had not moved, and called
        # next again. The field that was added to stop repeat calls was the
        # thing inviting them.
        heading, total = runner.heading, runner.total
        if heading >= total and runner.writing:
            where = (f"moving on to step {heading + 1} - it is still being "
                     f"planned and Tora will say it the moment it is ready")
        elif heading >= total:
            where = "the walkthrough is finishing - that was the last step"
        elif move == "back" and on_first:
            where = (f"still on step 1 of {total} - it is the first step, so "
                     f"Tora is saying it again")
        else:
            where = f"now on step {heading + 1} of {total}"
        out = {"ok": True, "action": action, "where": where,
               "note": ("Done - the move has happened and Tora is reading the "
                        "step aloud herself. Do NOT call guide_step again, and "
                        "do not read the step out; stay quiet or answer "
                        "briefly.")}
        if not accepted:
            out["note"] = ("That was already done: the guide moved once and is "
                           "speaking the new step now. Do NOT call guide_step "
                           "again for this.")
        return out

    async def _check_guide_move(self, move: str, runner) -> Optional[dict]:
        """A reply refusing a move the person did not ask for, or None to go
        ahead. See tutor_features/guide_intent.py for why this exists.

        An empty transcript is never grounds for refusing - it can lag the
        tool call, and blocking on missing evidence would make "next"
        unreliable in exactly the case it has to work.
        """
        heard = " ".join(self._heard_buf or []).strip()
        if not heard:
            return None
        from tutor_features.guide_intent import guide_intent, looks_like_command
        intent = guide_intent(heard)
        if intent == move:
            return None
        # The model heard the audio; this reads a transcript of it. Asked for
        # the same move twice for the same short, command-shaped words, it is
        # the transcript that is wrong - "next" came back as "net" on 24 Sep,
        # and refusing it cost 41 calls and a frozen guide (LG-03). A
        # question, an "okay", a "not yet" are never let through this way.
        key = (self._request_serial, move)
        if intent is None and looks_like_command(heard):
            if self._guide_refused == key:
                _log.info("guide_step %s: asked twice for %r - taking the model's word",
                          move, heard[:60])
                return None
            self._guide_refused = key
        if intent in self._GUIDE_STAYS:
            # "Where is it?", "I'm stuck" or "do it for me", read as a move.
            # Do what they asked instead - none of those moves the guide.
            _log.info("guide_step %s refused: they said %s (%r)", move, intent, heard[:60])
            out = await self._guide_stay(intent, runner)
            asked = {"where": "WHERE it is", "help": "for HELP",
                     "do_it": "Tora to DO it"}[intent]
            out["note"] = f"They asked {asked}, not to move on. " + out["note"]
            return out
        _log.info("guide_step %s refused: %r is %s, not a %s", move, heard[:60],
                  intent or "a question", move)
        if intent == "ack":
            note = ("They only acknowledged the step. The guide moves on by itself "
                    "the moment they do it - say nothing, or a word at most.")
        elif intent in ("next", "back", "repeat", "stop", "pause"):
            note = (f"They said {intent}, not {move}. Call guide_step with "
                    f"action {intent!r} instead, once.")
        else:
            note = ("They asked something - they did not ask to move on. Answer "
                    "them; the walkthrough stays exactly where it is.")
        return {"ok": True, "moved": False,
                "where": f"still on step {runner.index + 1} of {runner.total}",
                "note": note}

    async def _guide_stay(self, action: str, runner) -> dict:
        """where / help / do_it: help with the step on screen without moving
        off it. Tora only points; the live model does the talking, so there
        is one voice and not two."""
        plan = self._lesson_plan
        idx = runner.index
        step = plan.steps[idx] if plan is not None and 0 <= idx < len(plan.steps) else None
        target = (getattr(step, "target", "") or "") if step is not None else ""
        if target:
            self._submit(self._point_step_target(idx, step))
        self._count_said(action)
        arrow = f'The arrow is going to "{target}". ' if target else ""
        if action == "where":
            note = arrow + ("Say where it is in a few words." if target else
                            "This step has no one thing to point at - say in a "
                            "few words where to look.")
        elif action == "help":
            # The runner's own offer of help, after a still minute, would be
            # this help given twice.
            runner.mark_helped()
            other = self._step_alternative(step) if step is not None else ""
            note = (f'They are stuck on: "{getattr(step, "instruction", "")}". '
                    + arrow
                    + (f'Another way to do it: "{other}". ' if other else "")
                    + "Explain it one different way in one or two short "
                    "sentences - do not just repeat the step.")
        else:
            # "Do it for me" is GW-20. Until then, say so plainly rather
            # than pretend, and make doing it themselves as easy as it gets.
            note = ("Tora cannot do steps for them yet. Say so in one short "
                    "sentence" + (", and that the arrow is on exactly what to "
                                   "use." if target else "."))
        _log.info("guide step %d: %s - staying on it", idx + 1, action)
        return {"ok": True, "moved": False,
                "where": f"still on step {idx + 1} of {runner.total}",
                "note": note + " Do not call guide_step again."}

    def _guide_resume(self, running) -> dict:
        """'resume': a stopped walkthrough picks up at the step it was on.

        With one already running there is nothing to resume - restarting it
        would only say its step again - so it is left alone.
        """
        if running is not None:
            return {"ok": True, "moved": False,
                    "where": f"still on step {running.index + 1} of {running.total}",
                    "note": ("The walkthrough is already running - there is "
                             "nothing to resume. Leave it be.")}
        saved = self._resume
        if not saved or not self.resume_guide():
            return {"error": "there is no stopped walkthrough to resume"}
        guide, idx = saved
        self._count_said("resume")
        return {"ok": True, "action": "resume",
                "where": f"back on step {idx + 1} of {len(guide.steps)}",
                "note": ("Tora picks it up there and says the step herself - "
                         "say a few words at most, and do not read the step "
                         "out.")}

    def _count_said(self, command: str) -> None:
        """Which commands people actually use. A step that draws "where is
        it?" or "I'm stuck" is a badly planned step - GW-21 learns from it."""
        stats = self._guide_stats
        if stats is not None:
            said = stats.setdefault("said", {})
            said[command] = said.get(command, 0) + 1

    @staticmethod
    def _step_alternative(step) -> str:
        """A genuinely different route to the step's end, if the planner gave
        one - never a rewording of the instruction itself."""
        for other in (getattr(step, "possible_actions", None) or []):
            if other and other.lower() not in step.instruction.lower():
                return other
        return ""

    async def _tool_point_at(self, args: dict) -> dict:
        """Put the pointer on something the user asked about.

        Starts with the accessibility tree (a ~5 ms local lookup — the whole
        reason "where is X?" can be answered before the sentence finishes) and
        only pays for a screenshot + vision call when the tree and OCR both
        miss. Either way the outcome is an arrow on screen, not just words:
        see _resolve_point for the full chain.
        """
        name = str(args.get("name") or "").strip()
        if not name:
            return {"error": "no name given"}

        # The model BLOCKS on a tool result, so anything slow here is dead air
        # — it cannot say a word until this returns. Resolving a target can
        # take a second or two, and it used to take twenty-five.
        #
        # So: give the resolver a brief window to win outright, and otherwise
        # hand back "on its way" immediately and let it finish in the
        # background. The assistant talks while the arrow flies, the way a
        # person points and explains at the same time, instead of going silent
        # and then describing the location in words.
        task = asyncio.ensure_future(self._point_to(name, hold=False))
        try:
            found = await asyncio.wait_for(asyncio.shield(task), timeout=0.4)
        except asyncio.TimeoutError:
            return {
                "ok": True,
                "pointing_at": name,
                "note": (
                    "The pointer is on its way — say where it is in the same "
                    "breath, do not wait."
                ),
            }
        if found:
            return {"found": True, "pointed_at": name}
        return {
            "found": False,
            "hint": (
                "Couldn't find that on screen. It may be scrolled off, on "
                "another tab, or behind another window — tell the user where "
                "to look, or that it isn't visible right now."
            ),
        }

    # A capture is reused for this long, so the look, any drawing on it, and a
    # follow-up look within the same breath share one screen grab instead of
    # three. Coordinate accuracy is unaffected — the grab keeps full physical
    # dimensions; only the JPEG that is uploaded is downscaled.
    _SHOT_CACHE_S = 1.5

    async def _capture_shots(self, *, max_width: int = 1152, reuse: bool = True, pixels: bool = True):
        """Capture the screen, reusing a very recent grab when allowed.

        max_width is the width of the JPEG sent to the model — smaller uploads
        and infers faster. The fast glance uses a lighter image than the
        careful look, which wants every pixel it can get for reading.
        pixels=False: every monitor's numbers only, no picture - the caller
        grabs the one it needs (FS-05). Never cached: a cache hit must have
        its pictures.
        """
        if (reuse and self._screens_ctx
                and (time.monotonic() - self._screens_at) < self._SHOT_CACHE_S):
            return self._screens_ctx
        if not pixels:
            return await asyncio.to_thread(monitor_frames)
        screens = await asyncio.to_thread(capture_all_screens, max_width)
        if screens:
            self._screens_ctx = screens
            self._screens_at = time.monotonic()
        return screens

    async def _one_shot(self, max_width: int = 1024):
        """The active monitor's picture, and no other's (SC-11): a very recent
        grab reused, else that monitor's numbers and ONE grab of it. Grabbing
        and encoding all three of the owner's monitors to use one took 331 ms
        (9 Oct); looks stopped doing it in FS-05, and now so do pointing by
        vision, the walkthrough's planner and its step checks. None when the
        screen cannot be captured."""
        screens = await self._capture_shots(max_width=max_width, pixels=False)
        if not screens:
            return None
        shot = self._active_shot(screens) or screens[0]
        if shot.base64_jpeg:
            return shot                  # a recent grab, with its pictures
        return await asyncio.to_thread(grab_frame, shot, max_width)

    def _active_shot(self, screens=None):
        """The ScreenShot for the monitor the user's active window is on.

        With more than one monitor, mss's screens[0] is not necessarily where
        the user is working — on a three-monitor setup it is often the far
        one. Pointing that used screens[0] looked at, and pointed on, the
        WRONG screen. This picks the monitor whose physical bounds contain the
        foreground window, so vision and coordinate mapping always work on the
        screen the user is actually looking at.
        """
        screens = screens if screens is not None else self._screens_ctx
        if not screens:
            return None
        try:
            from tutor import foreground_window_center
            c = foreground_window_center()
            if c is not None:
                cx, cy = c
                for s in screens:
                    if (s.physical_left <= cx < s.physical_left + s.physical_width
                            and s.physical_top <= cy < s.physical_top + s.physical_height):
                        return s
        except Exception:
            pass
        return screens[0]

    def _dpi_scale(self) -> float:
        """Physical-to-logical pixel ratio for the active monitor."""
        shot = self._active_shot()
        if shot is not None:
            return shot.dpi_scale or 1.0
        try:
            from screen.capture import _query_dpi_scale
            return _query_dpi_scale() or 1.0
        except Exception:
            return 1.0

    async def _resolve_point(self, query: str, *, shot=None, use_reference: bool = True,
                             context: str = "", vision: bool = True):
        """Resolve a natural-language target to LOGICAL screen pixels.

        The chain that makes "take me there" reliable, cheap → strong, first
        hit wins:

          0. coreference              ~5 ms    "that" / "the one to its left"
                                               resolved against the last arrow
          1. UIA accessibility tree   ~5 ms    pixel-exact, any named control
          2. OCR                      ~300 ms  canvas apps with no tree
          3. the vision model         ~1 s     a grounded box for ANYTHING
                                               visible — a game, a photo, an
                                               unlabelled icon

        Only when all miss is the thing genuinely not on screen. Returns
        SimpleNamespace(x, y, label) in logical px, or None.
        """
        from types import SimpleNamespace
        query = (query or "").strip()
        if not query:
            return None

        # 0 — is the user pointing back at something already on screen?
        if use_reference:
            from tutor import deixis_direction, is_backref
            deictic = is_backref(query) or bool(deixis_direction(query))
            if deictic:
                ref = await self._resolve_reference(query)
                # A pronoun ("that", "the one next to it") means nothing to UIA
                # or the vision model, so don't fall through and let them guess
                # — a stale or missing reference is simply "not found".
                return ref

        # 1a — the control snapshot already gathered for this turn's context.
        # Free: it is in memory, and the model almost always calls point_at
        # with a name taken from the very list we handed it, so this is the
        # common case and it resolves in microseconds instead of a tree walk.
        snap = self._match_snapshot(query)
        if snap is not None:
            snap.via = "snapshot"
            return snap

        # 1 + 2 — local and free, but not always fast. In an app with no
        # accessibility tree both miss, and waiting for that before asking
        # the model made every walkthrough arrow in CapCut 13-32 s late. So
        # the model is asked as well once they have had LOCAL_GRACE_S - at
        # once in an app already known to have no tree - and the first
        # answer wins (APP-23).
        from ai.hybrid_pointer import find_target
        # `context` is what the target is for - a walkthrough step's own
        # instruction. It breaks ties between same-named controls (LG-05).
        local = asyncio.ensure_future(asyncio.to_thread(
            find_target, query, screenshot=shot, context=context,
            skip_ocr=bool(vision) and self._uia_rich()))
        local.add_done_callback(_retrieve)
        if not vision:
            # A look-ahead (LG-06): free tiers only, never a paid call.
            await asyncio.wait({local})
            return self._local_hit(local, shot, query)
        grace = 0.0 if self._uia_poor() else self.LOCAL_GRACE_S
        if grace:
            done, _ = await asyncio.wait({local}, timeout=grace)
            if done:
                hit = self._local_hit(local, shot, query)
                return hit if hit is not None else await self._vision_point(
                    query, shot, context=context)
        vision = asyncio.ensure_future(self._vision_point(query, shot, context=context))
        try:
            done, _ = await asyncio.wait({local, vision}, return_when=asyncio.FIRST_COMPLETED)
            if local in done:
                hit = self._local_hit(local, shot, query)
                if hit is not None:
                    vision.cancel()
                    return hit
                return await vision
            found = vision.result()
            if found is not None:
                return found
            # The model could not place it; OCR may still read it as text.
            await asyncio.wait({local})
            return self._local_hit(local, shot, query)
        except asyncio.CancelledError:
            vision.cancel()
            raise

    # How long UIA and OCR have before the vision model is asked as well.
    # UIA answers well inside this wherever it can answer at all (0.9 s in
    # the field log); OCR of a 1080p screen takes ~2 s even on an idle PC.
    LOCAL_GRACE_S = 1.0
    # An app whose accessibility tree names this few controls has no tree
    # worth waiting for: CapCut's named 1.
    UIA_POOR_AT = 2
    # SC-12: a tree naming this many controls had its chance - what it missed
    # is not text, so OCR (0.6-1 s of every core, and a word matched in the
    # wrong place) is skipped and the vision model has it alone.
    UIA_RICH_AT = 30
    _uia_counts = None                  # app key -> controls its tree named

    def _note_uia(self, title: str, count: int) -> None:
        if self._uia_counts is None:
            self._uia_counts = {}
        self._uia_counts[app_key(title or "")] = int(count)
        if count <= self.UIA_POOR_AT:
            from ai.hybrid_pointer import preload_ocr
            preload_ocr()                       # OCR is this app's local tier

    def _uia_poor(self) -> bool:
        """Does the app in front expose (next to) no controls at all?"""
        n = (self._uia_counts or {}).get(app_key(active_window_title() or ""))
        return n is not None and n <= self.UIA_POOR_AT

    def _uia_rich(self) -> bool:
        n = (self._uia_counts or {}).get(app_key(active_window_title() or ""))
        return n is not None and n >= self.UIA_RICH_AT

    def _local_hit(self, task, shot, query: str):
        """A finished local lookup as logical pixels, or None."""
        from types import SimpleNamespace
        try:
            target = task.result()
        except BaseException:  # noqa: BLE001 - a failed local tier is a miss
            return None
        if target is None:
            return None
        # Physical px (UI Automation, OCR) to the overlay's logical px, on
        # the monitor the target is on - each monitor has its own scaling.
        bbox = None
        if getattr(target, "bbox", None):
            bbox = monitors.rect_to_logical(*target.bbox)
        x, y = monitors.to_logical(target.x, target.y)
        return SimpleNamespace(
            x=x, y=y,
            label=(target.label or query), bbox=bbox,
            via=getattr(target, "source", "") or "local",
        )

    async def _vision_point(self, query: str, shot=None, *, context: str = ""):
        """Tier 3: the vision model. It sends a screenshot, so the privacy
        guard applies here - unlike the local tiers, which never leave the
        machine. None when it cannot place it, or cannot be asked."""
        from types import SimpleNamespace
        try:
            if shot is None:
                if self._privacy_guard and is_sensitive_window(active_window_title()):
                    return None
                shot = await self._one_shot(1024)
                if shot is None:
                    return None
            found = await gemini_vision.locate(
                query,
                hint=context,
                screenshot_jpeg_b64=shot.base64_jpeg,
                original_width=shot.width,
                original_height=shot.height,
                physical_width=shot.physical_width,
                physical_height=shot.physical_height,
                physical_left=shot.physical_left,
                physical_top=shot.physical_top,
                dpi_scale=shot.dpi_scale,
                screen_index=shot.index,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed look is "not found"
            _log.debug("vision locate failed for %r", query, exc_info=True)
            return None
        if found is None:
            return None
        return SimpleNamespace(
            x=found.x, y=found.y, label=(getattr(found, "label", "") or query),
            via="vision",
        )

    # How long the control snapshot taken with a question may answer "where
    # is it" without walking again: the same turn, not the next screen.
    SNAPSHOT_TRUST_S = 10.0
    _pointables_at = 0.0
    _pointables_title = ""

    def _snapshot_fresh(self) -> bool:
        """May the control snapshot still be trusted for positions?

        It was taken when the user last spoke. A walkthrough step presented
        a minute and three screens later used to be resolved against it, so
        a control that shared a name with one from back then - "Save" in a
        dialog and in the main window - got the arrow where the OLD one was
        (APP-04). Now: only for a few seconds, only in the same window, and
        never once a walkthrough step has ended (the screen was acted on).
        """
        if not self._pointables_at:
            return False
        if time.monotonic() - self._pointables_at > self.SNAPSHOT_TRUST_S:
            return False
        return active_window_title() == self._pointables_title

    def _match_snapshot(self, query: str):
        """Best match from this turn's control snapshot, or None.

        Pure in-memory scoring over the (cx, cy, name, type) list already
        walked for the screen context — no COM calls, no tree walk. Applies
        the same ambiguity margin as the UIA search so a coin-flip between two
        similar names defers to the slower, better resolvers instead of
        guessing. Only while the snapshot is fresh (_snapshot_fresh).
        """
        from types import SimpleNamespace
        from ai.hybrid_pointer import _score_match
        if not self._snapshot_fresh():
            return None
        best = None
        second = 0.0
        for cx, cy, name, ctype in (self._pointables or []):
            score = _score_match(query, name, ctype)
            if best is None or score > best[0]:
                second = best[0] if best is not None else second
                best = (score, cx, cy, name)
            elif score > second:
                second = score
        if best is None or best[0] < 0.6:
            return None
        if best[0] < 0.85 and (best[0] - second) < 0.15:
            return None                       # ambiguous — let the real search decide
        score, cx, cy, name = best
        x, y = monitors.to_logical(cx, cy)
        return SimpleNamespace(x=x, y=y, label=name)

    async def _point_to(self, query: str, *, hold: bool = True, shot=None) -> bool:
        """Resolve `query` and fly the buddy there. True if it landed."""
        self._activity("finding")
        try:
            found = await self._resolve_point(query, shot=shot)
        finally:
            self._activity("")
        return self._show_point(found, query, hold=hold)

    def _show_guide_point(self, found, query: str) -> bool:
        """A walkthrough step's arrow: there in a blink and held on the
        target until the step changes (LG-02), with its ring."""
        self._last_found = found
        if found is None:
            return False
        label = (found.label or query)[:24]
        self.sig_guide_point.emit(float(found.x), float(found.y), label)
        self._highlight(found)
        self._remember_ref(found.x, found.y, label)
        return True

    def _show_point(self, found, query: str, *, hold: bool = True) -> bool:
        """Fly the buddy to something already resolved. True if it landed."""
        self._last_found = found
        if found is None:
            return False
        if hold:
            self.sig_point_hold.emit(True)
            self._pointing_held = True
        label = (found.label or query)[:24]
        self.sig_point_at.emit(float(found.x), float(found.y), label)
        self._highlight(found)
        self._pointed_this_turn = True
        self._remember_ref(found.x, found.y, label)
        return True

    def _highlight(self, found) -> None:
        """Ring whatever we just pointed at.

        The arrow says "over there"; the ring says "THIS". We already know the
        target's exact bounds from the accessibility tree, so marking it costs
        nothing — and this is the drawing opportunity that was being missed on
        nearly every single interaction, because drawing previously required a
        screenshot the assistant had usually been told not to take.
        """
        bbox = getattr(found, "bbox", None)
        try:
            if bbox:
                l, t, r, b = bbox
                pad = 4.0
                self.sig_draw.emit({
                    "kind": "rect", "color": "tora",
                    "x1": l - pad, "y1": t - pad, "x2": r + pad, "y2": b + pad,
                })
            else:
                self.sig_draw.emit({
                    "kind": "circle", "color": "tora",
                    "x": float(found.x), "y": float(found.y), "r": 32.0,
                })
        except Exception:
            _log.debug("highlight failed", exc_info=True)

    def _remember_ref(self, x: float, y: float, label: str) -> None:
        """Record the last thing pointed at, so 'that'/'it' can find it again."""
        from types import SimpleNamespace
        self._last_ref = SimpleNamespace(
            x=float(x), y=float(y), label=label,
            at=time.monotonic(), app=app_key(active_window_title()),
        )

    # How long a reference stays "the thing we were just talking about".
    _REF_TTL_S = 120.0

    def _ref_fresh(self) -> bool:
        """True while the last arrow's coordinates still mean something.

        Absolute screen pixels only make sense in the window they were taken
        in, so a reference expires both on a timer and the moment the user
        switches apps — pointing at an old spot in a window that is no longer
        there would send the buddy somewhere meaningless.
        """
        ref = self._last_ref
        return (
            ref is not None
            and (time.monotonic() - ref.at) < self._REF_TTL_S
            and ref.app == app_key(active_window_title())
        )

    async def _resolve_reference(self, query: str):
        """Resolve a back-reference or a relative direction to screen pixels.

        "that" / "the same" / "go back there"  → the last arrow's spot.
        "the one to its left" / "below that"   → the nearest control in that
                                                  direction from an anchor,
                                                  where the anchor is a named
                                                  control if the query gives
                                                  one ("left of the File menu")
                                                  or the last arrow otherwise.
        Returns SimpleNamespace(x, y, label) in logical px, or None when the
        query is not a reference (so the normal chain runs).
        """
        from types import SimpleNamespace
        from tutor import deixis_direction, is_backref, is_pronoun, anchor_after_of

        ref = self._last_ref
        fresh = self._ref_fresh()

        direction = deixis_direction(query)
        if direction:
            anchor_phrase = anchor_after_of(query)
            anchor = None
            if anchor_phrase and not is_pronoun(anchor_phrase):
                # "to the left of the File menu" — resolve the named anchor,
                # without recursing back into reference handling.
                anchor = await self._resolve_point(anchor_phrase, use_reference=False)
            if anchor is None and fresh:
                anchor = ref
            if anchor is None:
                return None
            from ai.hybrid_pointer import nearest_in_direction
            # A stale snapshot is no snapshot: nearest_in_direction then walks
            # the screen as it is now (APP-04).
            snapshot = (self._pointables or None) if self._snapshot_fresh() else None
            ax, ay = monitors.to_physical(anchor.x, anchor.y)
            nb = await asyncio.to_thread(
                lambda: nearest_in_direction(ax, ay, direction, candidates=snapshot)
            )
            if nb is None:
                return None
            x, y = monitors.to_logical(nb.x, nb.y)
            return SimpleNamespace(x=x, y=y, label=nb.label or "there")

        if is_backref(query) and fresh:
            return SimpleNamespace(x=ref.x, y=ref.y, label=ref.label or "there")

        return None

    async def _ensure_pointed(self, transcript: str, said: str = "") -> None:
        """Safety net: a locate question must end with an arrow, not words.

        The live model is told to point, but it does not always — sometimes it
        just describes the location, sometimes it calls point_at with a name
        the tree does not have. This runs after any locate turn that produced
        no arrow, resolves the target itself through the full chain (which
        ends in the vision model, so it works even when there is no name), and
        points. It is what turns "once in a while" into "almost always".

        A what-to-do turn (UD-04) points at what the ANSWER said to act on -
        "Click Checkout to pay" - through the same chain, free tiers first: at
        most the one look a correct turn would have made.
        """
        if not is_locate(transcript):
            label = _target_from_answer(said)
            if not label:
                return
            _log.info("the answer drew nothing - pointing at %r from its words", label)
        else:
            label = _guess_label(transcript)
        if label in ("right here!", "here"):
            label = transcript                # no clean target — let vision read it
        if await self._point_to(label, hold=True):
            await asyncio.sleep(4.0)          # dwell while the user looks
            if self._pointing_held:
                self.sig_point_release.emit()
                self._pointing_held = False

    async def _tool_look_at_screen(self, args: dict) -> dict:
        """The expensive one: capture the screen and actually look at it.

        What goes back to the voice is the line to SAY and what is now drawn,
        never the look's readings. Handed those (piece lists, values), the
        voice read them out - "Black has 7 pawns... let me recount" - instead
        of answering (SN-02). For a what-should-I-do question exactly one
        thing is drawn: the move as an arrow, or the one thing to click
        (SN-03). See docs/SHOW_THE_NEXT_MOVE.md."""
        question = str(args.get("question") or "").strip()
        if not question:
            return {"error": "no question given"}
        self._mark_answer("tool")

        title = active_window_title()
        if self._privacy_guard and is_sensitive_window(title):
            return {
                "error": (
                    "That window looks sensitive (password manager, banking, "
                    "login) so no screenshot was taken. Tell the user that."
                )
            }

        # The careful look wants full resolution to read dense content and
        # never reuses a lighter cached grab; the fast glance takes a smaller,
        # possibly-cached image.
        self._activity("screen")
        careful = bool(args.get("careful"))
        # FS-01: rub out what Tora drew BEFORE the screenshot, not after it.
        # Her last arrow was in the next look's picture - the model saw its
        # own stale move on the board - and it made a changed screen and an
        # unchanged one hard to tell apart.
        self.sig_clear_drawings.emit()
        if self._drawings_on_screen():
            await asyncio.sleep(self.CLEAR_BEFORE_SHOT_S)
        width = 1280 if careful else 1024
        if net_quality.slow():
            # A weak connection: about half the bytes. The fast look sees the
            # picture at "medium" anyway (gemini_vision._FAST_RESOLUTION).
            width = 960 if careful else 768
        shot_t0 = time.monotonic()
        screens = await self._capture_shots(max_width=width, reuse=not careful, pixels=False)
        if not screens:
            return {"error": "could not capture the screen"}

        shot = self._active_shot(screens) or screens[0]
        cursor = look_region.cursor_position()
        # SP-10: "what's this?" with the mouse on another monitor means that
        # monitor - and the window under the mouse, not the one in front.
        by_mouse = False
        if look_region.points_at_mouse(question):
            try:
                i = look_region.frame_under(cursor, [(s.physical_left, s.physical_top, s.physical_width,
                                                      s.physical_height) for s in screens])
            except AttributeError:      # a shot without monitor numbers (tests)
                i = None
            if i is not None and screens[i] is not shot:
                shot, by_mouse = screens[i], True
        # UD-02: the window in front, sharper, and the mouse as a hint. A
        # shot without monitor numbers (tests) looks at what it was given.
        # FS-05: one picture - the window, or else that monitor - not every
        # monitor encoded and then the window grabbed again.
        image, monitor, region, window = shot.base64_jpeg, None, None, None
        try:
            monitor = (shot.physical_left, shot.physical_top, shot.physical_width, shot.physical_height)
            from tutor import foreground_window_rect
            window = look_region.window_at(*cursor) if by_mouse else foreground_window_rect()
            region = look_region.window_region(window, monitor)
            if region:
                grabbed = await asyncio.to_thread(capture_region, *region, width)
                image, region = (grabbed[0], region) if grabbed else (image, None)
        except Exception:  # noqa: BLE001 - the whole monitor is always a fine fallback
            region = None
        if not image:
            whole = await asyncio.to_thread(grab_frame, shot, width)
            if whole is None:
                return {"error": "could not capture the screen"}
            image = whole.base64_jpeg
        hint_kind = "mouse hint"
        traced = [r.focus.rect for r in self._trace_readings] if monitor else []
        if traced and not gemini_vision.needs_judgement(question, active_window_title()):
            # TF-06: traced while asking - the picture IS the traced area, with
            # room round it, at full sharpness. (A game keeps its whole board.)
            crop = look_region.trace_crop(traced, monitor)
            grabbed = await asyncio.to_thread(capture_region, *crop, width) if crop else None
            if grabbed:
                image, region, hint_kind = grabbed[0], crop, "trace crop"
        shot_ms = (time.monotonic() - shot_t0) * 1000
        hint = look_region.pointer_hint(question, cursor, region or monitor) if monitor else ""
        if traced:
            # Where on this image they traced, in order (TF-05/06).
            trace_line = look_region.trace_hint(traced, region or monitor)
            if trace_line:
                hint = trace_line
                hint_kind = "trace crop" if hint_kind == "trace crop" else "trace hint"
        self._mouse_shot = (shot, time.monotonic()) if by_mouse else None
        if by_mouse:
            _log.info("look: the monitor under the mouse (%s), not the window in front's",
                      getattr(shot, "index", "?"))

        # UD-05: asked again about a screen that has not changed - the last
        # look's answer and drawing again, and no call.
        sk = await asyncio.to_thread(look_region.sketch, image)      # a JPEG decode: off the loop
        again = self._reusable_look(question, sk, title)
        if again is not None:
            for tag in again["tags"]:
                sh = self._shape_from_tag(tag)
                if sh:
                    self.sig_draw.emit(sh)
            if again["tags"]:
                self._pointed_this_turn = True
                self._mark_answer("drawing", "redrawn")
            self._mark_answer("look", "reused: the screen had not changed, no call")
            self._note_answer(path="reused", cost=0.0)
            return dict(again["result"], reused=True)

        # 10 Oct: a chess move is the engine's, on the exact position -
        # followed from the last one Tora was sure of (no model at all), or
        # read off the screen by a fast model - before any slow look.
        title_now = active_window_title()
        chess = await self._play_chess(image) if gemini_vision.needs_judgement(question, title_now) else None
        # UD-01: fast first; the deep model only when the fast look is not sure.
        look = chess[0] if chess else await gemini_vision.look(question, image, context=hint, title=title_now)
        usage = getattr(look, "usage", None) or {}
        cost = float(getattr(look, "cost", 0.0) or 0.0)
        path = getattr(look, "path", "") or ("deep" if getattr(look, "deep", False) else "fast")
        self._note_answer(path=path, cost=cost)
        if chess:
            detail = "capture %d ms, %s, $%.4f" % (shot_ms, chess[1], cost)
        else:
            detail = "capture %d ms, %s %s, %s in / %s out / %s thinking, $%.4f" % (
                shot_ms, path, getattr(look, "model", "") or "?", usage.get("promptTokenCount", "?"),
                usage.get("candidatesTokenCount", "?"), usage.get("thoughtsTokenCount", 0), cost)
        if usage.get("server_total_ms"):
            # FS-06: through HeyTora's server - its own share, step by step.
            detail += ", server %d ms (sign-in %d, usage %d, Google %d)" % (
                usage["server_total_ms"], usage.get("server_auth_ms", 0),
                usage.get("server_consume_ms", 0), usage.get("server_google_ms", 0))
        if region:
            detail += ", window only"
        if hint:
            detail += f", {hint_kind}"
        if not look:
            self._mark_answer("look", detail)
            return {"error": "could not read the screen clearly"}
        if look.observations:
            _log.info("look read: %s", "; ".join(look.observations[:8])[:600])

        # A what-to-do question gets ONE thing drawn: the move, else the first
        # action. Anything else rings what the answer rests on, as before.
        actions = list(look.actions or [])
        if gemini_vision.is_next_action(question):
            moves = [a for a in actions if getattr(a, "is_move", False)]
            actions = (moves or actions)[:1]
        # 10 Oct: "knight from f3 to e4" - a move no knight can make. In a
        # chess game the engine plays the position the screen shows, read
        # by the look and checked square by square (ai/look_chess.py).
        played = await asyncio.to_thread(look_chess.play, look, image) if getattr(look, "chess", None) else None
        if played:
            look.answer = played.say
            actions = [played.action] if played.action else []
            detail += ", " + played.note
            self._chess_game = played.game
        else:
            # 10 Oct: "knight from g1 to f3" four times running, and every
            # arrow ended on g3 - the words right, the box a file off. On a
            # board a move is drawn between the squares the answer names.
            on_board = await asyncio.to_thread(look_board.place_moves, actions, image, look.answer)
            if on_board:
                detail += ", " + on_board
        if region:
            for a in actions:
                a.box = look_region.to_monitor_box(a.box, region, monitor)
                if getattr(a, "to_box", None):
                    a.to_box = look_region.to_monitor_box(a.to_box, region, monitor)
        snapped = await self._snap_to_controls(actions, monitor, window) if monitor else 0
        if snapped:
            detail += f", {snapped} snapped to the real control"
        self._mark_answer("look", detail)
        speed.mark("look")
        drawn = []
        for action in actions[:4]:
            sh = self._shape_from_tag(action.as_tag())
            if sh:
                self.sig_draw.emit(sh)
                drawn.append(action)
        if drawn:
            self._pointed_this_turn = True
            self._mark_answer("drawing", ", ".join(
                "arrow" if getattr(a, "is_move", False) else a.kind for a in drawn))

        # Where each highlight is, in the draw tool's 0-1000 coordinates.
        # The voice model never sees the screen: without these, a drawing it
        # adds after a look is a guess - in the first launch it ringed empty
        # space beside the line this look had boxed correctly.
        result = {
            "say": _one_line(look.answer),
            "drawn": [("arrow: " if getattr(a, "is_move", False) else f"{a.kind}: ") + a.label for a in drawn],
            "highlights": [{"label": a.label, "tag": a.as_tag()} for a in drawn],
        }
        self._last_look = {"sketch": sk, "question": _norm_question(question), "at": time.monotonic(),
                           "result": result, "tags": [a.as_tag() for a in drawn]}
        return result

    # ── A chess game: the engine's move on the exact position ────────────────
    #
    # 10 Oct, after the engine went in (1.7.10): the first move was right,
    # then the deep model was busy, the fast look misread a fourteen-move
    # list, and Tora fell back on the model's own moves - four different
    # arrows in one turn. Now the game is FOLLOWED: the position Tora was
    # last sure of, moved on by their move and the reply, found from the
    # picture alone in ~20 ms. Only a new game, or one that ran on further,
    # is READ - by a fast model that only copies the move list, at full
    # resolution - and every reading must match the board square by square.

    _chess_game: Optional["look_chess.Game"] = None
    CHESS_MISSES = "chess_misses"

    async def _play_chess(self, image: str):
        """(a Look holding the engine's answer, its line for the log) - or
        None when there is no board or it could not be read, and the look
        answers instead."""
        seen = await asyncio.to_thread(look_chess.see, image)
        if seen is None:
            return None
        found = await asyncio.to_thread(look_chess.follow, self._chess_game, seen)
        cost, model, readings = 0.0, "", []
        if found is not None:
            (pos, last), wb = found, self._chess_game.white_bottom
            note = "chess: followed from the last position, no model"
        else:
            for model in gemini_vision.fast_models():
                reading = await gemini_vision.read_game(image, model=model)
                if reading is None:
                    continue
                cost += float(reading.get("cost") or 0.0)
                readings.append(reading)
                got = await asyncio.to_thread(look_chess.from_reading, reading, seen)
                if got is not None:
                    pos, last, wb, source, wrong = got
                    note = f"chess: the position from {source}, read by {model}" + (
                        f" ({wrong} square unclear)" if wrong else "")
                    break
            else:
                from config import data_dir
                await asyncio.to_thread(look_chess.keep_miss, data_dir() / self.CHESS_MISSES, image, readings)
                return None
        played = await asyncio.to_thread(look_chess.answer, pos, wb, seen, note, last)
        self._chess_game = played.game
        look = gemini_vision.Look(answer=played.say, actions=[played.action] if played.action else [],
                                  model=model, path="chess", cost=cost)
        return look, played.note

    # ── UD-05: never pay twice for the same screen ──────────────────────────
    _last_look: Optional[dict] = None
    _LOOK_REUSE_S = 180.0
    CLEAR_BEFORE_SHOT_S = 0.1     # the overlay repaints, and Windows composes it, in this
    # SC-05: the wait is only for drawings that may still be showing - one
    # fades within ANNOTATION_TTL_S (14 s), one in a walkthrough stays.
    # Every look used to pay the 0.1 s, drawings or not.
    DRAWINGS_LAST_S = 15.0
    _drawn_at = 0.0
    _guide_runner = None                  # set in __init__; here for a manager built bare (tests)

    def _note_drawn(self, *_args) -> None:
        self._drawn_at = time.monotonic()

    def _drawings_on_screen(self) -> bool:
        guide = self._guide_runner
        return (time.monotonic() - self._drawn_at < self.DRAWINGS_LAST_S
                or (guide is not None and guide.running))

    # FS-01: on 9 Oct "Okay, I've done that. Where's my next best move?", "Okay,
    # look again" and "I've done that. Please do the next move." were all
    # answered from the look before - the old move, three times: "still stuck,
    # and didn't realise". Words that say the screen changed, or ask for a
    # fresh look, always look again; only asking to SEE the last answer again
    # re-shows it.
    _FRESH_RE = re.compile(
        r"\b(done|did (it|that)|i(?:'ve| have)? (?:done|moved|made|clicked|changed|played|typed)|now|next|"
        r"moved|changed|updated|look again|check again|try again|another|new)\b", re.IGNORECASE)
    _RESHOW_RE = re.compile(
        r"\b(show (me )?(that|it) again|where was (it|that)|remind me|repeat that|say that again|"
        r"one more time)\b", re.IGNORECASE)
    SAME_CELLS = 2          # the same question, nothing said: as good as nothing changed
    RESHOW_CELLS = 40       # "show me that again": the buddy, the clock or a caret may have moved

    def _reusable_look(self, question: str, sketch, title: str = "") -> Optional[dict]:
        """The last look, when it can answer this one with no call: within
        three minutes, never after words that say the screen changed, never a
        move in a game (the other side moves too); "show me that again" on a
        nearly unchanged screen, or the same question on an unchanged one."""
        last = self._last_look
        if not last or time.monotonic() - last["at"] > self._LOOK_REUSE_S:
            return None
        words = " ".join(getattr(self, "_heard_buf", None) or []) + " " + question
        if self._FRESH_RE.search(words) or gemini_vision.needs_judgement(question, title):
            return None
        change = look_region.changed_cells(sketch, last.get("sketch"))
        if self._RESHOW_RE.search(words):
            return last if change <= self.RESHOW_CELLS else None
        if _norm_question(question) == last["question"] and change <= self.SAME_CELLS:
            return last
        return None

    async def _snap_to_controls(self, actions, monitor, window) -> int:
        """UD-03: a ring drawn round a button is put on the button itself.
        Windows knows the control's exact rectangle at the box's centre in a
        few ms; it replaces the model's box only when it is plainly the same
        thing (see look_region.snap_rect). SP-09: the button, not the label
        inside it (look_region.best_control), and a move's two ends - a file
        to a folder - each snapped the same way. Pictures and board squares
        with no control of their size are left as drawn."""
        async def snap(box):
            rect = look_region.box_to_rect(box, monitor)
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            # Past three times the box's size nothing can be snapped to.
            limit = 3 * max(1.0, (rect[2] - rect[0]) * (rect[3] - rect[1]))
            try:
                chain = await asyncio.wait_for(
                    asyncio.to_thread(look_region.control_chain_at, cx, cy, limit), 0.3)
            except Exception:  # noqa: BLE001 - a slow or missing tree: keep the model's box
                chain = []
            exact = look_region.best_control(rect, chain, window)
            return look_region.rect_to_box(exact, monitor) if exact else None

        snapped = 0
        for a in actions:
            if getattr(a, "exact", False):
                continue                     # on a board's own squares already
            exact = await snap(a.box)
            if exact:
                a.box = exact
                snapped += 1
            if getattr(a, "to_box", None):
                there = await snap(a.to_box)
                if there:
                    a.to_box = there
                    snapped += 1
        return snapped

    async def _tool_draw(self, args: dict) -> dict:
        tags = str(args.get("tags") or "")
        if not tags.strip():
            return {"error": "no tags given"}
        shapes = self._extract_shapes(tags)
        for sh in shapes:
            self.sig_draw.emit(sh)
        if not shapes:
            return {"error": "none of those tags parsed — check the syntax"}
        return {"ok": True, "drawn": len(shapes)}

    async def _tool_clear(self, args: dict) -> dict:
        self.sig_clear_drawings.emit()
        return {"ok": True}

    async def _tool_show_me_how(self, args: dict) -> dict:
        """Start a guided walkthrough — WITHOUT blocking the model.

        Planning takes seconds, and the model cannot say a word until a tool
        returns. Awaiting the planner here meant the assistant went silent for
        the whole time it took (measured at 17.6 s with the pro model) before
        it could even say "sure, I'll walk you through it". So this returns
        immediately and the planning happens behind it: the user hears the
        acknowledgement at once, and the first step arrives as soon as there
        is a plan.
        """
        goal = str(args.get("goal") or "").strip()
        if not goal:
            return {"error": "no goal given"}

        self._submit(self._plan_and_start_guide(goal, time.monotonic()))
        # Step one is said in a few seconds: have Tora's voice's output
        # running by then, not opened when she starts to speak (GW-08).
        try:
            self._get_tts().warm()
        except Exception:  # noqa: BLE001 - a head start, never a blocker
            _log.debug("could not warm the voice output", exc_info=True)
        return {
            "started": True,
            "instruction": (
                "Say ONE short sentence RIGHT NOW — just that you'll walk "
                "them through it, nothing else. Do NOT say the first step, "
                "or any step, or what they should click: Tora speaks step "
                "one out loud a second from now, and if you say it too they "
                "hear the same instruction twice. 'Sure, let me walk you "
                "through it.' and then stop talking. Do not say 'say next' "
                "either — it advances by itself. If they later give a "
                "walkthrough command (next, back, repeat, stop, pause, where, "
                "help...), call guide_step once."
            ),
        }

    async def _plan_guide(self, goal: str, shot: str, title: str,
                          controls: Optional[list[str]] = None,
                          on_first_step=None):
        """Fast plan, falling back to the careful model if it is unusable.

        With `on_first_step` the fast plan is streamed (GW-08). Once step
        one has started a walkthrough there is no falling back: a different
        plan would not follow on from the step already being done."""
        started: list = []

        def first(partial) -> None:
            started.append(partial)
            on_first_step(partial)

        guide = await gemini_planner.plan(
            goal, screenshot_b64=shot, window_title=title,
            controls=controls, fast=True,
            on_first_step=first if on_first_step is not None else None,
        )
        # No credit, a refused key or no network fail the pro model the same
        # way - after 10-20 s more. A busy fast model is worth one careful
        # try: they are served separately.
        if not guide and not started and guide.failure not in ("billing", "key", "network"):
            _log.info("fast plan unusable for %r — retrying with the pro model",
                      goal[:60])
            # UI-06: one try, bounded. On 11 Oct the pro model was busy (503)
            # and its retries ran step one to 78 s; nobody waits that long
            # for a walkthrough. Past PLAN_FALLBACK_S it is said that it
            # could not be planned.
            try:
                guide = await asyncio.wait_for(gemini_planner.plan(
                    goal, screenshot_b64=shot, window_title=title,
                    controls=controls, fast=False, busy_retries=False,
                ), self.PLAN_FALLBACK_S)
            except asyncio.TimeoutError:
                _log.warning("the pro plan took over %.0fs - given up", self.PLAN_FALLBACK_S)
                guide = gemini_planner.Guide(goal=goal, feasible=False)
        return guide

    PLAN_FALLBACK_S = 20.0

    @staticmethod
    def _foreground_controls(title: str) -> list[str]:
        """The names of the controls in the window the user is in, as the app
        itself labels them. 0.2-0.4 s (UC-04); nothing is uploaded."""
        if not title:
            return []
        try:
            from agents.control import uia
            rows = uia.controls(title, limit=160)
        except Exception:  # noqa: BLE001 - no tree, no list; the plan still works
            _log.debug("could not read the controls of %r", title, exc_info=True)
            return []
        names = [str(r.get("name") or "").strip() for r in rows
                 if r.get("enabled", True)]
        return [n for n in dict.fromkeys(names) if n and len(n) <= 80]

    async def _plan_and_start_guide(self, goal: str,
                                    asked_at: Optional[float] = None) -> None:
        """Plan in the background, and start the walkthrough the moment its
        first step exists.

        Uses the FAST planner on the critical path — measured 3.8 s against
        17.6 s for the pro model, for a plan that was actually more granular.
        The pro model is kept as the fallback for when the quick plan comes
        back unusable, rather than as the thing everyone waits on.

        GW-08: the plan is streamed, and the walkthrough starts on step one
        while the rest is still being written - step one is complete about
        1.9 s in, where the whole plan took 3.6-4.3 s. The screenshot and the
        control walk, which both have to happen first, run side by side.
        """
        asked_at = asked_at or time.monotonic()
        title = active_window_title()
        self._activity("planning")
        planning_started = time.monotonic()
        started: list = []
        controls: list[str] = []
        process = ""

        def first_step(partial) -> None:
            gemini_planner.repair_first_step(partial, controls, title, process)
            self._activity("")
            self._plan_in_progress = partial
            started.append(partial)
            _log.info("guide step one ready %.1fs after it was asked for "
                      "(the rest is still being planned)",
                      time.monotonic() - asked_at)
            self._start_guide(partial, asked_at=asked_at)
            # Only the voice has to wait for "sure, let me walk you through
            # it" to end. The arrow goes up now, and step one's speech starts
            # downloading, so it plays the moment that answer is over.
            self._submit(self._prefetch_step(0))
            if partial.steps[0].target:
                self._submit(self._point_first_step(partial, asked_at))

        guide = None
        try:
            shot, controls = await asyncio.gather(
                self._planning_shot(),
                asyncio.to_thread(self._foreground_controls, title))
            try:
                from tutor import active_process_name
                process = active_process_name() or ""
            except Exception:  # noqa: BLE001
                process = ""
            _log.info("planning %r against %d on-screen controls", goal[:60],
                      len(controls))
            self._note_uia(title, len(controls))
            guide = await self._plan_guide(goal, shot, title, controls,
                                           on_first_step=first_step)
            if guide and not started:
                gemini_planner.repair_first_step(guide, controls, title, process)
        finally:
            # Planning happens after show_me_how has already returned, so the
            # tool wrapper cannot cover it — clear it here or the island sticks.
            # (Once step one is up the walkthrough owns the island.)
            if not started:
                self._activity("")
            plan_s = time.monotonic() - planning_started
            if started:
                # Always, even on an error: a runner told the plan is still
                # being written would otherwise wait for it.
                self._plan_written(started[0], guide, plan_s, goal)
        if started:
            return
        if not guide:
            await self._reply_local(
                guide.preamble
                or "I couldn't work out the steps for that from this screen."
            )
            return

        _log.info("guide ready: %d steps for %r (planned in %.1fs)",
                  len(guide.steps), goal[:60], plan_s)
        self._start_guide(guide, asked_at=asked_at)
        if self._guide_stats is not None:
            self._guide_stats["plan_s"] = plan_s

    async def _planning_shot(self) -> str:
        """The screenshot the planner sees, or "" for a sensitive window."""
        if self._privacy_guard and is_sensitive_window(active_window_title()):
            return ""
        shot = await self._one_shot(1024)
        return shot.base64_jpeg if shot is not None else ""

    def _plan_written(self, partial, full, plan_s: float, goal: str) -> None:
        """The rest of a streamed plan has arrived: add it behind the steps
        already running.

        The steps the walkthrough started with are the SAME objects the
        runner is using - a watcher hangs off the one on screen - so only
        what is new is appended. That holds whether or not it is still
        running: a stopped walkthrough keeps its guide for "resume".
        """
        self._plan_in_progress = None
        new = list(full.steps[len(partial.steps):]) if full else []
        partial.steps.extend(new)
        if not full:
            _log.warning("the rest of the plan for %r did not arrive - the "
                         "walkthrough has only its first step", goal[:60])
        _log.info("guide ready: %d steps for %r (planned in %.1fs; it started "
                  "on step one while the rest was written)",
                  len(partial.steps), goal[:60], plan_s)
        runner = self._guide_runner
        if runner is None or self._lesson_plan is not partial:
            return
        self._lesson_steps = [s.instruction for s in partial.steps]
        if self._guide_stats is not None:
            self._guide_stats["steps"] = len(partial.steps)
            self._guide_stats["plan_s"] = plan_s
        runner.plan_written()
        idx = runner.index
        if 0 <= idx < len(partial.steps):
            self.sig_guide_progress.emit(idx + 1, len(partial.steps),
                                         partial.steps[idx].instruction)
        # The next step's speech is ready by the time it is needed.
        self._submit(self._prefetch_step(idx + 1))

    async def _tool_search(self, args: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "no query given"}
        if not self._web_search_enabled:
            return {"error": "web search is switched off in the tray menu"}
        from ai.web_search import search
        self._activity("searching")
        started = time.monotonic()
        try:
            results = await search(query)
        except Exception as e:
            return {"error": str(e)}
        _log.info("web search took %.1fs (%d chars)", time.monotonic() - started, len(results or ""))
        # Short: the voice spent 5-8 s reading a 4,000-character result
        # before its first word (19 Oct). The search's own answer comes first.
        return {"results": (results or "")[:1500] or "nothing useful found"}

    async def _tool_recall(self, args: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "no query given"}
        hits = await gemini_memory.recall(query, k=5)
        if not hits:
            return {"found": False}
        return {
            "found": True,
            "passages": [
                {"text": h.text[:1200], "from": h.source or h.kind}
                for h in hits
            ],
        }

    # ── Input sources ─────────────────────────────────────────────────────────

    def on_hotkey_press(self):
        if not self._claim_begin():
            return
        self._hotkey_pressed_at = time.monotonic()
        self._key_down = True
        self._begin_capture()
        # Arm the silence watchdog straight away. If this turns out to be a
        # hold rather than a tap, on_hotkey_release claims the end first.
        self._submit(self._auto_stop_on_silence())
        if self._state == AppState.LISTENING:
            self._start_trace()

    def on_hotkey_release(self):
        self._key_down = False
        trace = self._stop_trace()
        if self._state != AppState.LISTENING:
            self._mist("drop")
            return
        held = time.monotonic() - self._hotkey_pressed_at
        if held < cfg.tap_max_seconds:
            # A tap, not a hold: keep listening and let the user finish
            # speaking. The watchdog ends the capture on silence. A tap
            # leaves no trail (decided 10 Oct: only while it is held).
            self._mist("drop")
            self._trace = None
            self.sig_hands_free.emit(True)
            return
        shown = trace is not None and trace.moved
        self._mist("keep" if shown else "drop")
        if shown:
            _log.info("trace: %s", trace.summary())
            # Read before the question is processed - queued first, it is
            # already under way when the voice's turn is about to end.
            self._submit(self._start_trace_read(trace))
        else:
            self._trace = None
        if self._claim_end():
            self._submit(self._end_capture_and_process())

    # ── Show it by tracing (docs/TRACE_TO_FOCUS.md) ──────────────────────────
    #
    # While the shortcut is held, the mouse's path is recorded (ai/trace.py)
    # and the overlay leaves a mist behind it (ui/mist.py). TF-01: the
    # record, in physical px, each point with its time since the press.

    _trace: Optional["tracing.Trace"] = None
    _trace_live = False
    # The shortcut is physically down. While it is, the question is not over:
    # 10 Oct, the owner drew round something, went quiet to finish the circle,
    # and the live session's own end-of-speech detection (200 ms of silence)
    # answered before the trace existed - and the local watchdog then ended
    # the capture, so the trace was dropped at the release. Now neither hears
    # a pause as the end while it is held (_realtime_stream, _auto_stop_on_silence).
    _key_down = False
    HOLD_QUIET_S = 0.12        # under the server's 200 ms end-of-speech window
    TRACE_EVERY_S = 0.016

    def _mist(self, state: str) -> None:
        """Tell the overlay: "on", "keep" or "drop". Like _activity, a sign
        on the screen - never a reason for the question to fail."""
        try:
            self.sig_trace.emit(state)
        except Exception:  # noqa: BLE001
            pass

    def _start_trace(self) -> None:
        self._trace = tracing.Trace()
        self._trace_ahead = {}
        self._trace_ahead_task = None
        self._trace_live = True
        self._mist("on")
        self._submit(self._record_trace(self._trace, self._hotkey_pressed_at))

    def _stop_trace(self) -> Optional["tracing.Trace"]:
        self._trace_live = False
        return self._trace

    # TF-04/05: what was traced is read (ai/trace_read.py) the moment the
    # shortcut comes up, and the voice is told before its turn ends.

    _trace_task: Optional[asyncio.Task] = None
    _trace_readings: list = []
    TRACE_WAIT_S = 0.25        # the answer waits no longer than this for it
    # WR-02: each stroke is read as soon as it ends - the mouse rests
    # tracing.PAUSE_S - while the shortcut is still held. A circle took
    # 0.2-0.4 s to read (once 1.3 s) against the answer's 0.25 s wait, and a
    # late reading lost its words; read while they finish the question, it
    # is there at the release.
    _trace_ahead: Optional[dict] = None           # focus key -> Reading, for this trace
    _trace_ahead_task: Optional[asyncio.Task] = None

    @staticmethod
    def _focus_key(f) -> tuple:
        return f.kind, tuple(f.rect), f.t0, f.t1

    def _private_now(self) -> bool:
        return bool(self._privacy_guard and is_sensitive_window(active_window_title()))

    def _read_ahead(self, trace) -> None:
        """Read the strokes finished so far, one reading at a time."""
        if self._trace_ahead is None:
            self._trace_ahead = {}
        before, ahead = self._trace_ahead_task, self._trace_ahead

        async def read() -> None:
            if before is not None:
                await asyncio.gather(before, return_exceptions=True)
            todo = [f for f in tracing.focuses(trace) if self._focus_key(f) not in ahead]
            if not todo or self._trace is not trace or self._private_now():
                return
            for f, reading in zip(todo, await asyncio.to_thread(trace_read.read, todo)):
                ahead[self._focus_key(f)] = reading
        self._trace_ahead_task = asyncio.ensure_future(read())

    def _shapes_with_what_was_read(self, focuses) -> list:
        """Each focus's reading if it was read while held, else its shape and place."""
        ahead = self._trace_ahead or {}
        return [ahead.get(self._focus_key(f)) or trace_read.Reading(f) for f in focuses]

    async def _start_trace_read(self, trace) -> None:
        self._trace_task = asyncio.ensure_future(self._read_trace(trace))

    async def _read_trace(self, trace) -> str:
        """The voice's line about what was traced - read, not photographed."""
        focuses = tracing.focuses(trace)
        if not focuses:
            return ""
        started = time.monotonic()
        if self._private_now():
            readings = [trace_read.Reading(f) for f in focuses]          # a place and a shape, nothing read
        else:
            if self._trace_ahead_task is not None:                      # a stroke being read: it is nearly done
                await asyncio.gather(self._trace_ahead_task, return_exceptions=True)
            ahead = self._trace_ahead or {}
            todo = [f for f in focuses if self._focus_key(f) not in ahead]
            fresh = iter(await asyncio.to_thread(trace_read.read, todo) if todo else ())
            readings = [ahead[self._focus_key(f)] if self._focus_key(f) in ahead else next(fresh)
                        for f in focuses]
            if len(todo) < len(focuses):
                _log.info("trace: %d of %d read while the shortcut was held", len(focuses) - len(todo), len(focuses))
        self._trace_readings = readings
        _log.info("trace: %s - read %d of them in %.0f ms", ", ".join(f.kind for f in focuses),
                  sum(1 for r in readings if r.text or r.things), (time.monotonic() - started) * 1000)
        return trace_read.voice_line(readings)

    async def _take_trace_line(self) -> str:
        """This question's line about what they traced, or "" - waited for
        at most TRACE_WAIT_S; late, it is the shapes and places alone."""
        task, self._trace_task = self._trace_task, None
        if task is None:
            return ""
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.TRACE_WAIT_S)
        except Exception:  # noqa: BLE001 - late or failed: what was read while held, the rest its shape and place
            trace = self._trace
            if not trace or self._private_now():
                return trace_read.voice_line([trace_read.Reading(f) for f in tracing.focuses(trace)]) if trace else ""
            return trace_read.voice_line(self._shapes_with_what_was_read(tracing.focuses(trace)))

    async def _send_trace_line(self, conv) -> None:
        """Before the voice's turn ends: what they traced, in one line."""
        started = time.monotonic()
        line = await self._take_trace_line()
        if line:
            await conv.send_context(line)
            _log.info("trace line sent %.2fs after the question", time.monotonic() - started)

    async def _record_trace(self, trace, since: float) -> None:
        """Sample the mouse ~60 times a second until the shortcut comes up -
        and once it has rested a stroke's pause, read what it showed."""
        read_at = 0                                   # the trace's points when it was last read
        while self._trace_live and self._trace is trace:
            pos = look_region.cursor_position()
            if pos is not None:
                trace.add(time.monotonic() - since, *pos)
            n = len(trace.points)
            if n > read_at and time.monotonic() - since - trace.points[-1].t >= tracing.PAUSE_S and trace.moved:
                read_at = n
                self._read_ahead(trace)
            await asyncio.sleep(self.TRACE_EVERY_S)

    def _handle_wake(self):
        """Triggered from ambient listener when wake-word is detected."""
        if not self._claim_begin():
            return
        self._begin_capture()
        self.sig_hands_free.emit(True)
        self._submit(self._auto_stop_on_silence())

    # ── The flow bar's buttons (ui/flow_bar.py) ──────────────────────────────
    # Any thread may call these; they go the same ways the hotkey does.

    def start_hands_free(self) -> None:
        """A click on the resting flow bar: listen, as a tap of the hotkey
        does - the user's silence (or ✓) ends it."""
        if not self._claim_begin():
            return
        self._begin_capture()
        self.sig_hands_free.emit(True)
        self._submit(self._auto_stop_on_silence())

    def finish_listening(self) -> None:
        """✓: that's the question - as if the hotkey came up."""
        if self._state == AppState.LISTENING and self._claim_end():
            self._submit(self._end_capture_and_process())

    def cancel_listening(self) -> None:
        """✕: never mind. Nothing is asked, and nothing of it is kept."""
        if self._state != AppState.LISTENING or not self._claim_end():
            return
        self._key_down = False
        self._stop_trace()
        self._trace = None
        self._mist("drop")
        streamed, self._rt_streaming = self._rt_streaming, False
        try:
            self._listener.stop_recording()
        except Exception:  # noqa: BLE001 - the mic may already be closed
            _log.debug("mic stop on cancel failed", exc_info=True)
        self._heard_buf = []
        self._emit_state(AppState.IDLE)
        _log.info("listening cancelled")
        if streamed and self._realtime is not None:
            # The live session has heard the start of it, and would join it
            # onto the next question: a fresh session instead.
            self._submit(self._restart_realtime())

    async def _restart_realtime(self) -> None:
        conv = self._realtime
        if conv is not None:
            try:
                await conv.close()
            except Exception:  # noqa: BLE001
                _log.debug("closing the live session failed", exc_info=True)
        await self._realtime_connect()      # a plan that opens it on the question: not now

    def _claim_begin(self) -> bool:
        """Exactly one of {hotkey press, wake word} may open a capture.

        Sets LISTENING inside the lock so a second caller on another thread
        cannot also observe IDLE and start a parallel capture.

        A press while she is thinking or answering used to do nothing until
        she had finished. Now she stops and listens (the owner's choice,
        18 Oct 2026): see _interrupt_answer.
        """
        with self._begin_lock:
            if self._state == AppState.LISTENING:
                return False
            busy = self._state != AppState.IDLE
            self._state = AppState.LISTENING
            # Before anything else: from here the last answer's code can no
            # longer set the state (_emit_state, _ANSWERING).
            self._question_no += 1
        if busy:
            self._interrupt_answer()
        return True

    # The question being answered on the slow route (_cascade_turn), so a
    # new question can stop it; None on the live route, where the session
    # stops its own answer (RealtimeConversation.interrupt).
    _answer_task: Optional[asyncio.Task] = None
    # Counts questions. An answer still finishing for an older one must not
    # set the state of the new one (_cascade_turn).
    _question_no = 0

    def _interrupt_answer(self) -> None:
        """Stop everything the last question set going - her voice, a look,
        typing, a tool, the slow route - because a new one has begun. Runs
        on the keyboard hook's thread: it flips flags and hands the rest to
        the loop. A walkthrough, an agent job and a PDF being made carry on:
        they are not an answer, and have their own stop."""
        _log.info("a new question while she was answering - she stops and listens")
        conv = self._realtime
        if conv is not None:
            conv.interrupt()
        task, loop = self._answer_task, self._loop
        if task is not None and loop is not None and loop.is_running():
            loop.call_soon_threadsafe(task.cancel)
        if self._pointing_held:
            self._pointing_held = False
            self.sig_point_release.emit()

    def _claim_end(self) -> bool:
        """Exactly one of {hotkey release, silence watchdog} ends a capture."""
        with self._end_lock:
            if self._end_claimed:
                return False
            self._end_claimed = True
            return True

    def _handle_level(self, rms: float):
        self._last_rms = rms
        try:
            self.sig_audio_level.emit(rms)
        except Exception:
            pass   # never crash the sounddevice audio thread

    # ── Capture flow ──────────────────────────────────────────────────────────

    def _begin_capture(self):
        self._end_claimed = False
        self._last_rms = 0.0
        # Silence anything still playing from the previous turn. Without this
        # a leftover TTS stream keeps going while the next answer starts, and
        # the two are heard on top of one another.
        try:
            from audio.playback import stop_audio
            stop_audio()
        except Exception:
            pass
        if self._idle_s() > self.IDLE_FRESH_S:
            self._forget_conversation()
        self._last_talk_at = time.monotonic()
        if self._realtime is not None:
            self._realtime.cancel()
        self._rt_audio.clear()
        self._heard_buf = []
        self._pointed_this_turn = False
        # A new thing the user is saying to Tora. Anything budgeted "per
        # request" - a guide move, for one - is measured against this.
        self._request_serial += 1
        self._trace_readings = []           # a trace belongs to the question it was drawn for
        self._rt_question_pcm = None
        speed.begin()
        # Shown before the microphone opens, not after: the press is answered
        # on screen (and by her sound) at once.
        self._emit_state(AppState.LISTENING)
        opened = time.monotonic()
        try:
            # The sink has to be attached BEFORE the first block is captured,
            # or the opening syllable of the question is only in the buffer
            # and never reaches the live session.
            self._listener.set_chunk_sink(self._feed_realtime)
            self._listener.start_recording()
        except Exception as e:
            _log.exception("mic start failed")
            self.sig_error.emit(
                f"Couldn't open the microphone: {e}\n"
                "Check Tray → Setup & Diagnostics → Microphone."
            )
            self._emit_state(AppState.IDLE)
            return
        speed.mark("mic")
        self._submit(gemini.keep_warm())        # SC-09: the look's connection, while they talk
        took = time.monotonic() - opened
        if took > 0.15:
            _log.info("the microphone took %.0f ms to open", took * 1000)

        # The session is already open, so there is nothing to wait for —
        # audio starts flowing to the model on the next 30 ms block.
        conv = self._realtime
        if conv is not None and conv.ready and getattr(conv, "aging", False):
            # FS-12: about to be closed by the server - this question gets a
            # fresh session, which picks the conversation up where it was.
            _log.info("live session %.1f min old - a fresh one for this question", conv.age_s / 60)
            conv.retire()
        ready = bool(conv is not None and conv.ready)
        # Hotkey pressed while the socket is still opening (the first
        # seconds after launch): the mic is already buffering, so give the
        # handshake a moment in _realtime_stream rather than sending this
        # question down the 25-second cascade.
        connecting = bool(conv is not None and not ready and conv.connecting)
        # No session: it is opened now, on the question - the mic is already
        # buffering, and _realtime_stream waits for it. A metered plan always
        # works this way; since FS-02 every plan does after a silence, rather
        # than sending the question down the much slower cascade.
        on_demand = (not ready and not connecting and cfg.realtime() and cfg.has_key())
        if self._is_new_question():
            # Every time the user talks is a talk message, on every plan.
            # Said before the pass is asked for (Free opens the voice now),
            # or it would ride on the last question's window; counted when
            # Tora hears them (_count_question) or at the first call made for
            # the answer, whichever comes first.
            self._new_talk()
        if on_demand:
            self._get_realtime()
            self._submit(self._realtime_connect(on_demand=True))
        self._rt_on_demand = on_demand
        self._rt_streaming = ready or connecting or on_demand
        _log.info("turn: %s", "realtime" if ready
                  else "realtime (waiting for session)" if connecting
                  else "realtime (opening the session)" if on_demand
                  else "cascade (no live session)")
        if self._rt_streaming:
            self._submit(self._realtime_stream())
        else:
            # No live session: the cascade handles this one, and a reconnect
            # is started so the next question is fast again.
            speed.route("slow")
            self._submit(self._realtime_connect())

    # End-of-speech tuning. SPEECH_RMS matches the ambient listener's VAD
    # threshold so both agree on what counts as speech.
    #
    # END_SILENCE_S only closes the microphone now. On the realtime route the
    # server decides when the question finished — it has the language context
    # to tell a mid-sentence pause from an ending, and it has already started
    # answering by the time this fires. It was 1.1 s, which was a flat second
    # of silence added to every single question while a local timer waited to
    # be sure.
    _SPEECH_RMS        = 0.006
    _END_SILENCE_S     = 0.45   # quiet for this long after speaking = done
    _NO_SPEECH_S       = 6.0    # nothing said at all = give up
    _MAX_UTTERANCE_S   = 45.0   # hard cap so a stuck mic can't record forever

    async def _auto_stop_on_silence(self):
        """End the capture once the user actually stops talking.

        The previous version just waited a flat 10 seconds, which cut long
        questions off mid-sentence and made short ones sit there in silence.
        This watches the live mic level instead: wait for speech to start,
        then end on a sustained pause.
        """
        start_t = time.monotonic()
        speech_started = False
        silence_from: Optional[float] = None

        while self._state == AppState.LISTENING:
            await asyncio.sleep(0.05)
            now = time.monotonic()

            if self._last_rms > self._SPEECH_RMS:
                speech_started = True
                silence_from = None
            elif self._key_down:
                silence_from = None             # held: the release ends it, not a pause
            elif speech_started:
                if silence_from is None:
                    silence_from = now
                elif now - silence_from >= self._END_SILENCE_S:
                    break

            if not speech_started and not self._key_down and now - start_t > self._NO_SPEECH_S:
                break
            if now - start_t > self._MAX_UTTERANCE_S:
                break

        if self._claim_end():
            await self._end_capture_and_process()

    async def _end_capture_and_process(self):
        """The hotkey came up. Answer through whichever route is live."""
        speed.mark("release")
        was_realtime, self._rt_streaming = self._rt_streaming, False
        conv = self._realtime
        # Still waiting for its session to open (_await_session): it stays on
        # the live route, and is sent the moment the session is there.
        awaiting = bool(self._rt_awaiting) and self._rt_awaiting == self._question_no
        if was_realtime and not awaiting and (conv is None or not conv.ready):
            was_realtime = False    # never opened; the cascade has the full pcm
        try:
            pcm = self._listener.stop_recording()
        except Exception as e:
            _log.exception("mic stop failed")
            self.sig_error.emit(f"Microphone capture failed: {e}")
            self._emit_state(AppState.IDLE)
            return
        _log.info("captured %.1fs of audio", len(pcm) / 32000)

        if was_realtime:
            if len(pcm) < 3200:            # nothing was said
                self._rt_said_nothing = self._question_no
                speed.finish("nothing said")
                self._emit_state(AppState.IDLE)
                return
            if awaiting:
                self._rt_question_pcm = pcm      # the slow way, if the session never opens
            # Nothing further to do. The server heard the question as it was
            # spoken, decided for itself that it finished, and the answer is
            # already on its way in through the pump — which will move the
            # state on to SPEAKING and then back to IDLE.
            if self._state == AppState.LISTENING:
                self._emit_state(AppState.THINKING)
            self._activity("understanding")
            self._turn_seq += 1
            self._submit(self._realtime_turn_watchdog(self._turn_seq, pcm))
            return

        if len(pcm) < 3200:
            speed.finish("nothing said")
            self._emit_state(AppState.IDLE)
            return
        speed.route("slow")
        if not gemini.own_key() and self._problem_kind in ("plan", "signin", "service"):
            # The voice pass was refused: this month's talk is used up, or
            # the sign-in has lapsed. The slower route would only spend the
            # dictation allowance on the same question - say why instead.
            words = self._PROBLEM_WORDS.get(self._problem_kind) or ""
            self.sig_response_chunk.emit(self._problem_detail or words)
            self.sig_response_done.emit(self._problem_detail or words)
            self._emit_state(AppState.IDLE)
            return
        await self._cascade_turn(pcm)

    # A turn that gets no answer at all must not strand the UI on "Thinking…"
    # (_NOTHING_HEARD_S, _HEARD_NO_ANSWER_S: FS-13) - unless a tool is
    # running for the model. Then the model is not
    # silent, it is waiting on us, and killing the turn (and the session
    # with it) throws away work that was about to land. The log showed a
    # 40 s deep look being cut off at 22 s, the session dropped, and the
    # reconnect tripping a GeneratorExit — three failures from one slow tool.
    _TURN_WATCHDOG_MAX_S = 75.0

    async def _realtime_turn_watchdog(self, seq: int, pcm: bytes = b"") -> None:
        """Guarantee a turn is answered, even if the live model never replies.

        The live session can look perfectly healthy and still produce nothing
        for a turn — no audio, no transcript, no turn_complete. The pump only
        reacts to events that arrive, and the socket never drops, so neither
        _on_realtime_turn_end nor _on_realtime_lost fires and the app sits on
        "Thinking…" forever. This is the backstop: if the same turn is still
        pending when it expires, the question - its audio, `pcm` - is asked
        the slow way instead (FX-01, 18 Oct 2026). It used to say "Sorry - I
        didn't catch that" (18 times in a fortnight), and on 16 Oct a session
        that had gone deaf heard nothing of five questions in a row, 6 s of
        speech among them: it is dropped now, and the next question has a
        fresh one.
        """
        started = time.monotonic()
        # Poll on a short interval rather than one long sleep, so a normal
        # answer (state leaves THINKING) is noticed within ~0.3 s.
        #
        # FS-13: how long to wait depends on what has happened. 5 Oct, 11:07:
        # a 1.1 s question got nothing back and Tora sat on "Thinking..." for
        # 22 s - then seven presses in ten seconds, none of them answered.
        #   no words heard at all       NOTHING_HEARD_S: the server never
        #                               registered the speech
        #   heard, no answer, no tool   HEARD_NO_ANSWER_S
        #   a tool running or just done up to MAX_S, as before
        while True:
            await asyncio.sleep(0.3)
            if seq != self._turn_seq:
                return                  # a newer turn superseded this one
            if self._state != AppState.THINKING:
                return                  # it answered, or moved on
            now = time.monotonic()
            if self._rt_awaiting:
                started = now           # not sent yet (_await_session): nothing to hear
                continue
            elapsed = now - started
            busy = self._tools_in_flight > 0 or now - self._tool_done_at < 6.0
            heard = bool(getattr(self, "_heard_buf", None)) or self._tool_done_at >= started
            if elapsed >= self._TURN_WATCHDOG_MAX_S:
                break                   # hard ceiling, tool or no tool
            # On a weak connection the first word is slower to come back.
            nothing_s = self._NOTHING_HEARD_S * (2 if net_quality.slow() else 1)
            if not busy and elapsed >= (self._HEARD_NO_ANSWER_S if heard else nothing_s):
                break                   # quiet long enough — really stuck
        rescue = len(pcm) >= 3200 and not busy
        _log.warning("realtime turn produced no response in %.1fs (%s)%s", time.monotonic() - started,
                     "words were heard" if heard else "no words were heard",
                     " - asking the slow way" if rescue else "")
        self._activity("")
        if rescue:
            self._drop_realtime()       # nothing more from it: it cannot answer twice
            speed.route("slow")
            await self._cascade_turn(pcm)
            return
        speed.finish("unanswered")
        self._emit_state(AppState.IDLE)
        if heard:
            # The model heard them and said nothing: the session is suspect —
            # retire it so the next question gets a fresh one rather than
            # hitting the same silence again. Nothing heard is not the
            # session's fault: it stays.
            self._drop_realtime()
        await self._reply_local(
            "Sorry — I didn't catch that. Could you say it again?"
        )

    _NOTHING_HEARD_S = 2.5          # FX-01: the slow way after this (5 s before, FS-13)
    _HEARD_NO_ANSWER_S = 12.0       # FS-13 (one 22 s wait for everything, before)

    def _record_turn(self, question: str, answer: str) -> None:
        """Everything that happens after an answer, whichever route made it."""
        if not answer:
            return
        title = active_window_title()
        ak = app_key(title)

        history = self._app_memory.setdefault(ak, [])
        if question:
            history.append(Message(role="user", content=question))
        history.append(Message(role="assistant", content=answer))
        self._app_memory[ak] = history[-20:]

        if self._journal_enabled and not self._quiz_mode and question:
            def write() -> None:
                try:
                    journal.log_qa(
                        question=question, answer=answer,
                        app_key=ak, window_title=title,
                        provider="gemini", model=Models.REALTIME,
                    )
                except Exception:
                    pass
            # A disk write (SQLite, its own connection) - never on the loop
            # that carries the voice (HP-12).
            threading.Thread(target=write, name="journal", daemon=True).start()

        if self._recorder and self._recorder.is_recording:
            if question:
                self._recorder.log_question(question)
            self._recorder.log_answer(answer)

        if self._collab and self._collab.code:
            self._submit(self._collab.send(
                {"type": "qa", "q": question, "a": answer}
            ))

    async def _cascade_turn(self, pcm: bytes):
        """Transcribe, think, speak — the fallback route."""
        # 32000 bytes == 1s of 16 kHz mono int16.
        audio_seconds = len(pcm) / 32000.0
        # A new question cancels this one (_interrupt_answer); its last
        # moments must then leave the new one's state alone (_emit_state).
        _ANSWERING.set(self._question_no)
        self._answer_task = asyncio.current_task()

        self._emit_state(AppState.THINKING)
        pointing_held = False  # track whether we told overlay to hold dwell

        try:
            # 1. Transcribe — bounded so a hung/downloading STT model can
            # never freeze the UI on "Thinking..." forever
            transcript = await asyncio.wait_for(
                self._get_stt().transcribe(pcm), timeout=90,
            )
            _log.info("transcript: %r", transcript[:120])
            if not transcript.strip():
                self._emit_state(AppState.IDLE)
                return

            # ── Voice commands — short-circuit before LLM ──
            if is_stop(transcript):
                self.stop()
                return

            title = active_window_title()
            ak = app_key(title)

            # Voice commands are one or two words, so they cannot come from a
            # long recording. Whisper degrades to a short generic phrase
            # ("Continue.", "Repeat.") on unclear or code-mixed speech, and
            # without this length check a mis-transcribed sentence silently
            # replayed the previous answer instead of being asked.
            command_plausible = audio_seconds <= self._MAX_COMMAND_SECONDS
            if not command_plausible and (is_next(transcript) or is_repeat(transcript)):
                _log.info(
                    "ignoring %r as a voice command — %.1fs of audio is too "
                    "long for one; treating it as a question",
                    transcript[:40], audio_seconds,
                )

            running_guide = (
                self._guide_runner is not None and self._guide_runner.running
            )
            if (command_plausible and is_resume(transcript)
                    and self._resume and not running_guide):
                if self.resume_guide():
                    self._emit_state(AppState.IDLE)
                    return
            if command_plausible and is_next(transcript):
                if running_guide:
                    self._guide_runner.request("next")
                    self._emit_state(AppState.IDLE)
                    return
                if self._lesson_steps:
                    await self._advance_lesson_step(ak)
                    return

            # "say it again" — replay the last response without a new LLM call
            if command_plausible and is_repeat(transcript) and self._last_response:
                self.sig_response_chunk.emit(self._last_response)
                self.sig_response_done.emit(self._last_response)
                self._emit_state(AppState.SPEAKING)
                try:
                    await self._get_tts().speak(self._last_response)
                except Exception:
                    pass
                self._emit_state(AppState.IDLE)
                return

            # Journal voice queries — answered locally, no LLM call needed
            if is_journal_today(transcript):
                msg = journal.summarise(journal.entries_today(),
                                        "Here's what you asked about today:\n")
                await self._reply_local(msg)
                return
            if is_journal_week(transcript):
                msg = journal.summarise(journal.entries_this_week(),
                                        "Here's the past week:\n")
                await self._reply_local(msg)
                return
            if is_quiz_review(transcript):
                await self._spaced_review()
                return

            # User-created skills (run BEFORE the LLM, like built-ins above)
            try:
                skill = skills_pkg.match(transcript)
                if skill:
                    msg = await skill["handler"](self, transcript)
                    if msg:
                        await self._reply_local(msg)
                    return
            except Exception as e:
                self.sig_error.emit(f"Skill error: {e}")

            # Autonomous tasks — "create a folder called X and build…" is
            # work for the agent workforce, not an answer for the model.
            # Checked after the voice commands and skills (they are cheaper
            # and more specific) but before we pay for a screenshot.
            task = await self._delegate_if_task(transcript)
            if task is not None:
                from agents.bootstrap import describe_plan
                await self._reply_local(describe_plan(task))
                return

            # 2. Screen capture — skipped if sensitive window (password manager etc.)
            #
            # ALSO skipped for "who is X" / "tell me about X" identity questions:
            # OpenAI + Claude refuse to identify people in screenshots even when
            # the answer is in their training data ("Sorry I can't identify the
            # person in images"). Stripping the screenshot lets the LLM answer
            # from training data + web search instead, which is what the user
            # actually wants when they ask "who is MrBeast" while on YouTube.
            sensitive = self._privacy_guard and is_sensitive_window(title)
            identity_q = is_identity_question(transcript)
            if sensitive or identity_q:
                screenshots = []
                images_b64 = []
                self._screens_ctx = []      # nothing to map coordinates against
            else:
                # mss + JPEG encoding is 50-150 ms of pure CPU; on the event
                # loop that is 50-150 ms during which nothing else starts.
                # reuse=False: a fresh question deserves a fresh grab, and this
                # also stamps the cache so any drawing this turn reuses it.
                screenshots = await self._capture_shots(
                    max_width=960 if net_quality.slow() else 1280, reuse=False)
                # Only the ACTIVE monitor goes to the model — sending all three
                # and hardcoding "screen1" in the prompt made it point on the
                # wrong screen. One image, one coordinate space.
                _active = self._active_shot(screenshots)
                images_b64 = [_active.base64_jpeg] if _active else []
            # Fresh question → wipe the previous lesson's drawings.
            self.sig_clear_drawings.emit()

            # Local figure detection (OpenCV) — finds triangles/rects/circles
            # with EXACT normalized vertices, so the model draws on them by
            # echoing numbers rather than estimating them from the image.
            self._figures_ctx = []
            fig_extra = ""
            if screenshots:
                try:
                    from ai.figure_detector import detect_figures, figures_prompt
                    self._figures_ctx = await asyncio.to_thread(
                        detect_figures, (self._active_shot(screenshots) or screenshots[0]).base64_jpeg,
                    )
                    fig_extra = figures_prompt(self._figures_ctx)
                except Exception:
                    self._figures_ctx = []

            # 3. Parallel side-work: web search + pointing
            #
            # Both are started now and awaited later, so they overlap with
            # each other instead of running back to back.
            locate_triggered = is_locate(transcript)
            multistep = is_multistep(transcript)

            search_task = None
            locate_task = None
            # Was unconditional — every cascade turn scraped the web, including
            # 'Hello.' Now only an explicit ask goes online.
            from ai.web_search import search, wants_search
            if self._web_search_enabled and wants_search(transcript):
                _log.info("web search: %r", transcript[:60])
                search_task = asyncio.create_task(search(transcript))

            if screenshots and locate_triggered:
                locate_task = asyncio.create_task(
                    self._locate_target(transcript, self._active_shot(screenshots) or screenshots[0])
                )

            search_results = ""
            if search_task:
                try:
                    search_results = await search_task or ""
                except Exception:
                    search_results = ""

            detected = None
            detected_coord = None
            if locate_task:
                try:
                    detected = await locate_task
                except Exception:
                    detected = None
            if detected:
                # Short label guess — first noun phrase after "the"/"where"
                label = _guess_label(transcript)
                # Prompt wants NORMALIZED 0-1000 coords (the model echoes them
                # into [POINT:...] which _parse_points denormalizes back).
                ndx, ndy = self._norm(detected.x, detected.y)
                detected_coord = (ndx, ndy, label)
                # Fire the overlay NOW so the buddy flies over while the LLM
                # still thinks. Hold dwell until TTS completes.
                self.sig_point_hold.emit(True)
                pointing_held = True
                self.sig_point_at.emit(
                    float(detected.x), float(detected.y), label,
                )
                self._remember_ref(detected.x, detected.y, label)

            # ── Per-turn enrichment: code mode, language, OCR, attached docs ──
            code_active = self._code_mode_auto and code_mode.is_code_window(title)
            lang_code = "en"          # Tora is English-only

            # OCR fallback for fine print (only if user actually asks to read)
            ocr_extra = ""
            if self._ocr_enabled and screenshots and ocr.needs_ocr(transcript):
                try:
                    import base64
                    jpeg = base64.b64decode((self._active_shot(screenshots) or screenshots[0]).base64_jpeg)
                    txt = ocr.run_ocr(jpeg)
                    if txt:
                        ocr_extra = ocr.format_for_prompt(txt)
                except Exception:
                    pass

            # Attached documents + remembered facts.
            #
            # Every attached document used to be pasted into the prompt in
            # full, on every question, for as long as it stayed attached —
            # up to 60,000 characters of PDF in front of "what does this
            # button do". Now the document is embedded once when it is
            # attached and only the passages related to THIS question come
            # back, which is both a better answer and a far smaller prompt.
            doc_extra = ""
            try:
                hits = await gemini_memory.recall(transcript, k=5)
                doc_extra = gemini_memory.format_for_prompt(hits)
            except Exception:
                _log.debug("recall failed", exc_info=True)

            # What they traced while asking (TF-05), as the live voice is told.
            trace_line = await self._take_trace_line()
            trace_extra = f"\n\n{trace_line}\n" if trace_line else ""

            # 4. Build system prompt with all context
            system = _build_system_prompt(
                window_title=title,
                lesson_step=self._lesson_step_idx,
                total_steps=len(self._lesson_steps),
                quiz_mode=self._quiz_mode,
                detected_coord=detected_coord,
                code_active=code_active,
                language_code=lang_code,
                extra=ocr_extra + doc_extra + fig_extra + trace_extra,
            )
            if sensitive:
                system += (
                    "\n\nPRIVACY GUARD: the user's active window looks sensitive "
                    "(password manager, banking, login). I did NOT take a "
                    "screenshot. Answer from memory only, and tell the user you "
                    "skipped the screenshot for safety.\n"
                )
            if search_results:
                from ai.web_search import build_search_context
                system += build_search_context(search_results)

            # Use per-app history so context doesn't bleed between apps
            history = self._app_memory.setdefault(ak, [])

            # 5. Stream LLM — buffer partial [POINT:...] tags so they never leak
            full_response = ""
            display_buf = ""
            self._cancel_flag = False
            async for chunk in self._get_llm().stream_response(
                user_text=transcript,
                screenshots_b64=images_b64,
                history=history,
                system_prompt=system,
                model=self._current_model,
            ):
                if self._cancel_flag:
                    break
                full_response += chunk
                display_buf += chunk
                self._parse_points(display_buf)
                display_buf = ANY_TAG_RE.sub("", display_buf)
                m = ANY_PARTIAL_RE.search(display_buf)
                if m:
                    flush = display_buf[: m.start()]
                    display_buf = display_buf[m.start():]
                else:
                    flush = display_buf
                    display_buf = ""
                if flush:
                    self.sig_response_chunk.emit(flush)
            if display_buf:
                self.sig_response_chunk.emit(ANY_TAG_RE.sub("", display_buf))

            # 6. Update per-app history. The raw response goes in, tags and
            # all, so a follow-up question sees what was actually drawn.
            history.append(Message(role="user", content=transcript))
            history.append(Message(role="assistant", content=full_response))
            self._app_memory[ak] = history[-20:]

            # Multistep: build a real plan for later "next" invocations.
            # Scraping a numbered list out of the prose only worked when the
            # model happened to write one, which the style rules explicitly
            # tell it not to do. The planner is asked directly instead, in
            # the background — the answer to THIS question is already on its
            # way to the speaker and must not wait for it.
            if multistep and not self._lesson_steps:
                self._submit(self._plan_in_background(transcript, images_b64))

            clean = ANY_TAG_RE.sub("", full_response).strip()
            self.sig_response_done.emit(clean)
            self._last_response = clean   # for "say it again"

            # Log to knowledge journal (skipped in quiz mode — those Q&As aren't
            # study material)
            if self._journal_enabled and not self._quiz_mode:
                try:
                    journal.log_qa(
                        question=transcript, answer=clean,
                        app_key=ak, window_title=title,
                        provider="gemini",
                        model=self._current_model or Models.VISION_FAST,
                    )
                except Exception:
                    pass

            # Lesson recorder gets the Q&A in transcript.md
            if self._recorder and self._recorder.is_recording:
                self._recorder.log_question(transcript)
                self._recorder.log_answer(clean)

            # Live-collab broadcast
            if self._collab and self._collab.code:
                try:
                    await self._collab.send({
                        "type": "qa", "q": transcript, "a": clean,
                    })
                except Exception:
                    pass

            # 7. Speak, holding the point visible while we do.
            #
            # No voice switching any more. Every Gemini voice speaks every
            # language, so a German reply is a German-sounding German reply
            # in the same voice — the accent used to change mid-conversation
            # whenever language detection wobbled on code-mixed speech.
            if self._cancel_flag:
                return
            self._emit_state(AppState.SPEAKING)
            speed.mark("audio")
            try:
                await self._play_lesson(full_response, clean)
            except asyncio.CancelledError:
                pass

        except Exception as e:
            # Log the traceback, not just the message. The toast shows one
            # line, so discarding the stack here left bug reporters with
            # nothing to go on — issue #15 was reported as a bare
            # "'NoneType' object has no attribute 'write'" with no way to
            # tell which call produced it.
            _log.exception("request failed: %s", e)
            if not self._show_failure(e):
                self.sig_error.emit(str(e))

        finally:
            if pointing_held:
                self.sig_point_release.emit()
            if self._answer_task is asyncio.current_task():
                self._answer_task = None
            if _ANSWERING.get() == self._question_no:
                speed.finish("answered")          # a cut-off one is closed by the next question
            self._emit_state(AppState.IDLE)       # not if a new question has begun (_ANSWERING)

    async def _reply_local(self, msg: str, *, shown: str = ""):
        """Show + speak a message that doesn't need an LLM round-trip.

        `shown`, when given, is displayed instead of `msg` - for when the
        screen should say more than the voice (a walkthrough's summary)."""
        self.sig_response_chunk.emit(shown or msg)
        self.sig_response_done.emit(shown or msg)
        self._last_response = msg
        self._emit_state(AppState.SPEAKING)
        try:
            await self._get_tts().speak(msg)
        except Exception:
            pass
        self._emit_state(AppState.IDLE)

    async def _spaced_review(self):
        """SR-style review: pick due entries from the journal, ask one back."""
        due = journal.due_for_review(limit=1)
        if not due:
            await self._reply_local(
                "Nothing due for review right now — keep learning, I'll quiz "
                "you in a few days."
            )
            return
        entry = due[0]
        msg = f"Review: {entry['question']}"
        # Mark "correct" optimistically — a real implementation would wait for
        # the user's answer and grade it. Stubbed: reschedule based on streak.
        try:
            journal.mark_reviewed(int(entry["id"]), correct=True)
        except Exception:
            pass
        await self._reply_local(msg)

    async def _plan_in_background(self, goal: str, images_b64: list) -> None:
        """Work out the steps for a how-to while the first answer is spoken.

        The user hears the reply to what they asked immediately; by the time
        they say "next", the plan is already sitting in memory and the step
        comes back with no model call at all.
        """
        try:
            guide = await gemini_planner.plan(
                goal,
                screenshot_b64=images_b64[0] if images_b64 else "",
                window_title=active_window_title(),
            )
        except Exception:
            _log.debug("background planning failed", exc_info=True)
            return
        if guide and not self._lesson_steps:
            self._lesson_plan = guide
            self._lesson_steps = [st.instruction for st in guide.steps]
            self._lesson_step_idx = 0
            self._lesson_nagged = False
            _log.info("planned %d steps for %r", len(guide.steps), goal[:60])

    # ── Autonomous guide ──────────────────────────────────────────────────────

    def _start_guide(self, guide, *, start_index: int = 0,
                     asked_at: Optional[float] = None) -> None:
        """Begin walking the user through a planned guide, hands-free."""
        from tutor_features.guide import GuideRunner
        self._stop_guide()
        self._resume = None
        self._guide_last = None
        self._lesson_plan = guide
        self._lesson_steps = [st.instruction for st in guide.steps]
        self._lesson_step_idx = start_index
        self._lesson_nagged = False
        # Started from a plan still being written (GW-08): the runner waits
        # for more steps rather than finishing when it runs out.
        writing = guide is self._plan_in_progress

        runner = GuideRunner(
            guide,
            # LG-01: on screen at once, said when quiet. The step watcher
            # arms itself, so a click made before the line was said counts.
            show_step=self._display_step,
            speak_step=self._speak_step_line,
            self_arming=lambda step: getattr(step, "_watcher", None) is not None,
            check=self._step_done,
            say=self._reply_local,
            is_busy=self._guide_busy,
            on_stop=lambda: self._guide_stopped(runner),
            # Look-ahead evaluated the planner's absolute conditions, which
            # are often already true — the same defect that jammed steps,
            # except here it SKIPPED them. Off until it can use the watcher.
            look_ahead=None,
            on_stuck=self._guide_stuck,
            on_event=self._guide_event,
            on_finish=self._guide_finished,
            stuck_after_s=25.0,
            step_timeout_s=60.0,
            observer=__import__(
                "ai.verification", fromlist=["StateObserver"]
            ).StateObserver(),
            start_index=start_index,
            writing=writing,
            # The pause lets "sure, let me walk you through it" begin before
            # step one. Step one then goes on screen at once, and its line
            # waits for that answer to end (LG-01). From a streamed plan step
            # one comes ~2 s after the answer started, so this is a margin.
            present_delay_s=0.3 if writing else 0.8,
        )
        self._guide_runner = runner
        self._guide_stats = {
            "goal": guide.goal, "steps": len(guide.steps), "plan_s": None,
            "started": time.monotonic(), "auto": 0, "manual": 0, "by": {},
            "judged": 0, "targets": {}, "missed": 0, "stuck": 0, "nudged": 0,
            "asked": 0, "said": {}, "asked_at": asked_at, "arrow_s": None,
            "first_s": None, "first_word_s": None,
        }
        self._drop_step_audio()
        self._step_point.clear()
        # Step one's speech is NOT prefetched: it streams when presented,
        # first word in ~0.9 s (GW-08). Prefetching it as well synthesised
        # the same line twice whenever the prefetch had not finished in
        # time - which, at 3.3-4.1 s a line, was usually.
        self._submit(runner.start())

    def resume_guide(self) -> bool:
        """Restart a guide that was stopped part-way. True if there was one."""
        if not self._resume:
            return False
        guide, idx = self._resume
        self._start_guide(guide, start_index=idx)
        return True

    def _stop_guide(self) -> None:
        runner = self._guide_runner
        if runner is not None:
            runner.stop()
        self._drop_step_jobs()
        self._arrow_for = None
        self.sig_guide_release.emit()

    def _note_first_word(self) -> None:
        """The first sound of a walkthrough's first step went out. What the
        person actually waits for is this, not the step being "shown"."""
        stats = self._guide_stats
        asked_at = (stats or {}).get("asked_at")
        if asked_at and stats.get("first_word_s") is None:
            stats["first_word_s"] = time.monotonic() - asked_at
            _log.info("guide step one heard %.1fs after it was asked for",
                      stats["first_word_s"])

    def _note_target(self, idx: int, target: str, found, secs: float) -> None:
        """Which tier found a step's target, and how long it took.

        52 targets fell through to the vision model across fourteen
        walkthroughs, and until this line existed there was no way to see
        which, or why. GW-18 is measured from here.
        """
        stats = self._guide_stats
        via = getattr(found, "via", "") if found is not None else ""
        _log.info("guide step %d target %r %s in %.2fs", idx + 1, target[:40],
                  ("found via " + (via or "unknown")) if found is not None
                  else "NOT FOUND", secs)
        if stats is None:
            return
        if found is None:
            stats["missed"] += 1
        else:
            key = via or "unknown"
            stats["targets"][key] = stats["targets"].get(key, 0) + 1

    def _guide_event(self, kind: str, detail: dict) -> None:
        """What the runner reports, written down.

        Before this, fourteen walkthroughs in the log recorded their start
        and nothing else: not one ending, nudge or step outcome. The audit
        that led to GW-01 had to guess at durations. Every later walkthrough
        milestone is measured from these lines.
        """
        stats = self._guide_stats
        plan = self._lesson_plan
        total = len(plan.steps) if plan is not None else (stats or {}).get("steps", 0)
        idx = detail.get("index", -1)

        if kind == "step_shown":
            self._clock_step(idx, "shown")
            if stats is not None and stats.get("ended_at"):
                stats.setdefault("next_gaps", []).append(time.monotonic() - stats["ended_at"])
                stats["ended_at"] = None
            text = plan.steps[idx].instruction if plan and 0 <= idx < total else ""
            writing = plan is not None and plan is self._plan_in_progress
            _log.info("guide step %d/%s shown: %s", idx + 1,
                      "?" if writing else total, text[:70])
            asked_at = (stats or {}).get("asked_at")
            if asked_at and stats.get("first_s") is None:
                # The number GW-08 is judged by: asked -> first step up.
                stats["first_s"] = time.monotonic() - asked_at
                _log.info("guide step one up %.1fs after it was asked for",
                          stats["first_s"])

        elif kind == "step_unsaid":
            # LG-01: the step was on screen and done before there was a quiet
            # moment to say it. Counted: a guide where most lines go unsaid
            # is one where the conversation is always talking over it.
            _log.info("guide step %d was done before it could be said - not said", idx + 1)
            if stats is not None:
                stats["unsaid"] = stats.get("unsaid", 0) + 1

        elif kind == "step_ended":
            # The screen was just acted on: the control snapshot from the
            # question describes a screen that no longer exists (APP-04).
            self._pointables_at = 0.0
            outcome = detail.get("outcome", "")
            step = plan.steps[idx] if plan and 0 <= idx < total else None
            watcher = getattr(step, "_watcher", None) if step is not None else None
            how = (getattr(watcher, "done_by", "") or "") if outcome == "done" else ""
            judged = getattr(watcher, "judged", 0) if watcher is not None else 0
            said = "finished by itself" if outcome == "done" else "ended: " + outcome
            _log.info("guide step %d/%d %s after %.1fs%s%s", idx + 1, total, said,
                      detail.get("secs", 0.0),
                      (" (" + how + ")") if how else "",
                      (", %d vision check(s)" % judged) if judged else "")
            self._log_step_timeline(idx, step)
            if stats is not None:
                stats["ended_at"] = time.monotonic()
                stats["judged"] += judged
                if outcome == "done":
                    stats["auto"] += 1
                    key = _finish_kind(how)
                    stats["by"][key] = stats["by"].get(key, 0) + 1
                elif outcome in ("next", "back", "repeat"):
                    stats["manual"] += 1

        elif kind in ("stuck", "nudged"):
            rest = ", ".join("%s %ss" % (k, v) for k, v in detail.items() if k != "index")
            _log.info("guide step %d/%d %s (%s)", idx + 1, total, kind, rest)
            if stats is not None:
                stats[kind] += 1

        elif kind == "ended":
            if stats is None:
                _log.info("guide ended: %s", detail.get("reason", ""))
                return
            by = ", ".join("%s %d" % kv for kv in sorted(stats["by"].items())) or "none"
            targets = ", ".join("%s %d" % kv for kv in sorted(stats["targets"].items()))
            missed = (", %d not found" % stats["missed"]) if stats["missed"] else ""
            plan_s = stats.get("plan_s")
            planned = ("planned in %.1fs, " % plan_s) if plan_s is not None else ""
            # asked -> step one: its arrow, its presentation, its first word
            firsts = ", ".join("%s %.1fs" % (name, stats[key]) for name, key in (
                ("arrow", "arrow_s"), ("up", "first_s"), ("heard", "first_word_s"))
                if stats.get(key) is not None)
            if firsts:
                planned += "step one: %s, " % firsts
            finished = stats["auto"] + stats["manual"]
            by_vision = stats["by"].get("vision", 0)
            share = (" (vision decided %d%%)" % round(100 * by_vision / finished)
                     if finished else "")
            said = ", ".join("%s %d" % kv for kv in sorted((stats.get("said") or {}).items()))
            _log.info("guide scorecard: %s", self._scorecard(stats))
            _log.info(
                "guide ended (%s): %r - %d steps, %d finished by themselves (%s)%s, "
                "%d manual moves, %d vision checks, targets: %s%s, stuck %d, "
                "nudged %d, asked %d, commands: %s, %s%.0fs total",
                detail.get("reason", "?"), stats["goal"][:50], stats["steps"],
                stats["auto"], by, share, stats["manual"], stats["judged"],
                targets or "none", missed, stats["stuck"], stats["nudged"],
                stats.get("asked", 0), said or "none", planned,
                time.monotonic() - stats["started"])
            self._guide_stats = None

    def _guide_stopped(self, runner) -> None:
        """The runner's on_stop — clears state only if it is still the active
        runner, so a guide replaced by a newer one cannot wipe the new one."""
        if self._guide_runner is runner:
            self._guide_runner = None
            # If it was stopped part-way, remember where so "continue" can pick
            # it back up; a finished guide leaves nothing to resume.
            if not runner.completed and runner.index >= 0 and self._lesson_plan:
                self._resume = (self._lesson_plan, runner.index)
            self._lesson_plan = None
            self._lesson_steps = []
            self._lesson_step_idx = 0
            self._drop_step_audio()
            self._step_point.clear()
            self._drop_step_jobs()
            # A held walkthrough arrow outlives its guide otherwise (LG-02).
            self._arrow_for = None
            self.sig_guide_release.emit()
            try:
                from tutor_features.step_watch import _clicks
                _clicks.stop()
            except Exception:
                pass
            self.sig_guide_progress.emit(0, 0, "")
            self._activity("")
            if self._state != AppState.IDLE:
                self._emit_state(AppState.IDLE)

    async def _guide_finished(self, ahead: bool) -> None:
        """The walkthrough is complete: say so, show what was done, keep it.

        Before GW-16 a finished walkthrough ended on one spoken line and
        vanished - nothing on screen, nothing kept, and the live model never
        knew it had happened. Now: a closing line, a one-line summary under
        it, and the walkthrough saved to the journal (the journal already
        keeps every answer Tora gives, unasked), so it turns up in "what did
        I learn today" and its plan can be replayed.
        """
        plan = self._lesson_plan
        if plan is None:
            await self._reply_local("That's it - all done!")
            return
        stats = self._guide_stats
        secs = time.monotonic() - stats["started"] if stats else 0.0
        saved = self._save_walkthrough(plan, secs)
        self._guide_last = (plan, time.monotonic(), saved)
        spoken = ("Looks like you're already there - nicely done!" if ahead
                  else "That's it - all done!")
        if saved:
            spoken += " I've saved the steps to your journal."
        summary = _walkthrough_summary(plan.goal, len(plan.steps), secs)
        await self._reply_local(spoken, shown=f"{spoken}\n✓ {summary}")

    def _save_walkthrough(self, plan, secs: float) -> bool:
        """Keep a finished walkthrough in the journal. Never raises: a
        journal that cannot be written must not spoil the ending."""
        if not self._journal_enabled:
            return False
        try:
            title = active_window_title()
            entry = journal.log_walkthrough(
                plan.goal, [s.instruction for s in plan.steps],
                plan=gemini_planner.guide_to_dict(plan),
                app_key=app_key(title), window_title=title, seconds=secs)
        except Exception:  # noqa: BLE001
            _log.warning("could not save the walkthrough to the journal", exc_info=True)
            return False
        if entry > 0:
            _log.info("walkthrough saved to the journal (entry %d)", entry)
        return entry > 0

    async def _guide_stuck(self, idx: int) -> None:
        """Nothing has moved for a while and the step isn't done — help.

        A person sitting next to you would not keep silent until a timeout
        expired; they would notice you hadn't moved and say "here, try it this
        way". So: put the pointer back on the target and offer a genuinely
        DIFFERENT route from the ones the planner listed, rather than
        repeating the same instruction louder.
        """
        plan = self._lesson_plan
        if plan is None or not (0 <= idx < len(plan.steps)):
            return
        step = plan.steps[idx]

        # Re-reading the instruction was the single most-complained-about
        # behaviour ("it repeats things"). Point again and say almost
        # nothing; offer a different route only if the planner gave one.
        if step.target:
            self._submit(self._point_step_target(idx, step))

        other = self._step_alternative(step)
        if other:
            msg = f"Or you can {other[0].lower()}{other[1:]}"
        elif step.target:
            msg = "It's right here."
        else:
            msg = "Say next when you've done it."
        await self._reply_local(msg)

    def _guide_busy(self) -> bool:
        """True while Tora is doing something the guide must not talk over."""
        if self._realtime is not None and self._realtime.speaking:
            return True
        return self._state in (AppState.THINKING, AppState.LISTENING)

    def _step_line(self, idx: int) -> str:
        """What a step sounds like. Deliberately short.

        The old line tacked "You'll know it worked when…" onto every step,
        which doubled the words — and every word is speech-synthesis time the
        user waits through. The completion hint is on the progress pill
        instead; out loud, one crisp instruction is faster and clearer.
        """
        plan = self._lesson_plan
        if plan is None or not (0 <= idx < len(plan.steps)):
            return ""
        step = plan.steps[idx]
        if plan is self._plan_in_progress:
            # How many steps there are is not known yet (GW-08).
            line = f"Step {idx + 1}: {step.instruction}"
        else:
            line = f"Step {idx + 1} of {len(plan.steps)}: {step.instruction}"
        if getattr(step, "waiting", False):
            # Say that nothing needs doing and that Tora will speak up - or a
            # person sits there wondering whether they missed an instruction.
            line += " This takes a moment - I'll tell you when it's done."
        elif idx > 0 and getattr(plan.steps[idx - 1], "waiting", False):
            line = "That's finished. " + line
        return line

    def _drop_step_audio(self) -> None:
        """Forget prepared step speech, and stop what is still downloading."""
        for prepared in self._step_audio.values():
            try:
                prepared.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._step_audio.clear()

    async def _prefetch_step(self, idx: int) -> None:
        """Start the step's SPEECH downloading in advance.

        Synthesising a line takes seconds, so doing it at the moment the step
        is due meant the user sat through a long silence first. Doing it while
        the previous step is still being performed makes presenting the next
        one instant. Speech is safe to prepare early because the words do not
        depend on what is on screen — unlike the pointer target, see below.

        It is a SpeechBuffer (GW-08): playable before it has all arrived, so
        a step that comes up sooner than expected starts from what is there
        instead of being synthesised a second time.
        """
        plan = self._lesson_plan
        if plan is None or not (0 <= idx < len(plan.steps)):
            return
        if idx not in self._step_audio:
            try:
                self._step_audio[idx] = self._get_tts().prepare(
                    _speakable(self._step_line(idx)))
            except Exception:
                _log.debug("step audio prefetch failed", exc_info=True)
        # NOTE: only the SPEECH is prefetched, never the pointer coordinates.
        # Prefetching a target resolves it against the screen as it looks NOW,
        # which is the screen BEFORE the current step has been performed — and
        # the next step's target usually does not exist yet ("Background" is
        # not on screen until Personalization is opened). Resolving early
        # either finds nothing, or worse, matches something unrelated that
        # happens to be visible and then confidently points at the wrong
        # place. Targets are resolved when the step is actually presented.

    async def _present_step(self, idx: int) -> None:
        """Show one guide step and say it - the two halves the runner now
        calls separately (LG-01): _display_step at once, _speak_step_line
        when it is quiet."""
        await self._display_step(idx)
        await self._speak_step_line(idx)

    async def _display_step(self, idx: int) -> None:
        """Put a step on screen: its words, its arrow, its progress pill -
        and start watching for it being done. Nothing here makes a sound,
        so nothing here waits for a quiet moment (LG-01): on 24 Sep the arrow
        and the words were held behind every sentence Tora or the user said.
        """
        plan = self._lesson_plan
        if plan is None or not (0 <= idx < len(plan.steps)):
            return
        self._lesson_step_idx = idx
        self._lesson_nagged = False
        self._drop_step_jobs(keep=idx)
        # The last step's arrow and box point at something that is done:
        # off with them, before this step's arrow flies in. Boxes used to
        # pile up, one per step, for the whole walkthrough. Not this step's
        # own arrow, though - step one's is often up before step one is
        # (GW-08), and a repeat keeps its arrow where it is.
        if self._arrow_for != (id(plan), idx):
            self._arrow_for = None
            self.sig_guide_release.emit()
            self.sig_clear_drawings.emit()
        step = plan.steps[idx]
        self._reset_step_verification(step)
        msg = self._step_line(idx)

        # Watch for the user DOING the step: a click on the pointed control,
        # or a real change on screen judged against a before-shot. Started
        # before the sentence is spoken so clicking mid-sentence counts.
        nxt = plan.steps[idx + 1] if idx + 1 < len(plan.steps) else None
        step._watcher = self._make_step_watcher(
            step, next_target=getattr(nxt, "target", "") or "")
        self._submit(step._watcher.begin())

        # Get the step AFTER this one ready while this one is being spoken.
        self._submit(self._prefetch_step(idx + 1))

        # A negative total is "at least this many, more coming": the plan is
        # still being written (GW-08). _plan_written sends the real count.
        writing = plan is self._plan_in_progress
        self.sig_guide_progress.emit(idx + 1, -(idx + 1) if writing else len(plan.steps),
                                     step.instruction)

        # Resolve and point NOW, against the screen as it actually is. This is
        # deliberately not prefetched — see _prefetch_step.
        if step.target:
            self._submit(self._point_step_target(idx, step))

        self.sig_response_chunk.emit(msg)
        self.sig_response_done.emit(msg)
        self._last_response = msg
        if self._guide_runner is not None and self._guide_runner.running:
            self._activity("waiting")

    async def _speak_step_line(self, idx: int) -> None:
        """Say a step that is already on screen. The runner's voice lane
        calls this once it is quiet - and not at all if the user finished
        the step first."""
        plan = self._lesson_plan
        if plan is None or not (0 <= idx < len(plan.steps)):
            return
        msg = self._step_line(idx)
        self._emit_state(AppState.SPEAKING)

        def heard() -> None:
            self._clock_step(idx, "voice")
            self._note_first_word()

        try:
            prepared = self._step_audio.pop(idx, None)
            if prepared is not None:
                await prepared.play(on_start=heard)
            else:
                await self._get_tts().speak(_speakable(msg), on_start=heard)
        except Exception:
            pass
        self._emit_state(AppState.IDLE)
        # Step spoken — now we're watching for them to do it. Say so, rather
        # than going quiet and looking like nothing is happening.
        if self._guide_runner is not None and self._guide_runner.running:
            self._activity("waiting")

    # A visual step is re-checked with the vision model no more often than
    # this — a look costs a capture + a round trip, unlike the ~5 ms UIA read.
    _VISUAL_RECHECK_S = 3.5
    # How often the local verification layers actually run (they are polled
    # far more often than this; the last verdict is reused in between).
    _VERIFY_EVERY_S = 0.9
    # Conditions that need a UIA tree walk — throttled, see _step_done.
    _SLOW_KINDS = ("control_appears", "control_gone", "url_contains")
    _SLOW_RECHECK_S = 1.2

    @staticmethod
    def _reset_step_verification(step) -> None:
        """Forget what we previously concluded about this step.

        Verdicts are cached on the step object to keep polling cheap. That
        cache has to be dropped whenever the step is (re)presented — on a
        repeat, a back, or a resume the old verdict is very likely "done",
        and the runner only advances on a false->true transition, so a stale
        "done" means it can never arm and the step hangs until the timeout.
        """
        w = getattr(step, "_watcher", None)
        if w is not None:
            try:
                w.end()
            except Exception:
                pass
        for attr in ("_watcher", "_verdict", "_verify_at", "_slow_verdict",
                     "_slow_at", "_visual_verdict", "_visual_at"):
            if hasattr(step, attr):
                try:
                    delattr(step, attr)
                except Exception:
                    pass

    async def _step_done(self, step):
        """Has this step's OUTCOME been reached — however the user got there?

        Runs the verification engine: cheap local signals first (is the right
        app in front, did the window title change, did a control appear, did
        the browser navigate), and only a vision check if nothing local can
        tell. Any single satisfied signal counts, which is what lets someone
        press Ctrl+S instead of clicking Save and still be counted as done.

        Returns True / False / None (unsure) for the runner's arming and
        debounce logic, driven by the verdict's confidence.
        """
        if not self._guide_auto_advance:
            return None            # tray switch off: only 'next' advances
        w = getattr(step, "_watcher", None)
        if w is not None:
            result = await w.poll()
            self._look_ahead(w)
            if result is not True and w.unsure and not getattr(step, "_asked_unsure", False):
                step._asked_unsure = True
                self._submit(self._ask_if_done(step, w))
            return result
        return await self._step_done_by_expectation(step)

    async def _ask_if_done(self, step, watcher) -> None:
        """The screen has changed twice and the vision model said "not done"
        both times. Ask - once - rather than stall in silence until the
        minute-long nudge, and never advance on a guess.

        The target is pointed at again first, because the likeliest story is
        that the person did something nearby but not quite the step, and the
        arrow shows them where it actually is. A "yes" is a "next": the
        per-turn guide context already routes that to guide_step.
        """
        plan = self._lesson_plan
        idx = plan.steps.index(step) if plan is not None and step in plan.steps else -1
        _log.info("guide step %d unsure after %d 'not done yet' answers (%s) - asking",
                  idx + 1, watcher.not_yet, watcher.last_reason[:80])
        if self._guide_stats is not None:
            self._guide_stats["asked"] = self._guide_stats.get("asked", 0) + 1
        if getattr(step, "target", ""):
            self._submit(self._point_step_target(idx, step))
        await self._reply_local(
            "I can't quite tell if that worked. If it did, say next and I'll move on.")

    async def _step_done_by_expectation(self, step):
        """The previous engine: the planner's predicted end state. Kept as
        the fallback when no watcher exists, and as the watcher's third
        signal (only when it was false at step start)."""
        from ai import verification

        expected = getattr(step, "expected", None)
        if expected is None or not expected:
            # No outcome declared — fall back to the single legacy condition.
            return await self._legacy_step_done(step)

        if expected.has_local_signal:
            # Throttled: the local layers are cheap individually, but a
            # control_appears miss still walks the tree, and while a step is
            # outstanding the answer is "no" — the expensive case.
            now = time.monotonic()
            if now - getattr(step, "_verify_at", 0.0) < self._VERIFY_EVERY_S:
                cached = getattr(step, "_verdict", None)
                if cached is not None:
                    return self._verdict_to_tristate(cached)
            verdict = await asyncio.to_thread(verification.verify_local, expected)
            step._verify_at = time.monotonic()
            step._verdict = verdict
            if verdict.done:
                _log.info("step verified by %s (%s)", verdict.method, verdict.detail)
                return True
            if not verdict.needs_vision:
                return self._verdict_to_tristate(verdict)

        # Nothing local could confirm it and the step described something
        # visible — ask the eyes, on their own slower clock.
        if expected.visual:
            return await self._visual_done(
                step, _VisualCond(expected.visual),
            )
        return False

    @staticmethod
    def _verdict_to_tristate(verdict):
        from ai import verification
        if verdict.confidence >= verification.ADVANCE:
            return True
        if verdict.confidence < verification.UNCERTAIN:
            return False
        return None      # middling — keep watching rather than guess

    async def _legacy_step_done(self, step):
        """Single-condition path, for plans without an expected_state."""
        cond = getattr(step, "completion", None)
        if cond is None:
            return None
        if cond.checkable:
            from ai import guide_state
            if cond.kind in self._SLOW_KINDS:
                now = time.monotonic()
                if now - getattr(step, "_slow_at", 0.0) < self._SLOW_RECHECK_S:
                    return getattr(step, "_slow_verdict", None)
                verdict = await asyncio.to_thread(guide_state.check, cond)
                step._slow_at = time.monotonic()
                step._slow_verdict = verdict
                return verdict
            return await asyncio.to_thread(guide_state.check, cond)
        if cond.kind == "visual" and cond.value:
            return await self._visual_done(step, cond)
        return None

    async def _visual_done(self, step, cond):
        """Yes/no vision check for a step no accessibility signal can see.

        Throttled: the actual look runs at most every few seconds, and the
        last verdict is returned in between, so the runner's debounce still
        works without a vision call on every poll.
        """
        now = time.monotonic()
        if now - getattr(step, "_visual_at", 0.0) < self._VISUAL_RECHECK_S:
            return getattr(step, "_visual_verdict", False)
        step._visual_at = now
        if self._privacy_guard and is_sensitive_window(active_window_title()):
            step._visual_verdict = False
            return False
        active = await self._one_shot(1024)
        if active is None:
            return getattr(step, "_visual_verdict", False)
        verdict = await gemini_vision.confirm(cond.value, active.base64_jpeg)
        step._visual_verdict = bool(verdict)
        return step._visual_verdict

    async def _guide_look_ahead(self, from_idx: int):
        """The furthest of the next couple of steps the user has already done.

        Catches the user getting ahead of the guide — doing two steps quickly,
        or clicking straight past one. Reads screen state once and checks the
        upcoming checkable steps against it. Returns an index, or None.
        """
        plan = self._lesson_plan
        if plan is None:
            return None
        upcoming = [
            (i, plan.steps[i].completion)
            for i in range(from_idx + 1, min(from_idx + 3, len(plan.steps)))
        ]
        upcoming = [(i, c) for i, c in upcoming if c.checkable]
        if not upcoming:
            return None
        from ai import guide_state
        want_url = any(guide_state.needs_url(c) for _, c in upcoming)
        state = await asyncio.to_thread(guide_state.read_state, want_url=want_url)
        hit = None
        for i, c in upcoming:
            if guide_state.evaluate(c, state) is True:
                hit = i          # keep the furthest satisfied step
        return hit

    async def _advance_lesson_step(self, ak: str):
        """User said 'next' — validate the current step, then speak the next.

        Before moving on, check whether the step they are on actually looks
        done. If it has a machine-checkable condition that is NOT met, say so
        once and hold — a gentle "that doesn't look done yet" instead of
        marching ahead. Saying "next" again advances regardless, because it is
        the user's call in the end. (M4 turns this same check into automatic
        advancing, with no "next" needed at all.)
        """
        plan = self._lesson_plan
        cur = self._lesson_step_idx
        if (plan is not None and cur < len(plan.steps)
                and not self._lesson_nagged):
            done = await self._step_done(plan.steps[cur])
            if done is False:
                self._lesson_nagged = True
                step = plan.steps[cur]
                msg = (
                    "That doesn't look done yet — "
                    f"{step.instruction[0].lower()}{step.instruction[1:]} "
                    "Say 'next' again when you're ready to move on."
                )
                await self._reply_local(msg)
                return
        self._lesson_nagged = False

        self._lesson_step_idx += 1
        if self._lesson_step_idx >= len(self._lesson_steps):
            msg = "That's the last step — you're done!"
            self._lesson_steps = []
            self._lesson_plan = None
            self._lesson_step_idx = 0
            self._lesson_nagged = False
        else:
            total = len(self._lesson_steps)
            plan = self._lesson_plan
            if plan is not None and self._lesson_step_idx < len(plan.steps):
                # The planned step knows what it acts on and how the user
                # can tell it worked, so say both.
                msg = plan.steps[self._lesson_step_idx].spoken(
                    self._lesson_step_idx, total
                )
                target = plan.steps[self._lesson_step_idx].target
                if target and self._screens_ctx:
                    self._submit(self._point_at_named(target))
            else:
                step = self._lesson_steps[self._lesson_step_idx]
                msg = f"Step {self._lesson_step_idx + 1} of {total}: {step}"

        self.sig_response_chunk.emit(msg)
        self.sig_response_done.emit(msg)
        self._emit_state(AppState.SPEAKING)
        try:
            await self._get_tts().speak(msg)
        except Exception:
            pass
        self._emit_state(AppState.IDLE)

    # ── Pointing ──────────────────────────────────────────────────────────────

    async def _point_at_named(self, name: str) -> None:
        """Point at an element by name — used when a lesson step names one."""
        if not self._screens_ctx:
            return
        try:
            found = await self._locate_target(name, self._active_shot() or self._screens_ctx[0])
        except Exception:
            return
        if found is not None:
            self.sig_point_at.emit(float(found.x), float(found.y), name[:24])
            self._remember_ref(found.x, found.y, name[:24])

    async def _locate_target(self, question: str, shot):
        """Cascade-path pointing. Logical screen pixels, or None.

        Same chain as the realtime path — UIA → OCR → vision — so both routes
        point with the same reliability. The screenshot is already in hand
        here, so it is passed straight through.
        """
        return await self._resolve_point(question, shot=shot)

    # ── Coordinate mapping ────────────────────────────────────────────────────
    #
    # The LLM emits NORMALIZED 0-1000 coordinates relative to the screenshot
    # it saw. The overlay draws in LOGICAL screen pixels. These helpers convert
    # between the two using the ScreenShot metadata captured this turn.

    # SP-10: a look on the mouse's monitor, not the one in front. Its
    # drawings - and the voice's own draws from its highlights - are on that
    # monitor's 0-1000, so they map back through it for a while.
    _mouse_shot: Optional[tuple] = None          # (the shot, when)
    MOUSE_SHOT_S = 90.0

    def _shot(self, screen_idx: int = 1):
        # Only the active monitor is sent to the model now, so the normalized
        # coordinates it returns are relative to that image regardless of the
        # "screenN" it names — always map them back through the active shot.
        pinned = self._mouse_shot
        if pinned is not None and time.monotonic() - pinned[1] < self.MOUSE_SHOT_S:
            return pinned[0]
        active = self._active_shot()
        if active is not None:
            return active
        for s in self._screens_ctx:
            if s.index == screen_idx:
                return s
        return self._screens_ctx[0] if self._screens_ctx else None

    def _active_geometry(self, screen_idx: int = 1):
        """(left, top, width, height) of the active monitor in LOGICAL px.

        Falls back to reading monitor geometry directly when no screenshot has
        been taken. That fallback is the whole point: drawing only needs to
        know how big the screen is, not what is on it, so requiring a capture
        first meant the assistant could not draw unless it had already paid
        for a look — which is why it so rarely drew anything.
        """
        shot = self._shot(screen_idx)
        if shot is not None:
            dpi = shot.dpi_scale or 1.0
            return (shot.logical_left, shot.logical_top,
                    shot.physical_width / dpi, shot.physical_height / dpi)
        try:
            from tutor import foreground_window_center
            centre = foreground_window_center()
            mons = monitors.monitors()
            m = (monitors.monitor_at(centre[0], centre[1], mons) if centre is not None
                 else monitors.primary(mons))
            # The monitor's own logical rectangle - its own scaling, its own
            # corner - not the physical one divided by the system scaling.
            return tuple(m.logical) if m is not None else None
        except Exception:
            return None

    def _denorm(self, nx: float, ny: float, screen_idx: int = 1):
        """Normalized 0-1000 (screen space) → logical screen pixels."""
        geo = self._active_geometry(screen_idx)
        if geo is None:
            return float(nx), float(ny)
        left, top, log_w, log_h = geo
        shot = self._shot(screen_idx)
        # Legacy safety: values beyond 1000 are raw pixels in the downscaled
        # JPEG the model saw — scale by the JPEG dimensions instead.
        bx = by = 1000.0
        if shot is not None and (nx > 1000 or ny > 1000):
            bx = float(max(shot.width, 1))
            by = float(max(shot.height, 1))
        return left + (nx / bx) * log_w, top + (ny / by) * log_h

    def _denorm_len(self, n: float, screen_idx: int = 1) -> float:
        """Normalized length (0-1000 x-units) → logical pixels."""
        geo = self._active_geometry(screen_idx)
        if geo is None:
            return float(n)
        return (n / 1000.0) * geo[2]

    def _norm(self, x: float, y: float, screen_idx: int = 1):
        """Logical screen pixels → normalized 0-1000 (for prompt injection)."""
        shot = self._shot(screen_idx)
        if shot is None:
            return int(x), int(y)
        log_w = shot.physical_width / shot.dpi_scale
        log_h = shot.physical_height / shot.dpi_scale
        nx = (x - shot.logical_left) / max(log_w, 1) * 1000
        ny = (y - shot.logical_top) / max(log_h, 1) * 1000
        return int(round(nx)), int(round(ny))

    def _resolve_anchor(self, name: str):
        """Resolve '@element name' → logical bbox via UIA (fast tier only)."""
        try:
            from ai.hybrid_pointer import find_target
            # skip_vision was dropped from find_target when the vision tier
            # moved to the manager, and passing it raised - caught below, so
            # every "@element" anchor quietly resolved to nothing.
            t = find_target(name, skip_ocr=True)
            if t is None:
                return None
            return monitors.rect_to_logical(*t.bbox)
        except Exception:
            return None

    def _parse_points(self, text: str):
        """Live-during-stream tags: pointing and board-clear only. Drawing
        tags are deferred and played back in sync with narration."""
        for match in POINT_RE.finditer(text):
            x, y, label, scr = match.groups()
            lx, ly = self._denorm(float(x), float(y), int(scr))
            self.sig_point_at.emit(lx, ly, label.strip())
        if CLEAR_RE.search(text):
            self.sig_clear_drawings.emit()

    # ── Vertex snapping (figure-detector assisted accuracy) ─────────────────

    def _snap_pt(self, nx: float, ny: float, thresh: float = 35.0):
        """Snap a normalized point to the nearest detected-figure vertex."""
        best, bd = None, thresh
        for fig in self._figures_ctx:
            for (vx, vy) in fig.vertices:
                d = math.hypot(nx - vx, ny - vy)
                if d < bd:
                    bd, best = d, (float(vx), float(vy))
        return best if best is not None else (nx, ny)

    def _angle_rot_for_vertex(self, nx: float, ny: float):
        """Rotation (deg) that puts a right-angle marker INSIDE the detected
        polygon at vertex (nx,ny), aligned with its two edges. None if the
        point is not a detected vertex."""
        for fig in self._figures_ctx:
            verts = fig.vertices
            if len(verts) < 3:
                continue
            for i, (vx, vy) in enumerate(verts):
                if math.hypot(nx - vx, ny - vy) > 6:
                    continue
                P = self._denorm(vx, vy)
                A = self._denorm(*verts[i - 1])
                B = self._denorm(*verts[(i + 1) % len(verts)])
                a1 = math.degrees(math.atan2(A[1] - P[1], A[0] - P[0]))
                a2 = math.degrees(math.atan2(B[1] - P[1], B[0] - P[0]))
                rot = a1
                # Marker spans [rot, rot+90]; flip if the second edge sits on
                # the other side so the square lies between the two edges.
                if (a2 - rot) % 360 > 180:
                    rot -= 90
                return rot
        return None

    # ── Drawing-tag extraction (deferred, order-preserving) ──────────────────

    def _shape_from_tag(self, tag: str):
        m = CIRCLE_AT_RE.fullmatch(tag)
        if m:
            name, color = m.groups()
            bbox = self._resolve_anchor(name.strip())
            if not bbox:
                return None
            l, t, r, b = bbox
            return {"kind": "circle", "x": (l + r) / 2, "y": (t + b) / 2,
                    "r": max(r - l, b - t) / 2 + 10, "label": "",
                    "color": color or "tora"}
        m = UNDERLINE_AT_RE.fullmatch(tag)
        if m:
            name, color = m.groups()
            bbox = self._resolve_anchor(name.strip())
            if not bbox:
                return None
            l, t, r, b = bbox
            return {"kind": "underline", "x": l, "y": b + 3, "w": r - l,
                    "color": color or "tora"}
        m = LINE_RE.fullmatch(tag) or ARROW_RE.fullmatch(tag)
        if m:
            kind = "line" if tag.startswith("[LINE") else "arrow"
            x1, y1, x2, y2, color = m.groups()
            n1 = self._snap_pt(float(x1), float(y1))
            n2 = self._snap_pt(float(x2), float(y2))
            return {"kind": kind, "pts": [self._denorm(*n1), self._denorm(*n2)],
                    "color": color or "tora"}
        m = CIRCLE_RE.fullmatch(tag)
        if m:
            x, y, r, label, color = m.groups()
            cx, cy = self._denorm(float(x), float(y))
            return {"kind": "circle", "x": cx, "y": cy,
                    "r": max(12.0, self._denorm_len(float(r))),
                    "label": (label or "").strip(), "color": color or "tora"}
        m = RECT_RE.fullmatch(tag)
        if m:
            x1, y1, x2, y2, color = m.groups()
            p1 = self._denorm(*self._snap_pt(float(x1), float(y1)))
            p2 = self._denorm(*self._snap_pt(float(x2), float(y2)))
            return {"kind": "rect", "x1": p1[0], "y1": p1[1],
                    "x2": p2[0], "y2": p2[1], "color": color or "tora"}
        m = POLY_RE.fullmatch(tag)
        if m:
            pts_str, color = m.groups()
            pts = [self._denorm(*self._snap_pt(float(a), float(b)))
                   for a, b in re.findall(r'(\d+),(\d+)', pts_str)]
            if len(pts) < 3:
                return None
            return {"kind": "poly", "pts": pts, "color": color or "tora"}
        m = TEXT_RE.fullmatch(tag)
        if m:
            x, y, content, color, size = m.groups()
            lx, ly = self._denorm(float(x), float(y))
            return {"kind": "text", "x": lx, "y": ly, "text": content.strip(),
                    "color": color or "tora", "size": size or "m"}
        m = ANGLE_RE.fullmatch(tag)
        if m:
            x, y, s, rot, color = m.groups()
            nx, ny = self._snap_pt(float(x), float(y))
            auto_rot = self._angle_rot_for_vertex(nx, ny)
            lx, ly = self._denorm(nx, ny)
            return {"kind": "angle", "x": lx, "y": ly,
                    "s": max(10.0, self._denorm_len(float(s))),
                    "rot": auto_rot if auto_rot is not None else float(rot or 0),
                    "color": color or "tora"}
        m = UNDERLINE_RE.fullmatch(tag)
        if m:
            x, y, w, color = m.groups()
            lx, ly = self._denorm(float(x), float(y))
            return {"kind": "underline", "x": lx, "y": ly,
                    "w": max(8.0, self._denorm_len(float(w))),
                    "color": color or "tora"}
        m = LABEL_RE.fullmatch(tag)
        if m:
            x, y, txt, color = m.groups()
            lx, ly = self._denorm(float(x), float(y))
            return {"kind": "text", "x": lx, "y": ly, "text": txt.strip(),
                    "color": color or "tora", "size": "s"}
        return None

    def _extract_shapes(self, text: str) -> list:
        """All drawing shapes in a piece of text, in document order."""
        shapes = []
        for m in ANY_TAG_RE.finditer(text):
            try:
                sh = self._shape_from_tag(m.group(0))
            except Exception:
                sh = None
            if sh:
                shapes.append(sh)
        return shapes

    # ── Teacher-style narrated playback ──────────────────────────────────────

    def _segment_lesson(self, full_response: str) -> list:
        """Split a response into (sentence, [shapes]) pairs, preserving which
        sentence each drawing tag belongs to."""
        tags: list[str] = []

        def _stash(m):
            tags.append(m.group(0))
            return f"\x00{len(tags) - 1}\x00"

        masked = ANY_TAG_RE.sub(_stash, full_response)
        parts = re.split(r'(?<=[.!?])\s+', masked)
        out = []
        for part in parts:
            ids = [int(i) for i in re.findall(r'\x00(\d+)\x00', part)]
            shapes = self._extract_shapes("".join(tags[i] for i in ids))
            clean = re.sub(r'\s+', ' ', re.sub(r'\x00\d+\x00', ' ', part)).strip()
            if clean or shapes:
                out.append((clean, shapes))
        return out

    async def _play_lesson(self, full_response: str, clean: str):
        """Narrate sentence by sentence, drawing each sentence's shapes as it
        is spoken — the cadence of a teacher at a whiteboard. Falls back to
        plain TTS when the response contains no drawings."""
        segments = self._segment_lesson(full_response)
        if not any(shapes for _, shapes in segments):
            await self._get_tts().speak(_speakable(clean))
            return

        try:
            from ui.overlay import (
                _shape_length, STROKE_SPEED_PX_S,
                SHAPE_DRAW_MIN_S, SHAPE_DRAW_MAX_S, SHAPE_GAP_SECONDS,
            )
        except Exception:
            _shape_length = None

        draw_end = time.monotonic()
        for text, shapes in segments:
            if self._cancel_flag:
                break
            draw_end = max(draw_end, time.monotonic())
            for sh in shapes:
                self.sig_draw.emit(sh)
                if _shape_length is not None:
                    dur = _shape_length(sh) / STROKE_SPEED_PX_S
                    dur = max(SHAPE_DRAW_MIN_S, min(SHAPE_DRAW_MAX_S, dur))
                    draw_end += dur + SHAPE_GAP_SECONDS
                else:
                    draw_end += 1.0
            if text:
                try:
                    await self._get_tts().speak(_speakable(text))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            # A real teacher finishes the stroke before the next sentence —
            # wait out any drawing time the narration didn't cover.
            remaining = draw_end - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(min(remaining, 4.0) + 0.1)

    # What the assistant is doing, in the user's words. Never model or tool
    # names — "Analysing your screen", not "gemini vision request".
    ACTIVITY = {
        "understanding": "Understanding what you need…",
        "screen":        "Analysing your screen…",
        "finding":       "Finding the right spot…",
        "planning":      "Working out the steps…",
        "verifying":     "Checking if that worked…",
        "waiting":       "Waiting for you…",
        "searching":     "Looking that up…",
        "writing":       "Writing it for you…",
        "reading_email": "Reading the email…",
        "reading_code": "Reading your editor…",
    }

    def _activity(self, key: str) -> None:
        """Tell the overlay what is happening. "" clears it.

        Called around every operation that can take more than a moment, so
        the user is never left watching a still screen wondering whether the
        assistant is thinking or has died.
        """
        try:
            self.sig_activity.emit(key, self.ACTIVITY.get(key, "") if key else "")
        except Exception:
            pass

    def _emit_state(self, state: AppState):
        asked = _ANSWERING.get()
        if asked and asked != self._question_no:
            # An answer the user talked over (_interrupt_answer), in its last
            # moments: the state belongs to the question asked since.
            return
        self._state = state
        # Wake-word scanning only means anything while Tora is idle: while
        # she thinks or speaks, a press is the way in (_claim_begin). Left
        # running, it paid to transcribe Tora's own answer coming back
        # through the microphone.
        try:
            self._listener.set_wake_paused(state is not AppState.IDLE)
        except Exception:
            pass
        self.sig_state_changed.emit(state)

    # ── Settings ──────────────────────────────────────────────────────────────

    def set_model(self, model: str):
        self._current_model = model

    def refresh_models(self):
        """Public — bound to Tray → Model → Refresh model list."""
        self._submit(self._refresh_one_model_list("gemini"))

    async def _refresh_one_model_list(self, provider: str = "gemini"):
        try:
            from ai.model_registry import refresh
            ms = await refresh()
            self.sig_models_refreshed.emit("gemini", len(ms))
        except Exception as e:
            self.sig_error.emit(f"Model refresh failed: {e}")

    def set_conversation_mode(self, mode: str):
        """Tray callback — realtime websocket or the cascade.

        The live session carries the old mode's setup, so it is dropped here
        rather than left to be discovered as wrong on the next question.
        """
        cfg.set_conversation_mode(mode)
        self._drop_realtime()

    def set_voice(self, name: str):
        """Tray callback — which Gemini voice speaks.

        Both routes have to be told: the voice is fixed in the live session's
        setup frame, so that session is retired, and the cascade's synthesiser
        keeps its own copy.
        """
        cfg.set_voice(name)
        self._drop_realtime()
        if self._tts is not None and hasattr(self._tts, "set_voice"):
            self._tts.set_voice(name)

    def _drop_realtime(self) -> None:
        """Retire the live session and open a replacement straight away.

        Voice and conversation mode are fixed in the setup frame, so changing
        either needs a new session. Reconnecting now rather than lazily keeps
        the next question as fast as the last one.
        """
        conv, self._realtime = self._realtime, None
        self._rt_streaming = False
        if conv is not None and self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(conv.close(), self._loop)
            except Exception:
                pass
        self._submit(self._realtime_connect())

    def set_custom_instructions(self, text: str):
        """Tray callback — restrict/steer what Tora helps with. Persists
        to .env so it survives a restart, not just this session."""
        cfg.custom_instructions = text.strip()
        try:
            from dotenv import set_key
            # cfg.env_path(), not this file's folder: in the packaged app
            # that is _internal\, and the setting was saved where nothing
            # ever reads it (APP-03).
            env_path = cfg.env_path()
            if not env_path.exists():
                env_path.touch()
            escaped = text.strip().replace("\n", "\\n")
            set_key(str(env_path), "CUSTOM_INSTRUCTIONS", escaped)
        except Exception as e:
            self.sig_error.emit(f"Could not save instructions: {e}")

    def set_stt_language(self, code: str):
        """Pin the language transcription runs in. "" = auto-detect.

        Auto-detect re-decides on every single utterance, which is unreliable
        for code-mixed speech: Hinglish flips between Hindi and English turn to
        turn, and the transcript quality swings with it. Pinning one language
        makes transcription consistent.
        """
        code = (code or "").strip()
        cfg.stt_language = code
        try:
            from dotenv import set_key
            env_path = cfg.env_path()
            if not env_path.exists():
                env_path.touch()
            set_key(str(env_path), "STT_LANGUAGE", code)
        except Exception:
            pass
        # Drop the cached engine so the next transcription picks this up.
        self._stt = None

    def set_response_language(self, code: str):
        """Tray callback — pin Tora's reply language ('' = auto-detect)."""
        cfg.response_language = code
        try:
            from dotenv import set_key
            env_path = cfg.env_path()          # see set_custom_instructions
            if not env_path.exists():
                env_path.touch()
            set_key(str(env_path), "RESPONSE_LANGUAGE", code)
        except Exception as e:
            self.sig_error.emit(f"Could not save language setting: {e}")

    def set_mic_device(self, device_index: int):
        """Tray callback — switch input device without restarting the app."""
        cfg.set_mic_device(device_index)     # saved with its name, not just its number
        try:
            self._listener.stop()
        except Exception:
            pass
        from audio.ambient_listener import AmbientListener
        self._listener = AmbientListener(
            on_level=self._handle_level,
            on_wake=self._handle_wake,
            device=cfg.mic_device(),
            ambient=cfg.ambient_mic(),
            # Without these the new listener had no wake word at all.
            wake_transcriber=self._transcribe_blocking,
            wake_local=self._local_wake(),
            wake_mode=cfg.wake_engine(),
        )
        try:
            self._listener.start()
        except Exception as e:
            self.sig_error.emit(f"Could not start mic: {e}")

    # ── When Windows' speaker or microphone changes ─────────────────────────
    #
    # 19 Oct 2026, the owner: "I can't hear her." Headphones had become the
    # default after HeyTora started, and she was still talking into the
    # laptop's speakers: PortAudio lists the devices once, at start, and her
    # output stays open for the whole run (HP-04). audio/endpoints.py asks
    # Windows every 2 s; on a change the audio is reopened at the next quiet
    # moment, on the new devices.
    AUDIO_QUIET_WAIT_S = 120.0

    def _watch_audio_devices(self) -> None:
        try:
            from audio.endpoints import DeviceWatch
            DeviceWatch(self._audio_devices_changed).start()
        except Exception:  # noqa: BLE001 - no watch: a restart still picks the new device
            _log.debug("audio device watch not started", exc_info=True)

    def _audio_devices_changed(self, _old, _new) -> None:
        """On the watch's thread: wait for a quiet moment, then follow."""
        deadline = time.monotonic() + self.AUDIO_QUIET_WAIT_S
        while (self._state != AppState.IDLE or self._key_down) and time.monotonic() < deadline:
            time.sleep(0.5)
        self._reopen_audio()

    def _reopen_audio(self) -> None:
        """Close the mic and her speaker, have PortAudio list the devices
        again, and open both on Windows' defaults (the chosen mic is found
        again by its name: device numbers move)."""
        import sounddevice as sd
        from audio.playback import stop_audio
        conv = self._realtime
        player = getattr(conv, "_player", None) if conv is not None else None
        try:
            self._listener.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if player is not None:
                player.stop()
            stop_audio()
        except Exception:  # noqa: BLE001
            pass
        try:
            sd._terminate()
            sd._initialize()
        except Exception:  # noqa: BLE001 - the old list stays; the reopen still helps a changed default
            _log.warning("could not refresh the sound devices", exc_info=True)
        self._listener._device = cfg.mic_device()
        try:
            self._listener.start()
        except Exception as e:  # noqa: BLE001
            self.sig_error.emit(f"Could not restart the microphone: {e}")
        if player is not None:
            try:
                player.open()
            except Exception:  # noqa: BLE001 - it opens on her next word
                pass
        speaker, mic = _sound_devices()
        _log.info("Windows' sound devices changed - speaker: %s, microphone: %s", speaker, mic)

    def set_web_search(self, enabled: bool):
        self._web_search_enabled = bool(enabled)
        cfg.set_switch("web_search", bool(enabled))

    def set_wake_word(self, enabled: bool):
        """Tray toggle — switches between always-listening and hotkey-only.

        ON keeps the mic open and runs a Whisper pass over everything it hears
        to catch "Tora". OFF (the default) leaves the mic closed until you
        press the hotkey, which is both quieter on CPU and less invasive.
        """
        cfg.set_mic_mode("ambient" if enabled else "hotkey")
        self._listener.set_wake_word_enabled(enabled)
        self._listener.set_ambient(enabled)

    def set_slow_mode(self, enabled: bool):
        self._slow_mode = bool(enabled)
        cfg.set_switch("slow_mode", bool(enabled))

    def set_guide_auto_advance(self, enabled: bool):
        """Tray: detect step completion automatically, or wait for 'next'."""
        self._guide_auto_advance = bool(enabled)
        cfg.set_switch("auto_advance", bool(enabled))
        _log.info("guide auto-advance %s", "on" if enabled else "off")

    def _make_step_watcher(self, step, next_target: str = ""):
        import dataclasses

        from tutor_features.step_watch import StepWatcher
        from ai import gemini_vision, verification

        expected = getattr(step, "expected", None)
        # The strongest signal a step can have is usually one the planner
        # never thinks to write down: the thing the NEXT step points at
        # appearing. "Open the File menu" is done the moment "Save As" is on
        # screen. It costs nothing, it is instant, and if that control is
        # already visible the arming pass mutes it — so adding it is free.
        if expected is not None and next_target and not expected.control_appears:
            expected = dataclasses.replace(expected, control_appears=next_target)
        local = None
        if expected and getattr(expected, "has_local_signal", False):
            # ArmedExpectation, not verify_local: it mutes the signals that
            # are already true when the step is spoken and keeps watching the
            # rest. A plain check returns "done" on step one of every guide
            # that names the app the user is already sitting in.
            local = verification.ArmedExpectation(expected)
        target = (getattr(step, "target", "") or "").strip()
        # A typing step also finishes on the text the user typed, read from
        # the field - not from a screenshot. It sits ALONGSIDE the step's
        # other signals, not instead of them: a typing step nearly always
        # has the next step's target as a signal too, and that one is often
        # already on screen and muted. One that ends in Enter is left to what
        # Enter causes (a page loads, a search runs) - the value settling
        # first would advance the guide before they had pressed it.
        if _is_typing_step(step.instruction):
            local = verification.AnyOf([local, verification.TypedText()])
        # "Press Ctrl and T" finishes the moment the keys are pressed, not
        # after a 4 s vision check (LG-08).
        keys = verification.shortcut_in(step.instruction)
        if keys:
            local = verification.AnyOf([local, verification.KeysPressed(keys)])
        return StepWatcher(
            step.instruction,
            judge=gemini_vision.judge_step,
            dpi_scale=self._dpi_scale(),
            fallback_check=verification.TargetGone(target) if target else None,
            waiting=bool(getattr(step, "waiting", False)),
            local_check=local,
        )

    _step_jobs = None                   # (plan id, step) -> its lookup, while it runs
    _arrow_for = None                   # (plan id, step) whose arrow is up now

    async def _step_target(self, plan, idx: int, step):
        """Look a step's target up ONCE, however many ask while it runs.

        Step one is asked for when it is planned and again when it is said,
        and "where is it?" can ask a third time: each used to start its own
        UIA walk, OCR and vision call for the one arrow."""
        if self._step_jobs is None:
            self._step_jobs = {}
        key = (id(plan), idx)
        ahead = (self._step_found or {}).pop(key, None)
        if ahead is not None:
            found, at, title = ahead
            if (time.monotonic() - at <= self.AHEAD_TRUST_S
                    and title == (active_window_title() or "")):
                return found                    # already found: the arrow is instant
        job = self._step_jobs.get(key)
        if job is None or job.done():
            async def _look():
                self._activity("finding")
                try:
                    return await self._resolve_point(
                        step.target, context=getattr(step, "instruction", "") or "")
                finally:
                    self._activity("")
            job = asyncio.ensure_future(_look())
            job.add_done_callback(_retrieve)
            job.add_done_callback(lambda j, k=key: (self._step_jobs or {}).get(k) is j
                                  and self._step_jobs.pop(k, None))
            self._step_jobs[key] = job
        return await asyncio.shield(job)

    # LG-06: while a step is being done, the NEXT step's target is looked up
    # as soon as the screen settles after a change - clicking "Captions" is
    # what puts "Auto captions" on screen - so its arrow is there when its
    # words are. Free tiers only; trusted for this long, in the same window.
    AHEAD_SETTLE_S = 0.6
    AHEAD_TRUST_S = 3.0
    _step_found = None                  # (plan id, step) -> (found, when, window)
    _guide_stats = None                 # the running walkthrough's numbers (GW-03, LG-09)
    _ahead_mark = None                  # the change the last look-ahead was for

    def _look_ahead(self, watcher) -> None:
        """Called while a step is polled: look the next step's target up once
        the screen has settled after a change."""
        plan = self._lesson_plan
        changed = getattr(watcher, "changed_at", 0.0) or 0.0
        if plan is None or not changed or time.monotonic() - changed < self.AHEAD_SETTLE_S:
            return
        nxt = self._lesson_step_idx + 1
        if not (0 <= nxt < len(plan.steps)) or not getattr(plan.steps[nxt], "target", ""):
            return
        mark = (id(plan), nxt, changed)
        if self._ahead_mark == mark:
            return
        self._ahead_mark = mark
        job = asyncio.ensure_future(self._find_ahead(plan, nxt))
        job.add_done_callback(_retrieve)

    async def _find_ahead(self, plan, idx: int) -> None:
        step = plan.steps[idx]
        found = await self._resolve_point(
            step.target, context=getattr(step, "instruction", "") or "", vision=False)
        if found is None or self._lesson_plan is not plan:
            return
        found.via = "ahead " + (getattr(found, "via", "") or "local")
        if self._step_found is None:
            self._step_found = {}
        self._step_found[(id(plan), idx)] = (found, time.monotonic(),
                                             active_window_title() or "")

    def _drop_step_jobs(self, keep: int = -1) -> None:
        """Stop looking for the targets of steps that are over."""
        plan_id = id(self._lesson_plan)
        for (pid, i), job in list((self._step_jobs or {}).items()):
            if pid != plan_id or i != keep:
                job.cancel()
                self._step_jobs.pop((pid, i), None)

    async def _point_first_step(self, guide, asked_at: float) -> None:
        """Step one's arrow, the moment step one exists (GW-08).

        Step one acts on the screen as it is now, so this is the screen its
        target is on - unlike later steps, whose targets are resolved when
        they come up (see _prefetch_step). It is pointed at again when the
        step is said; that second look is ~5 ms and also arms its watcher."""
        step = guide.steps[0]
        await self._point_step_target(0, step, note=False)
        stats = self._guide_stats
        if (0 in self._step_point and self._lesson_plan is guide
                and stats is not None and stats.get("arrow_s") is None):
            stats["arrow_s"] = time.monotonic() - asked_at
            _log.info("guide step one pointed at %.1fs after it was asked for",
                      stats["arrow_s"])

    async def _point_step_target(self, idx: int, step, *, note: bool = True) -> None:
        """Point at a guide step's target and tell its watcher the bounds,
        so a click inside them counts as the step being done."""
        plan = self._lesson_plan
        started = time.monotonic()
        try:
            found = await self._step_target(plan, idx, step)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise                  # we were stopped, not just the lookup
            return                     # the step ended and its lookup went with it
        if note:
            self._note_target(idx, step.target, found, time.monotonic() - started)
        # The walkthrough may have moved on while that was being looked up.
        # Drawing it now points at the last screen's button while the user
        # reads the next step: the field log had step one's arrow landing
        # when they were on step three (APP-23).
        if plan is not self._lesson_plan or self._lesson_step_idx != idx:
            if found is not None:
                _log.info("guide step %d arrow came too late - not drawn (now on step %d)",
                          idx + 1, self._lesson_step_idx + 1)
            return
        if not self._show_guide_point(found, step.target):
            return
        self._arrow_for = (id(plan), idx)
        self._clock_step(idx, "arrow", via=getattr(found, "via", "") or "")
        self._step_point[idx] = found
        w = getattr(step, "_watcher", None)
        bbox = getattr(found, "bbox", None)
        if w is not None:
            if bbox:
                w.target_bbox = tuple(float(v) for v in bbox)
            else:
                r = 28.0
                w.target_bbox = (found.x - r, found.y - r, found.x + r, found.y + r)

    def set_quiz_mode(self, enabled: bool):
        was = self._quiz_mode
        self._quiz_mode = enabled
        if enabled and not was:
            # Kick off the first question immediately so the user doesn't
            # have to ask "begin quiz". Uses the active screen as context.
            self._submit(self._kickoff_quiz())

    async def _kickoff_quiz(self):
        """Called when quiz mode flips ON — generates the first question
        without waiting for a user utterance."""
        if self._state != AppState.IDLE:
            return
        try:
            self._emit_state(AppState.THINKING)
            screenshots = capture_all_screens()
            # The active monitor only: three images cost three times as much
            # and the question is always about the screen in front of them.
            active = self._active_shot(screenshots)
            images_b64 = [(active or screenshots[0]).base64_jpeg] if screenshots else []
            title = active_window_title()
            system = _build_system_prompt(
                window_title=title, quiz_mode=True,
            )
            ak = app_key(title)
            history = self._app_memory.setdefault(ak, [])

            full = ""
            async for chunk in self._get_llm().stream_response(
                user_text="(quiz mode just enabled — start the quiz now)",
                screenshots_b64=images_b64,
                history=history,
                system_prompt=system,
                model=self._current_model,
            ):
                if self._cancel_flag:
                    break
                full += chunk
                self.sig_response_chunk.emit(chunk)
            self.sig_response_done.emit(full)
            self._emit_state(AppState.SPEAKING)
            try:
                await self._get_tts().speak(full)
            except Exception:
                pass
        except Exception as e:
            self.sig_error.emit(f"Quiz start failed: {e}")
        finally:
            self._emit_state(AppState.IDLE)

    def set_privacy_guard(self, enabled: bool):
        self._privacy_guard = bool(enabled)
        cfg.set_switch("privacy_guard", bool(enabled))

    @property
    def slow_mode(self) -> bool:  return self._slow_mode
    @property
    def quiz_mode(self) -> bool:  return self._quiz_mode
    @property
    def privacy_guard(self) -> bool:  return self._privacy_guard

    def clear_history(self):
        self._history = []
        self._app_memory.clear()
        self._lesson_steps = []
        self._lesson_plan = None
        self._lesson_step_idx = 0
        self._lesson_nagged = False
        self._last_ref = None

    # ── Attached documents (drag-drop on panel) ──────────────────────────────

    def attach_document(self, path: str) -> bool:
        """Extract a document and index it for recall.

        Indexing is a handful of embedding calls and happens in the
        background — the toast says "attached" straight away and a big PDF
        keeps working through while the user reads it. Questions asked in
        that window simply find fewer passages, never an error.
        """
        text = pdf_context.extract_text(path)
        if not text.strip():
            return False
        name = Path(path).name
        self._attached_docs.append((name, text))
        # Cap context — most recent 3 docs
        dropped = self._attached_docs[:-3]
        self._attached_docs = self._attached_docs[-3:]
        for old_name, _ in dropped:
            gemini_memory.forget(old_name)
        self._submit(self._index_document(name, text))
        return True

    def attach_text(self, name: str, text: str) -> bool:
        """Attach text Tora produced itself, e.g. a captured workflow.

        Same path as a dropped file — it gets indexed, so "what did I just
        do?" finds it through recall rather than needing the caller to reach
        into _attached_docs and have it never be embedded.
        """
        if not text.strip():
            return False
        self._attached_docs.append((name, text))
        self._attached_docs = self._attached_docs[-3:]
        self._submit(self._index_document(name, text))
        return True

    async def _index_document(self, name: str, text: str) -> None:
        try:
            n = await gemini_memory.index_document(name, text)
            _log.info("indexed %s into %d passages", name, n)
        except Exception as e:
            self.sig_error.emit(f"Could not index {name}: {e}")

    def clear_attachments(self):
        for name, _ in self._attached_docs:
            gemini_memory.forget(name)
        self._attached_docs = []

    # ── Lesson recording ─────────────────────────────────────────────────────

    def start_recording(self) -> Optional[str]:
        if self._recorder is None:
            self._recorder = lesson_recorder.LessonRecorder()
        out = self._recorder.start()
        if out:
            self.sig_recording_state.emit(True, str(out))
            return str(out)
        return None

    def stop_recording(self) -> Optional[str]:
        if not self._recorder or not self._recorder.is_recording:
            return None
        out = self._recorder.stop()
        self.sig_recording_state.emit(False, str(out) if out else "")
        return str(out) if out else None

    @property
    def is_recording(self) -> bool:
        return bool(self._recorder and self._recorder.is_recording)

    # ── Workflow capture (record clicks/keystrokes) ──────────────────────────

    def workflow_start(self) -> bool:
        if self._workflow is None:
            self._workflow = workflow_capture.WorkflowCapture()
        return self._workflow.start()

    def workflow_stop(self) -> str:
        if not self._workflow:
            return ""
        events = self._workflow.stop()
        return self._workflow.summarise() if events else ""

    # ── Live collaboration ───────────────────────────────────────────────────

    def collab_start_host(self):
        """Live-session host. Disabled — see tutor_features/collab.py."""
        self.sig_error.emit(
            "Live Session: not available in this build. "
            "Requires a WebRTC signalling server (planned for a future release)."
        )

    def collab_join(self, code: str):
        """Live-session join. Disabled — see tutor_features/collab.py."""
        self.sig_error.emit(
            "Live Session: not available in this build. "
            "Requires a WebRTC signalling server (planned for a future release)."
        )

    # ── Voice picker ─────────────────────────────────────────────────────────

    def set_tts_voice(self, voice: str):
        """Kept as the old name; set_voice does the work for both routes."""
        self.set_voice(voice)

    # ── Toggle setters for the rest of the new features ──────────────────────

    def set_code_mode_auto(self, enabled: bool):
        self._code_mode_auto = bool(enabled)
        cfg.set_switch("code_mode", bool(enabled))

    def set_multilang(self, enabled: bool):
        # Tora is English-only: nothing reads this. The tray no longer shows
        # the switch (APP-03); the method stays for main.py's wiring.
        self._multilang = enabled

    def set_journal(self, enabled: bool):
        self._journal_enabled = bool(enabled)
        cfg.set_switch("journal", bool(enabled))

    def set_ocr_enabled(self, enabled: bool):
        self._ocr_enabled = bool(enabled)
        cfg.set_switch("ocr", bool(enabled))

    # ── Stop / cancel ─────────────────────────────────────────────────────────

    def stop(self):
        """Cancel the current LLM stream + any in-flight TTS. Bound to Esc."""
        self._cancel_flag = True
        # Kill audio playback immediately — flips the global stop event so
        # the chunked PortAudio loop bails out within ~50 ms.
        try:
            from audio.playback import stop_audio
            stop_audio()
        except Exception:
            pass
        # Some TTS providers also have their own cancel hook
        tts = self._tts
        if tts and hasattr(tts, "stop"):
            try:
                tts.stop()
            except Exception:
                pass
        # End a running guide, and clear any stored lesson, so "stop" (or Esc)
        # really means "back to zero".
        self._stop_guide()
        if self._realtime is not None:
            self._realtime.cancel()
        self._lesson_steps = []
        self._lesson_step_idx = 0
        self._lesson_nagged = False
        self._emit_state(AppState.IDLE)
