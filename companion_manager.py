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
import logging
import math
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
from screen.capture import capture_all_screens
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
    base = cfg.custom_instructions.strip()
    base = base.replace("{{CONTEXT}}", chr(10).join(ctx_lines))
    base = base.replace("{{TODAY}}", today)
    # The realtime model calls tools; the cascade writes tags into its text.
    # Giving either one the other's rules produces an answer full of literal
    # [ARROW:...] read aloud, or a tool the model was never told it has.
    rules = _REALTIME_RULES if realtime else _TECHNICAL_RULES
    return base + rules + _code_addendum(code_active) + extra


def _code_addendum(active: bool) -> str:
    if not active:
        return ""
    from tutor_features.code_mode import code_system_prompt_addendum
    return code_system_prompt_addendum()


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


POINT_RE = re.compile(r'\[POINT:(\d+),(\d+):([^:\]]+):screen(\d+)\]')
# A partial "[POINT..." prefix that hasn't closed yet — hold it back from display
# until the next chunk so we never leak a half tag.
POINT_PARTIAL_RE = re.compile(r'\[(?:P|PO|POI|POIN|POINT|POINT:[^\]]*)?$')

# ── Teaching / drawing tags ──────────────────────────────────────────────────
# ALL coordinates are normalized 0-1000 relative to the screenshot the model
# saw (x: 0=left edge, 1000=right edge; y: 0=top, 1000=bottom). The manager
# converts to logical screen pixels via _denorm(). Trailing :color is optional
# on every shape.
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
    sig_open_extension = pyqtSignal(str, str)   # (extension id, "" | "dashboard" | "wizard:<template>")
    sig_agents_ready        = pyqtSignal()                # workforce built (or failed); wire the UI now
    sig_account_problem     = pyqtSignal(str, str)        # (kind, plain words); ("", "") = over

    def __init__(self):
        super().__init__()
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
            device=cfg.mic_device_index,
            ambient=cfg.ambient_mic(),
            wake_transcriber=self._transcribe_blocking,
        )

        # Autonomous agent workforce (agents/). Built in start(), never here:
        # a broken agent package must not stop the companion from running.
        self._agents = None

        # Background asyncio loop
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        try:
            self._listener.start()
        except Exception as e:
            self.sig_error.emit(f"Mic error: {e}")
        self._start_agents()
        # Out of credit, or a refused key, from any request anywhere: shown,
        # because it cannot be spoken - the voice uses the same account.
        gemini.on_problem(self._account_problem)
        # Sleep/wake watchdog — restarts mic + loop after system resume
        self._start_sleep_watchdog()
        # Open everything now, while the user is still reading the startup
        # toast: the HTTP pool, and the live conversation itself. The first
        # question then costs exactly what the tenth does.
        self._submit(self._warm_up())
        self._submit(self._realtime_connect())
        # Refresh a stale model cache in the background (weekly at most).
        self._submit(self._refresh_stale_models())

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

    def _transcribe_blocking(self, pcm: bytes) -> str:
        """Wake-word transcription, called from the listener's worker thread.

        Bridges onto the manager's loop so the shared HTTP client and its
        warm connection are reused rather than a fresh one per check. Bounded
        so a slow network cannot pile up wake-word threads.
        """
        if not self._loop or not self._loop.is_running():
            return ""
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._get_stt().transcribe(pcm), self._loop
            )
            return fut.result(timeout=12) or ""
        except Exception:
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
        reg = getattr(mgr, "extensions", None)
        if reg is not None:
            try:
                parts.extend(reg.voice_hints(include_off=False))
            except Exception:  # noqa: BLE001
                pass
        if not parts:
            return ""
        return (" ".join(parts) + ". If the user asks how it is going, answer from this; "
                "to pause, resume, cancel or approve, call manage_agents.")

    @property
    def agents(self):
        """The AgentManager, or None when the agent system is unavailable."""
        return self._agents

    def shutdown(self):
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
                    # System was sleeping — restart subsystems
                    self._on_system_resume()

        t = threading.Thread(target=_watch, daemon=True)
        t.start()

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
            try:
                f.result()
            except Exception as e:
                # A swallowed exception here used to leave the UI stuck on
                # "Listening..." forever (GitHub issue #6). Surface it and
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
               "key": "Google refused Tora's voice: check the API key in Setup"}.get(
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

    async def _realtime_connect(self) -> bool:
        """Open the conversation before anyone asks anything.

        Called at startup and after a drop. The handshake and setup exchange
        are the better part of a second; paying that here means the first
        question does not.
        """
        if not cfg.realtime() or not cfg.has_key():
            return False
        try:
            return await self._get_realtime().connect(
                self._realtime_system_prompt()
            )
        except Exception as e:
            _log.info("realtime unavailable (%s) — cascade will be used", e)
            return False

    def _on_realtime_heard(self, text: str) -> None:
        if not self._heard_buf:
            self._rearm_desktop_control()
        self._heard_buf.append(text)

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

    def _on_realtime_speaking(self) -> None:
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
        needs_point = is_locate(heard) and not self._pointed_this_turn
        if needs_point:
            self._submit(self._ensure_pointed(heard))
        elif self._pointing_held:
            self.sig_point_release.emit()
            self._pointing_held = False

        if said:
            self.sig_response_done.emit(said)
            self._last_response = said
            self._record_turn(heard, said)
        self._heard_buf = []
        self._emit_state(AppState.IDLE)

    def _on_realtime_lost(self, reason: str) -> None:
        """The session dropped. Reconnect quietly, in the background."""
        _log.info("realtime session lost: %s", reason)
        if self._state != AppState.IDLE:
            self._emit_state(AppState.IDLE)
        self._submit(self._realtime_reconnect())

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
    }
    _PROBLEM_RENOTIFY_S = 600.0
    _problem_kind = ""
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
        told = self._problem_told
        if told is None:
            told = self._problem_told = {}
        now = time.monotonic()
        if now - told.get(kind, -1e9) < self._PROBLEM_RENOTIFY_S:
            return
        told[kind] = now
        _log.warning("account problem (%s): %s", kind, (detail or "")[:200])
        self.sig_account_problem.emit(kind, self._PROBLEM_WORDS.get(kind, detail))

    def _show_failure(self, error) -> bool:
        """Put a failed request's REASON on screen, in words a person can act
        on. True when it was one of the known kinds; the caller shows the
        raw error otherwise."""
        kind = gemini.problem_of(error)
        words = self._PROBLEM_WORDS.get(kind)
        if not words:
            return False
        self.sig_response_chunk.emit(words)
        self.sig_response_done.emit(words)
        return True

    def _feed_realtime(self, block: bytes) -> None:
        """Audio-thread callback. Must be cheap and must never raise.

        Two jobs: park the bytes for the pump, and flush playback the instant
        the user talks over the answer. The barge-in is done here rather than
        on the loop because here is where the audio actually is — it is a
        buffer clear, not a network call.
        """
        self._rt_audio.append(block)
        if self._last_rms > self._SPEECH_RMS:
            conv = self._realtime
            if conv is not None and conv.speaking:
                conv.barge_in()

    async def _realtime_stream(self) -> None:
        """Forward mic audio for one question, then stop.

        The screen summary goes first, while the user is still drawing
        breath. It is one line of text — no screenshot, no upload — so it
        costs nothing on the way to the answer.
        """
        conv = self._realtime
        if conv is None:
            return
        if not conv.ready:
            deadline = time.monotonic() + 3.0
            while (not conv.ready and self._rt_streaming
                   and time.monotonic() < deadline):
                await asyncio.sleep(0.05)
            if not conv.ready:
                _log.info("live session not ready in time — cascade takes this one")
                self._rt_streaming = False
                return
        try:
            await conv.send_context(await self._screen_context())
        except Exception:
            _log.debug("screen context failed", exc_info=True)

        while self._rt_streaming:
            if self._rt_audio:
                chunk = b"".join(self._rt_audio)
                self._rt_audio.clear()
                await conv.push_audio(chunk)
            else:
                await asyncio.sleep(0.02)
        if self._rt_audio:
            chunk = b"".join(self._rt_audio)
            self._rt_audio.clear()
            await conv.push_audio(chunk)

        # Let the server hear the question end. See end_user_audio — this is
        # what makes the first word arrive immediately rather than a second
        # and a half later.
        await conv.end_user_audio()

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
                f"Everything else they say mid-walkthrough is a question or a "
                f"remark - answer it and leave the guide alone; it keeps "
                f"running underneath. 'Okay' and 'got it' are not commands: it "
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
        elif self._lesson_steps:
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
        if getattr(req, "sensitive", False):
            return (f'WAITING ON YOU: "{req.reason}". This one is sensitive - '
                    f'it can ONLY be approved by typing {req.confirm_word} in the '
                    f'box on screen. If they say yes, tell them that; never '
                    f'approve it for them.')
        return (f'WAITING ON YOU: "{req.reason}". If they say yes, go on, do it, '
                f'sure, please do - call manage_agents with action "approve". '
                f'If they say no, stop, cancel, leave it - call manage_agents '
                f'with action "deny". Do this INSTEAD of answering; they are '
                f'replying to the box, not asking something new.')

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
            "desktop_action":  self._tool_desktop_action,
            "manage_agents":   self._tool_manage_agents,
        }

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
        title = str(args.get("title") or goal[:60]).strip()
        try:
            kind = TaskKind(str(args.get("kind") or "coding").lower())
        except ValueError:
            kind = TaskKind.CODING
        meta = {"request": goal, "reply": f"Understood. Starting on {title} now."}
        hint = str(args.get("workspace_hint") or "").strip().strip("'\"")
        if hint:
            meta["subdir"] = hint
        task = Task(title=title, goal=goal, workspace=self._agents.workspace_root,
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

    @staticmethod
    def _desktop_result(out) -> dict:
        return {"ok": out.ok, "action": out.action, "app": out.app,
                "how": out.tier.label if out.tier else "", "result": out.message,
                "note": ("Read the result back in a few words." if out.ok
                         else "Tell the user what stopped it; do not pretend it worked.")}

    async def _after_approval(self, future, found, arguments: dict) -> None:
        """Wait for the dialog, run the action, and say how it went.

        This runs with nothing blocked on it. The conversation carried on
        while the box was open, so the outcome has to be volunteered rather
        than returned - see RealtimeConversation.say_now.
        """
        from agents.control import control
        try:
            approved = await asyncio.wrap_future(future)
        except Exception:  # noqa: BLE001 - a broken dialog is a refusal
            _log.debug("approval future failed", exc_info=True)
            approved = False

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

        if action in ("list_extensions", "enable_extension", "disable_extension", "open_dashboard",
                      "create_agent"):
            return self._manage_extensions(mgr, action, args)

        candidates = [t for t in top if not t.status.is_terminal]
        if wanted:
            candidates = [t for t in candidates if wanted in t.title.lower()] or candidates
        if not candidates:
            return {"error": "no running task" + (f" matching {wanted!r}" if wanted else "")}
        target = candidates[0]
        done = {"pause": mgr.pause, "resume": mgr.resume, "cancel": mgr.cancel}.get(action)
        if done is None:
            return {"error": "action must be status, pause, resume, cancel, approve, deny, list_extensions, "
                             "enable_extension, disable_extension, open_dashboard or create_agent"}
        ok = done(target.id)
        return {"ok": bool(ok), "action": action, "task": target.title,
                "status": (mgr.get_task(target.id) or target).status.value}

    def _manage_extensions(self, mgr, action: str, args: dict) -> dict:
        """Industry packs by voice: "turn on the accounting tools", "open the
        accounting dashboard", "create an accounts payable agent"."""
        reg = getattr(mgr, "extensions", None)
        if reg is None:
            return {"error": "extensions are not available in this build"}
        ext_id = str(args.get("id") or args.get("extension") or "").strip().lower()
        if action == "list_extensions":
            return {"ok": True, "extensions": [
                {"id": e["id"], "name": e["name"], "enabled": e["enabled"], "adds": e.get("offer", [])}
                for e in reg.available()]}
        if not ext_id:
            on = [e["id"] for e in reg.available() if e["enabled"]]
            ext_id = on[0] if len(on) == 1 and action != "enable_extension" else ""
        if not ext_id:
            return {"error": "say which extension (id), e.g. 'accounting'"}
        try:
            if action == "enable_extension":
                ext = reg.enable(ext_id)
                self.sig_open_extension.emit(ext_id, "")
                return {"ok": True, "enabled": ext.manifest.name, "adds": ext.manifest.offer,
                        "agents": [t.name for t in ext.get_agent_templates()],
                        "note": ("Tell the user it is on and what it adds, in one breath. The dashboard is "
                                 "opening. Suggest trying the demo client, or 'prepare month-end for <client>'.")}
            if action == "disable_extension":
                reg.disable(ext_id)
                return {"ok": True, "disabled": ext_id, "note": "Their data is kept."}
            if action == "open_dashboard":
                self.sig_open_extension.emit(ext_id, "dashboard")
                return {"ok": True, "note": "The dashboard is opening on screen."}
            if action == "create_agent":
                template = str(args.get("template") or "").strip()
                self.sig_open_extension.emit(ext_id, f"wizard:{template}")
                return {"ok": True, "note": ("The setup wizard is on screen: accounting system, inbox, "
                                             "permission level, approval threshold and schedule. Walk the "
                                             "user through it briefly.")}
        except KeyError as exc:
            return {"error": str(exc.args[0]) if exc.args else "unknown extension"}
        return {"error": "unknown extension action"}

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

    async def _capture_shots(self, *, max_width: int = 1152, reuse: bool = True):
        """Capture the screen, reusing a very recent grab when allowed.

        max_width is the width of the JPEG sent to the model — smaller uploads
        and infers faster. The fast glance uses a lighter image than the
        careful look, which wants every pixel it can get for reading.
        """
        if (reuse and self._screens_ctx
                and (time.monotonic() - self._screens_at) < self._SHOT_CACHE_S):
            return self._screens_ctx
        screens = await asyncio.to_thread(capture_all_screens, max_width)
        if screens:
            self._screens_ctx = screens
            self._screens_at = time.monotonic()
        return screens

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
            find_target, query, screenshot=shot, context=context))
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
    _uia_counts = None                  # app key -> controls its tree named

    def _note_uia(self, title: str, count: int) -> None:
        if self._uia_counts is None:
            self._uia_counts = {}
        self._uia_counts[app_key(title or "")] = int(count)

    def _uia_poor(self) -> bool:
        """Does the app in front expose (next to) no controls at all?"""
        n = (self._uia_counts or {}).get(app_key(active_window_title() or ""))
        return n is not None and n <= self.UIA_POOR_AT

    def _local_hit(self, task, shot, query: str):
        """A finished local lookup as logical pixels, or None."""
        from types import SimpleNamespace
        try:
            target = task.result()
        except BaseException:  # noqa: BLE001 - a failed local tier is a miss
            return None
        if target is None:
            return None
        scale = (shot.dpi_scale if shot is not None else None) or self._dpi_scale()
        bbox = None
        if getattr(target, "bbox", None):
            l, t, r, b = target.bbox
            bbox = (l / scale, t / scale, r / scale, b / scale)
        return SimpleNamespace(
            x=target.x / scale, y=target.y / scale,
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
                screens = await self._capture_shots(max_width=1024)
                if not screens:
                    return None
                shot = self._active_shot(screens) or screens[0]
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
        scale = self._dpi_scale()
        return SimpleNamespace(x=cx / scale, y=cy / scale, label=name)

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
                    "kind": "rect", "color": "blue",
                    "x1": l - pad, "y1": t - pad, "x2": r + pad, "y2": b + pad,
                })
            else:
                self.sig_draw.emit({
                    "kind": "circle", "color": "blue",
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
            scale = self._dpi_scale()
            from ai.hybrid_pointer import nearest_in_direction
            # A stale snapshot is no snapshot: nearest_in_direction then walks
            # the screen as it is now (APP-04).
            snapshot = (self._pointables or None) if self._snapshot_fresh() else None
            nb = await asyncio.to_thread(
                lambda: nearest_in_direction(
                    anchor.x * scale, anchor.y * scale, direction,
                    candidates=snapshot,
                )
            )
            if nb is None:
                return None
            return SimpleNamespace(x=nb.x / scale, y=nb.y / scale,
                                   label=nb.label or "there")

        if is_backref(query) and fresh:
            return SimpleNamespace(x=ref.x, y=ref.y, label=ref.label or "there")

        return None

    async def _ensure_pointed(self, transcript: str) -> None:
        """Safety net: a locate question must end with an arrow, not words.

        The live model is told to point, but it does not always — sometimes it
        just describes the location, sometimes it calls point_at with a name
        the tree does not have. This runs after any locate turn that produced
        no arrow, resolves the target itself through the full chain (which
        ends in the vision model, so it works even when there is no name), and
        points. It is what turns "once in a while" into "almost always".
        """
        label = _guess_label(transcript)
        if label in ("right here!", "here"):
            label = transcript                # no clean target — let vision read it
        if await self._point_to(label, hold=True):
            await asyncio.sleep(4.0)          # dwell while the user looks
            if self._pointing_held:
                self.sig_point_release.emit()
                self._pointing_held = False

    async def _tool_look_at_screen(self, args: dict) -> dict:
        """The expensive one: capture the screen and actually look at it."""
        question = str(args.get("question") or "").strip()
        if not question:
            return {"error": "no question given"}

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
        screens = await self._capture_shots(
            max_width=1280 if careful else 1024, reuse=not careful,
        )
        if not screens:
            return {"error": "could not capture the screen"}
        self.sig_clear_drawings.emit()

        shot = self._active_shot(screens) or screens[0]
        look = await gemini_vision.look(
            question, shot.base64_jpeg,
            deep=careful or None,
        )
        if not look:
            return {"error": "could not read the screen clearly"}

        # Ring what the answer rests on, so the user can see the working.
        drawn = 0
        for action in look.actions[:4]:
            sh = self._shape_from_tag(action.as_tag())
            if sh:
                self.sig_draw.emit(sh)
                drawn += 1
        if drawn:
            self._pointed_this_turn = True

        return {
            "answer": look.answer,
            "observations": look.observations[:8],
            "highlighted_on_screen": [a.label for a in look.actions[:4]],
        }

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
            guide = await gemini_planner.plan(
                goal, screenshot_b64=shot, window_title=title,
                controls=controls, fast=False,
            )
        return guide

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
        screens = await self._capture_shots(max_width=1024)
        if not screens:
            return ""
        return (self._active_shot(screens) or screens[0]).base64_jpeg

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
        try:
            results = await search(query)
        except Exception as e:
            return {"error": str(e)}
        return {"results": (results or "")[:4000] or "nothing useful found"}

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
        self._begin_capture()
        # Arm the silence watchdog straight away. If this turns out to be a
        # hold rather than a tap, on_hotkey_release claims the end first.
        self._submit(self._auto_stop_on_silence())

    def on_hotkey_release(self):
        if self._state != AppState.LISTENING:
            return
        held = time.monotonic() - self._hotkey_pressed_at
        if held < cfg.tap_max_seconds:
            # A tap, not a hold: keep listening and let the user finish
            # speaking. The watchdog ends the capture on silence.
            return
        if self._claim_end():
            self._submit(self._end_capture_and_process())

    def _handle_wake(self):
        """Triggered from ambient listener when wake-word is detected."""
        if not self._claim_begin():
            return
        self._begin_capture()
        self._submit(self._auto_stop_on_silence())

    def _claim_begin(self) -> bool:
        """Exactly one of {hotkey press, wake word} may open a capture.

        Sets LISTENING inside the lock so a second caller on another thread
        cannot also observe IDLE and start a parallel capture.
        """
        with self._begin_lock:
            if self._state != AppState.IDLE:
                return False
            self._state = AppState.LISTENING
            return True

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
        if self._realtime is not None:
            self._realtime.cancel()
        self._rt_audio.clear()
        self._heard_buf = []
        self._pointed_this_turn = False
        # A new thing the user is saying to Tora. Anything budgeted "per
        # request" - a guide move, for one - is measured against this.
        self._request_serial += 1
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
        self._emit_state(AppState.LISTENING)

        # The session is already open, so there is nothing to wait for —
        # audio starts flowing to the model on the next 30 ms block.
        conv = self._realtime
        ready = bool(conv is not None and conv.ready)
        # Hotkey pressed while the socket is still opening (the first
        # seconds after launch): the mic is already buffering, so give the
        # handshake a moment in _realtime_stream rather than sending this
        # question down the 25-second cascade.
        connecting = bool(conv is not None and not ready and conv.connecting)
        self._rt_streaming = ready or connecting
        _log.info("turn: %s", "realtime" if ready
                  else "realtime (waiting for session)" if connecting
                  else "cascade (no live session)")
        if self._rt_streaming:
            self._submit(self._realtime_stream())
        else:
            # No live session: the cascade handles this one, and a reconnect
            # is started so the next question is fast again.
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
            elif speech_started:
                if silence_from is None:
                    silence_from = now
                elif now - silence_from >= self._END_SILENCE_S:
                    break

            if not speech_started and now - start_t > self._NO_SPEECH_S:
                break
            if now - start_t > self._MAX_UTTERANCE_S:
                break

        if self._claim_end():
            await self._end_capture_and_process()

    async def _end_capture_and_process(self):
        """The hotkey came up. Answer through whichever route is live."""
        was_realtime, self._rt_streaming = self._rt_streaming, False
        conv = self._realtime
        if was_realtime and (conv is None or not conv.ready):
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
                self._emit_state(AppState.IDLE)
                return
            # Nothing further to do. The server heard the question as it was
            # spoken, decided for itself that it finished, and the answer is
            # already on its way in through the pump — which will move the
            # state on to SPEAKING and then back to IDLE.
            if self._state == AppState.LISTENING:
                self._emit_state(AppState.THINKING)
            self._activity("understanding")
            self._turn_seq += 1
            self._submit(self._realtime_turn_watchdog(self._turn_seq))
            return

        if len(pcm) < 3200:
            self._emit_state(AppState.IDLE)
            return
        await self._cascade_turn(pcm)

    # A turn that gets no answer at all must not strand the UI on "Thinking…".
    _TURN_WATCHDOG_S = 22.0
    # ...unless a tool is running for the model. Then the model is not
    # silent, it is waiting on us, and killing the turn (and the session
    # with it) throws away work that was about to land. The log showed a
    # 40 s deep look being cut off at 22 s, the session dropped, and the
    # reconnect tripping a GeneratorExit — three failures from one slow tool.
    _TURN_WATCHDOG_MAX_S = 75.0

    async def _realtime_turn_watchdog(self, seq: int) -> None:
        """Guarantee a turn ends, even if the model never replies to it.

        The live session can be perfectly healthy and still produce nothing
        for a turn — no audio, no transcript, no turn_complete. The pump only
        reacts to events that arrive, and the socket never drops, so neither
        _on_realtime_turn_end nor _on_realtime_lost fires and the app sits on
        "Thinking…" forever. This is the backstop: if the same turn is still
        pending when it expires, say so and go back to idle.
        """
        started = time.monotonic()
        # Poll on a short interval rather than one long sleep, so a normal
        # answer (state leaves THINKING) is noticed within ~0.3 s. The turn
        # is declared dead only after WATCHDOG_S of genuine quiet — no tool
        # running and none finished in the last 6 s — capped at MAX_S total.
        while True:
            await asyncio.sleep(0.3)
            if seq != self._turn_seq:
                return                  # a newer turn superseded this one
            if self._state != AppState.THINKING:
                return                  # it answered, or moved on
            now = time.monotonic()
            elapsed = now - started
            busy = self._tools_in_flight > 0 or now - self._tool_done_at < 6.0
            if elapsed >= self._TURN_WATCHDOG_MAX_S:
                break                   # hard ceiling, tool or no tool
            if not busy and elapsed >= self._TURN_WATCHDOG_S:
                break                   # quiet long enough — really stuck
        _log.warning("realtime turn produced no response in %.0fs",
                     time.monotonic() - started)
        self._activity("")
        self._emit_state(AppState.IDLE)
        # The session is suspect — retire it so the next question gets a
        # fresh one rather than hitting the same silence again.
        self._drop_realtime()
        await self._reply_local(
            "Sorry — I didn't catch that. Could you say it again?"
        )

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
            try:
                journal.log_qa(
                    question=question, answer=answer,
                    app_key=ak, window_title=title,
                    provider="gemini", model=Models.REALTIME,
                )
            except Exception:
                pass

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
                screenshots = await self._capture_shots(max_width=1280, reuse=False)
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

            # 4. Build system prompt with all context
            system = _build_system_prompt(
                window_title=title,
                lesson_step=self._lesson_step_idx,
                total_steps=len(self._lesson_steps),
                quiz_mode=self._quiz_mode,
                detected_coord=detected_coord,
                code_active=code_active,
                language_code=lang_code,
                extra=ocr_extra + doc_extra + fig_extra,
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
            self._emit_state(AppState.IDLE)

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
        screens = await self._capture_shots(max_width=1024)
        if not screens:
            return getattr(step, "_visual_verdict", False)
        active = self._active_shot(screens) or screens[0]
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

    def _shot(self, screen_idx: int = 1):
        # Only the active monitor is sent to the model now, so the normalized
        # coordinates it returns are relative to that image regardless of the
        # "screenN" it names — always map them back through the active shot.
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
            import mss
            from screen.capture import _query_dpi_scale
            from tutor import foreground_window_center
            dpi = _query_dpi_scale() or 1.0
            centre = foreground_window_center()
            with mss.mss() as sct:
                monitors = sct.monitors[1:]
                if not monitors:
                    return None
                pick = monitors[0]
                if centre is not None:
                    cx, cy = centre
                    for m in monitors:
                        if (m["left"] <= cx < m["left"] + m["width"]
                                and m["top"] <= cy < m["top"] + m["height"]):
                            pick = m
                            break
            return (pick["left"] / dpi, pick["top"] / dpi,
                    pick["width"] / dpi, pick["height"] / dpi)
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
            shot = self._shot(1)
            scale = (shot.dpi_scale if shot else 1.0) or 1.0
            l, tp, r, b = t.bbox
            return (l / scale, tp / scale, r / scale, b / scale)
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
                    "color": color or "blue"}
        m = UNDERLINE_AT_RE.fullmatch(tag)
        if m:
            name, color = m.groups()
            bbox = self._resolve_anchor(name.strip())
            if not bbox:
                return None
            l, t, r, b = bbox
            return {"kind": "underline", "x": l, "y": b + 3, "w": r - l,
                    "color": color or "blue"}
        m = LINE_RE.fullmatch(tag) or ARROW_RE.fullmatch(tag)
        if m:
            kind = "line" if tag.startswith("[LINE") else "arrow"
            x1, y1, x2, y2, color = m.groups()
            n1 = self._snap_pt(float(x1), float(y1))
            n2 = self._snap_pt(float(x2), float(y2))
            return {"kind": kind, "pts": [self._denorm(*n1), self._denorm(*n2)],
                    "color": color or "blue"}
        m = CIRCLE_RE.fullmatch(tag)
        if m:
            x, y, r, label, color = m.groups()
            cx, cy = self._denorm(float(x), float(y))
            return {"kind": "circle", "x": cx, "y": cy,
                    "r": max(12.0, self._denorm_len(float(r))),
                    "label": (label or "").strip(), "color": color or "blue"}
        m = RECT_RE.fullmatch(tag)
        if m:
            x1, y1, x2, y2, color = m.groups()
            p1 = self._denorm(*self._snap_pt(float(x1), float(y1)))
            p2 = self._denorm(*self._snap_pt(float(x2), float(y2)))
            return {"kind": "rect", "x1": p1[0], "y1": p1[1],
                    "x2": p2[0], "y2": p2[1], "color": color or "blue"}
        m = POLY_RE.fullmatch(tag)
        if m:
            pts_str, color = m.groups()
            pts = [self._denorm(*self._snap_pt(float(a), float(b)))
                   for a, b in re.findall(r'(\d+),(\d+)', pts_str)]
            if len(pts) < 3:
                return None
            return {"kind": "poly", "pts": pts, "color": color or "blue"}
        m = TEXT_RE.fullmatch(tag)
        if m:
            x, y, content, color, size = m.groups()
            lx, ly = self._denorm(float(x), float(y))
            return {"kind": "text", "x": lx, "y": ly, "text": content.strip(),
                    "color": color or "blue", "size": size or "m"}
        m = ANGLE_RE.fullmatch(tag)
        if m:
            x, y, s, rot, color = m.groups()
            nx, ny = self._snap_pt(float(x), float(y))
            auto_rot = self._angle_rot_for_vertex(nx, ny)
            lx, ly = self._denorm(nx, ny)
            return {"kind": "angle", "x": lx, "y": ly,
                    "s": max(10.0, self._denorm_len(float(s))),
                    "rot": auto_rot if auto_rot is not None else float(rot or 0),
                    "color": color or "blue"}
        m = UNDERLINE_RE.fullmatch(tag)
        if m:
            x, y, w, color = m.groups()
            lx, ly = self._denorm(float(x), float(y))
            return {"kind": "underline", "x": lx, "y": ly,
                    "w": max(8.0, self._denorm_len(float(w))),
                    "color": color or "blue"}
        m = LABEL_RE.fullmatch(tag)
        if m:
            x, y, txt, color = m.groups()
            lx, ly = self._denorm(float(x), float(y))
            return {"kind": "text", "x": lx, "y": ly, "text": txt.strip(),
                    "color": color or "blue", "size": "s"}
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
        self._state = state
        # Wake-word scanning only means anything while Tora is idle: a wake
        # word during THINKING or SPEAKING is refused by _claim_begin. Left
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
        cfg.mic_device_index = device_index if device_index >= 0 else None
        try:
            self._listener.stop()
        except Exception:
            pass
        from audio.ambient_listener import AmbientListener
        self._listener = AmbientListener(
            on_level=self._handle_level,
            on_wake=self._handle_wake,
            device=cfg.mic_device_index,
            ambient=cfg.ambient_mic(),
        )
        try:
            self._listener.start()
        except Exception as e:
            self.sig_error.emit(f"Could not start mic: {e}")

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
