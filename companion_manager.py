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


class CompanionManager(QObject):
    """Thread-safe signals for Qt UI updates from async/audio threads."""

    sig_state_changed       = pyqtSignal(object)          # AppState
    sig_response_chunk      = pyqtSignal(str)
    sig_response_done       = pyqtSignal(str)
    sig_audio_level         = pyqtSignal(float)
    sig_point_at            = pyqtSignal(float, float, str)
    sig_point_hold          = pyqtSignal(bool)            # True → dwell forever until release
    sig_point_release       = pyqtSignal()                # end dwell + fly buddy back
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

    def __init__(self):
        super().__init__()
        self._state: AppState = AppState.IDLE
        self._history: List[Message] = []
        self._current_model: Optional[str] = None
        self._web_search_enabled = True
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
        # Prefetched step speech, per step index, filled while the previous
        # step is being performed so presenting is instant. Pointer
        # coordinates are deliberately NOT prefetched (see _prefetch_step);
        # _step_point remains only so the clearing paths stay uniform.
        self._step_audio: dict = {}
        self._step_point: dict = {}
        # Tray switch. Off = steps only advance on 'next'. The detector is
        # new (tutor_features/step_watch.py); this is the escape hatch.
        self._guide_auto_advance: bool = True
        self._last_found = None        # what _point_to last resolved
        # True while the overlay has been told to hold its pointer dwell.
        self._pointing_held = False
        # Whether an arrow has landed this turn — drives the guaranteed-
        # pointing safety net, so a locate question never ends in words alone.
        self._pointed_this_turn = False
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
        # Toggles
        self._slow_mode = False
        self._quiz_mode = False
        self._privacy_guard = True
        self._code_mode_auto = True       # auto-detect IDE windows
        self._multilang = False            # English-only; kept for the tray toggle
        self._journal_enabled = True       # log every Q&A to SQLite
        self._ocr_enabled = True           # use Tesseract for fine print
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
        return self._tts

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
        # its left") reuses this turn's walk instead of walking again.
        self._pointables = summary.pointables
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
                cur_line = f' The current step is: "{s.instruction}"'
                if s.target:
                    cur_line += (
                        f' — if the user asks where to click, call point_at '
                        f'with "{s.target}".'
                    )
            bits.append(
                "A guided walkthrough is running"
                + (f' toward: "{plan.goal}"' if getattr(plan, "goal", "") else "")
                + f" — step {cur + 1} of {len(self._lesson_steps)}, "
                f"{max(0, len(self._lesson_steps) - cur - 1)} left. Tora "
                f"speaks and points at each step and advances on its own as "
                f"the user completes it — do NOT read the steps and do NOT say "
                f"'say next'. If they ask what they're doing or what's coming, "
                f"answer from this context rather than starting over. If they "
                f"say next / back / repeat / stop, call guide_step; otherwise "
                f"just answer and the guide keeps running underneath.{cur_line}"
            )
        elif self._resume:
            bits.append(
                "A guide was paused part-way. If the user says 'continue' or "
                "'resume the guide', call guide_step with action 'resume'."
            )
        elif self._lesson_steps:
            bits.append(
                f"A guide is in progress: step {self._lesson_step_idx + 1} of "
                f"{len(self._lesson_steps)}. If they say 'next', teach the "
                f"following step only."
            )
        if self._attached_docs:
            names = ", ".join(n for n, _ in self._attached_docs)
            bits.append(f"Documents they have attached: {names}. Use recall.")
        agents_line = self.agent_context_line()
        if agents_line:
            bits.append(agents_line)
        return " ".join(bits)

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
        the user can wave through that one action for good.
        """
        import json as _json
        from agents.control import control

        app = str(args.get("app") or "").strip()
        action = str(args.get("action") or "").strip()
        arguments = args.get("arguments")
        if not isinstance(arguments, dict):
            raw = args.get("arguments_json") or args.get("value") or ""
            arguments = {}
            if isinstance(raw, str) and raw.strip():
                try:
                    parsed = _json.loads(raw)
                    arguments = parsed if isinstance(parsed, dict) else {}
                except ValueError:
                    arguments = {}
        c = control()
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
            return {"error": c.suggestions(app, action)}
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
                                              grant_key=f"app_action:{found.key}")
            try:
                approved = await asyncio.wrap_future(future)
            except Exception:  # noqa: BLE001
                approved = False
            if not approved:
                return {"ok": False, "action": found.key,
                        "note": "The user did not approve it. Say so plainly and do not try another way."}
        out = await c.run(app, action, arguments)
        return {"ok": out.ok, "action": out.action, "app": out.app,
                "how": out.tier.label if out.tier else "", "result": out.message,
                "note": ("Read the result back in a few words." if out.ok
                         else "Tell the user what stopped it; do not pretend it worked.")}

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

    async def _tool_guide_step(self, args: dict) -> dict:
        action = str(args.get("action") or "").strip().lower()
        if action == "resume":
            if self.resume_guide():
                return {"ok": True, "action": "resume"}
            return {"error": "there is no paused guide to resume"}
        runner = self._guide_runner
        if runner is None or not runner.running:
            return {"error": "no guide is running"}
        if action not in ("next", "back", "repeat", "stop"):
            return {"error": "action must be next, back, repeat, stop or resume"}
        accepted = runner.request(action)
        # Say where the walkthrough now is. Returning a bare "ok" told the
        # model nothing, so when it was unsure it called again — three times
        # in 1.4s in the field log, which walked the user off the end of a
        # two-step guide.
        out = {"ok": True, "action": action,
               "step": runner.index + 1, "of": runner.total}
        if not accepted:
            out["note"] = ("Already done a moment ago — the guide has moved "
                           "once, not again. Do not call this again.")
        return out

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

    async def _resolve_point(self, query: str, *, shot=None, use_reference: bool = True):
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
            return snap

        # 1 + 2 — local and fast.
        try:
            from ai.hybrid_pointer import find_target
            target = await asyncio.to_thread(find_target, query, screenshot=shot)
        except Exception:
            target = None
        if target is not None:
            scale = (shot.dpi_scale if shot is not None else None) or self._dpi_scale()
            bbox = None
            if getattr(target, "bbox", None):
                l, t, r, b = target.bbox
                bbox = (l / scale, t / scale, r / scale, b / scale)
            return SimpleNamespace(
                x=target.x / scale, y=target.y / scale,
                label=(target.label or query), bbox=bbox,
            )

        # 3 — the vision fallback. It sends a screenshot, so the privacy guard
        # applies here (unlike the local tiers above, which never leave the
        # machine).
        if shot is None:
            if self._privacy_guard and is_sensitive_window(active_window_title()):
                return None
            screens = await self._capture_shots(max_width=1024)
            if not screens:
                return None
            shot = self._active_shot(screens) or screens[0]

        found = await gemini_vision.locate(
            query,
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
        if found is None:
            return None
        return SimpleNamespace(
            x=found.x, y=found.y, label=(getattr(found, "label", "") or query),
        )

    def _match_snapshot(self, query: str):
        """Best match from this turn's control snapshot, or None.

        Pure in-memory scoring over the (cx, cy, name, type) list already
        walked for the screen context — no COM calls, no tree walk. Applies
        the same ambiguity margin as the UIA search so a coin-flip between two
        similar names defers to the slower, better resolvers instead of
        guessing.
        """
        from types import SimpleNamespace
        from ai.hybrid_pointer import _score_match
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
            snapshot = self._pointables or None
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

        self._submit(self._plan_and_start_guide(goal))
        return {
            "started": True,
            "instruction": (
                "Say ONE short sentence RIGHT NOW confirming you'll walk them "
                "through it. Do NOT list steps and do NOT say 'say next' — "
                "Tora speaks and points at each step and advances by itself "
                "as they go. If they later say next/back/repeat/stop, call "
                "guide_step."
            ),
        }

    async def _plan_guide(self, goal: str, shot: str, title: str):
        """Fast plan, falling back to the careful model if it is unusable."""
        guide = await gemini_planner.plan(
            goal, screenshot_b64=shot, window_title=title, fast=True,
        )
        if not guide:
            _log.info("fast plan unusable for %r — retrying with the pro model",
                      goal[:60])
            guide = await gemini_planner.plan(
                goal, screenshot_b64=shot, window_title=title, fast=False,
            )
        return guide

    async def _plan_and_start_guide(self, goal: str) -> None:
        """Plan in the background, then hand the guide to the runner.

        Uses the FAST planner on the critical path — measured 3.8 s against
        17.6 s for the pro model, for a plan that was actually more granular.
        The pro model is kept as the fallback for when the quick plan comes
        back unusable, rather than as the thing everyone waits on.
        """
        shot = ""
        if not (self._privacy_guard and is_sensitive_window(active_window_title())):
            screens = await self._capture_shots(max_width=1024)
            if screens:
                active = self._active_shot(screens) or screens[0]
                shot = active.base64_jpeg

        title = active_window_title()
        self._activity("planning")
        try:
            guide = await self._plan_guide(goal, shot, title)
        finally:
            # Planning happens after show_me_how has already returned, so the
            # tool wrapper cannot cover it — clear it here or the island sticks.
            self._activity("")
        if not guide:
            await self._reply_local(
                guide.preamble
                or "I couldn't work out the steps for that from this screen."
            )
            return

        _log.info("guide ready: %d steps for %r", len(guide.steps), goal[:60])
        self._start_guide(guide)

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
            self.sig_error.emit(str(e))

        finally:
            if pointing_held:
                self.sig_point_release.emit()
            self._emit_state(AppState.IDLE)

    async def _reply_local(self, msg: str):
        """Show + speak a message that doesn't need an LLM round-trip."""
        self.sig_response_chunk.emit(msg)
        self.sig_response_done.emit(msg)
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

    def _start_guide(self, guide, *, start_index: int = 0) -> None:
        """Begin walking the user through a planned guide, hands-free."""
        from tutor_features.guide import GuideRunner
        self._stop_guide()
        self._resume = None
        self._lesson_plan = guide
        self._lesson_steps = [st.instruction for st in guide.steps]
        self._lesson_step_idx = start_index
        self._lesson_nagged = False

        runner = GuideRunner(
            guide,
            speak_step=self._present_step,
            check=self._step_done,
            say=self._reply_local,
            is_busy=self._guide_busy,
            on_stop=lambda: self._guide_stopped(runner),
            # Look-ahead evaluated the planner's absolute conditions, which
            # are often already true — the same defect that jammed steps,
            # except here it SKIPPED them. Off until it can use the watcher.
            look_ahead=None,
            on_stuck=self._guide_stuck,
            stuck_after_s=25.0,
            step_timeout_s=60.0,
            observer=__import__(
                "ai.verification", fromlist=["StateObserver"]
            ).StateObserver(),
            start_index=start_index,
        )
        self._guide_runner = runner
        self._step_audio.clear()
        self._step_point.clear()
        # Start generating step one's speech NOW, while the model is still
        # saying "sure, let me walk you through it" — so the first step lands
        # without the ~4 s synthesis pause.
        self._submit(self._prefetch_step(start_index))
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
            self._step_audio.clear()
            self._step_point.clear()
            try:
                from tutor_features.step_watch import _clicks
                _clicks.stop()
            except Exception:
                pass
            self.sig_guide_progress.emit(0, 0, "")
            self._activity("")
            if self._state != AppState.IDLE:
                self._emit_state(AppState.IDLE)

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

        alternatives = [
            a for a in (getattr(step, "possible_actions", None) or [])
            if a and a.lower() not in step.instruction.lower()
        ]
        if alternatives:
            msg = f"Or you can {alternatives[0][0].lower()}{alternatives[0][1:]}"
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
        return f"Step {idx + 1} of {len(plan.steps)}: {plan.steps[idx].instruction}"

    async def _prefetch_step(self, idx: int) -> None:
        """Generate the next step's SPEECH in advance.

        Synthesising a line takes ~4 s, so doing it at the moment the step is
        due meant the user sat through a long silence first. Doing it while the
        previous step is still being performed makes presenting the next one
        instant. Speech is safe to prepare early because the words do not
        depend on what is on screen — unlike the pointer target, see below.
        """
        plan = self._lesson_plan
        if plan is None or not (0 <= idx < len(plan.steps)):
            return
        if idx not in self._step_audio:
            try:
                pcm, rate = await self._get_tts().synthesize(
                    _speakable(self._step_line(idx))
                )
                if pcm:
                    self._step_audio[idx] = (pcm, rate)
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
        """Speak one guide step and point at its target — both immediately.

        Everything slow was prefetched while the previous step was being done,
        so the arrow and the first word land together instead of one long
        pause, some words, another pause, then the arrow.
        """
        plan = self._lesson_plan
        if plan is None or not (0 <= idx < len(plan.steps)):
            return
        self._lesson_step_idx = idx
        self._lesson_nagged = False
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

        self.sig_guide_progress.emit(idx + 1, len(plan.steps), step.instruction)

        # Resolve and point NOW, against the screen as it actually is. This is
        # deliberately not prefetched — see _prefetch_step.
        if step.target:
            self._submit(self._point_step_target(idx, step))

        self.sig_response_chunk.emit(msg)
        self.sig_response_done.emit(msg)
        self._last_response = msg
        self._emit_state(AppState.SPEAKING)
        try:
            cached = self._step_audio.pop(idx, None)
            if cached is not None:
                from audio.playback import play_pcm_async
                await play_pcm_async(cached[0], cached[1])
            else:
                await self._get_tts().speak(_speakable(msg))
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
            return await w.poll()
        return await self._step_done_by_expectation(step)

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
            t = find_target(name, skip_ocr=True, skip_vision=True)
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
            env_path = Path(__file__).parent / ".env"
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
            env_path = Path(__file__).parent / ".env"
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
        self._web_search_enabled = enabled

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
        self._slow_mode = enabled

    def set_guide_auto_advance(self, enabled: bool):
        """Tray: detect step completion automatically, or wait for 'next'."""
        self._guide_auto_advance = bool(enabled)
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
        return StepWatcher(
            step.instruction,
            judge=gemini_vision.judge_step,
            dpi_scale=self._dpi_scale(),
            local_check=local,
        )

    async def _point_step_target(self, idx: int, step) -> None:
        """Point at a guide step's target and tell its watcher the bounds,
        so a click inside them counts as the step being done."""
        ok = await self._point_to(step.target, hold=False)
        found = self._last_found if ok else None
        if found is None:
            return
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
        self._privacy_guard = enabled

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
        self._code_mode_auto = enabled

    def set_multilang(self, enabled: bool):
        self._multilang = enabled

    def set_journal(self, enabled: bool):
        self._journal_enabled = enabled

    def set_ocr_enabled(self, enabled: bool):
        self._ocr_enabled = enabled

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
