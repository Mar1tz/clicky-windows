"""
The conversation itself — gemini-3.8-live.

    mic ─► 100 ms frames ─► websocket ─► model
                                │           │
                           local VAD    audio + tool calls
                                │           │
                           barge-in ◄───────┘
                                ▼
                      flush playback, interrupt

Four things make this fast, and all four were learned the hard way:

  • The socket is opened when Tora starts, not when the hotkey is pressed.
    A Live handshake is roughly a third of a second and the setup exchange
    another. Paying that once per session instead of once per question is the
    difference between "instant" and "a beat too slow", every single turn.

  • The SERVER decides when the user stopped talking. A local silence timer
    has to wait out a pause long enough to be sure — 1.1 s here — and it
    spends that on every question. The model has the language context to hear
    that a sentence finished and starts answering immediately.

  • Nothing is uploaded before the question. There is no screenshot on the
    critical path: the model gets one line of text naming the app, the
    focused control and the buttons it can point at, and calls
    look_at_screen only when the answer genuinely needs pixels.

  • Barge-in is local. When the mic hears the user start talking over the
    answer, playback is flushed straight away rather than after a round trip
    to ask the server's permission.

Turn contract, forced by the wire format: a tool call ends the model's turn
with no audio, and the spoken answer arrives in the NEXT turn once the tool
result goes back. The pump therefore never treats a turn that produced only
tool calls as the end of an answer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ai.gemini import Models
from ai.gemini_live import CHUNK_BYTES, LiveSession, ToolCall, add_usage
from audio.playback import PcmStreamPlayer

_log = logging.getLogger("tora.realtime")

# A Live session is not open-ended; the server sends goAway before dropping
# one. This is a belt-and-braces age limit so a long-idle session is replaced
# in the background rather than discovered to be dead mid-question.
MAX_SESSION_AGE_S = 8 * 60

# A tool chain that never settles must not hang the conversation.
MAX_TOOL_ROUNDS = 6
# Past MAX_TOOL_ROUNDS every call is answered with an error, and on 24 Sep the
# model ignored 35 of those in a row: 41 guide_step calls in 20 s, each one a
# paid pass over the whole conversation, until the 22 s watchdog ended it.
# Past this many it is not asking any more, it is looping - the session is
# closed, and the conversation resumes in a fresh one (LG-03).
HARD_TOOL_ROUNDS = 10

# Context compression. 16k is roughly forty spoken turns of history, far more
# than any conversation refers back to, and it caps what every later turn
# costs. Keeping 8k after a trim leaves several turns of real context.
COMPRESSION_TRIGGER_TOKENS = 16_000
COMPRESSION_TARGET_TOKENS = 8_000

# How much trailing silence to stream so the server's VAD hears the question
# end. Eight frames is 800 ms, which measured reliable; the loop exits early
# as soon as the model starts speaking, so in practice it rarely runs to the
# end.
TAIL_SILENCE_FRAMES = 8


ToolHandler = Callable[[dict], Awaitable[dict]]


def _outcome_of(result: dict) -> str:
    """What a tool result actually SAYS happened — for the log.

    This used to print "ok" for anything without an `error` key, and
    `desktop_action` never sets one: it reports trouble as `ok: False` with
    a `note`. So a denied action logged "ok", a failed action logged "ok",
    and a whole evening of "Tora would not do it" came back from the field
    with a log file full of successes. The log is the first place anyone
    looks, and it was the one thing that could not be trusted.
    """
    if not isinstance(result, dict):
        return "ok"
    if result.get("error"):
        return "error: " + str(result["error"])[:160]
    if result.get("ok") is False:
        detail = result.get("result") or result.get("note") or "no reason given"
        state = "denied" if "did not approve" in str(detail) else "failed"
        return f"{state}: {str(detail)[:160]}"
    if result.get("awaiting_approval"):
        return "waiting for the user to approve"
    return "ok"


def tool_declarations() -> list[dict]:
    """What the live model can do besides talk.

    point_at takes a NAME, not coordinates. Windows already knows where
    "Save" is, to the pixel, and will say so in about five milliseconds —
    asking a vision model to estimate it from a screenshot is a thousand
    times slower and less accurate. Coordinates are accepted only as a
    fallback for things with no accessible name, and those come from
    look_at_screen, which has actually looked.
    """
    return [{"functionDeclarations": [
        {
            "name": "point_at",
            "description": (
                "Put Tora's pointer on a control, by name: the exact name "
                "from the controls list, or your best short name for it if "
                "it is not listed - Tora finds it. Instant, no screenshot. "
                "Call it BEFORE describing where something is: the arrow "
                "flies over while you speak."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "The control's name, exactly as listed, e.g. "
                            "'Bold (Ctrl+B)' or 'Conversations'."
                        ),
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "look_at_screen",
            "description": (
                "LOOK at the screen, for what names cannot answer: a game, a "
                "photo, a chart, a diagram, handwriting, an error with no "
                "name, any 'what is the best...'. Not for a named button - "
                "point_at is instant, this takes a second or two. It draws "
                "the answer (a what-to-do question: one arrow) and returns "
                "`say`: say that one line and stop. Never read out what it "
                "saw."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Exactly what to work out from the screen.",
                    },
                    "careful": {
                        "type": "boolean",
                        "description": "Dense reading only: a sharper image.",
                    },
                },
                "required": ["question"],
            },
        },
        {
            "name": "draw",
            "description": (
                "Draw ON what you are explaining: arrows, circles, boxes, "
                "labels. Coordinates are 0-1000, x from the left, y from the "
                "top. You cannot see the screen: take every position from "
                "look_at_screen's `highlights` (each tag holds its "
                "coordinates). Never invent coordinates, and don't redraw "
                "what look_at_screen has already highlighted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "string",
                        "description": (
                            "One or more tags, coords 0-1000. Leave the "
                            "trailing :color off: your drawings wear your own "
                            "colour, the one the user picked for you. Name a "
                            "colour only when it means something - red for a "
                            "mistake, green for what is right (red green "
                            "yellow orange purple white cyan). "
                            "[ARROW:x1,y1->x2,y2:color] "
                            "[LINE:x1,y1->x2,y2:color] "
                            "[CIRCLE:x,y,r:label:color] "
                            "[RECT:x1,y1,x2,y2:color] "
                            "[POLY:x1,y1 x2,y2 x3,y3:color] "
                            "[TEXT:x,y:content:color:size] "
                            "Example: [CIRCLE:500,300,80:here] "
                            "[CIRCLE:420,610,60:wrong:red]"
                        ),
                    },
                },
                "required": ["tags"],
            },
        },
        {
            "name": "clear_drawings",
            "description": "Wipe every annotation currently on screen.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "show_me_how",
            "description": (
                "Start a guided walkthrough of a multi-step task — 'how do I "
                "export/install/configure/set up…'. Returns INSTANTLY: call "
                "it FIRST, before saying anything, then say one short "
                "sentence confirming you'll walk them through it. Tora then "
                "speaks and points at each step and advances on its own as "
                "they go — you do NOT read the steps yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "What the user is trying to accomplish.",
                    },
                },
                "required": ["goal"],
            },
        },
        {
            "name": "guide_step",
            "description": (
                "Control a guided walkthrough - ONLY when the user gives "
                "one of these commands: next (done / I did it), skip, back "
                "(the step before), repeat (say this step again), stop, "
                "pause, resume (pick a stopped walkthrough back up), where "
                "(they can't find it - Tora points again), help (they're "
                "stuck - Tora points again and you explain it another way), "
                "do_it (do it for me). A question is not a command: answer it "
                "and leave the walkthrough alone - it advances on its own as "
                "each step is done."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["next", "skip", "back", "repeat", "stop",
                                 "pause", "resume", "where", "help", "do_it"],
                    },
                },
                "required": ["action"],
            },
        },
        {
            "name": "search_web",
            "description": (
                "Search the web. ONLY when the user explicitly asks you to "
                "search or look something up, or asks about news, prices, "
                "releases or events after your training. NEVER for what is on "
                "their screen, how to do something in an app, a game, or "
                "anything the screen context already answers. It costs 5-20 s."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "recall",
            "description": (
                "Search what Tora remembers about this user and the "
                "documents they have attached. Use when they refer to "
                "something from earlier, their preferences, or a file."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "delegate_task",
            "description": (
                "Hand real work to a background agent - folders, projects, "
                "apps, files, installs, or any job one of the user's skills "
                "describes - when the user asks you to BUILD, MAKE or DO "
                "something on their machine. Never for a question or the "
                "screen. It works while you talk: say briefly what it will do "
                "and that it has started."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "What to build, with every detail the user gave.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label, e.g. 'Create React app MyApp'.",
                    },
                    "workspace_hint": {
                        "type": "string",
                        "description": "The folder name they gave, exactly; omit if none.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["coding", "file", "terminal", "research", "computer"],
                    },
                    "skill": {
                        "type": "string",
                        "description": (
                            "The id of the user's skill this job is, from the "
                            "skills in your context. Omit when none fits."
                        ),
                    },
                },
                "required": ["goal"],
            },
        },
        {
            "name": "read_skill",
            "description": (
                "Read one of the user's skills in full: the steps and what to "
                "know. Use when they ask HOW to do a job a skill covers, to "
                "guide them through it on screen."
            ),
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
        {
            "name": "desktop_action",
            "description": (
                "Do one thing in a REAL APP on this PC, in the background, through "
                "its own API: Spotify (play, play_playlist, next, previous, play_pause, "
                "volume, now_playing), VS Code / Cursor / Windsurf (open_project, "
                "open_file, chat, recent_projects, install_extension), Windows "
                "(open_app, media, volume, window, clipboard_copy, open_folder, "
                "open_settings), websites (app 'browser': open_url, search - a new tab "
                "in the browser they have open). ALWAYS prefer the real app: a song is "
                "app='spotify', action='play', never open.spotify.com. Not sure an app "
                "can do it? action='list' first, never a browser instead. The result "
                "says when the user's yes is needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string",
                            "description": "browser, spotify, vscode, cursor, windsurf, "
                                           "antigravity, windows (the OS: any installed app, "
                                           "volume, windows, clipboard, folders, settings); "
                                           "chrome, edge, firefox, brave, opera ONLY if the "
                                           "user named it."},
                    "action": {"type": "string",
                               "description": "The action name, or 'list' to see what exists."},
                    "arguments_json": {
                        "type": "string",
                        "description": "The action's arguments as JSON, e.g. "
                                       "{\"project\": \"clicky-windows-main\"}.",
                    },
                },
                "required": ["app", "action"],
            },
        },
        {
            "name": "manage_agents",
            "description": (
                "Check on or control background agents: how a build is going, "
                "stop / pause / resume / cancel, approve / deny when one waits "
                "for permission."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "pause", "resume", "cancel", "approve", "deny"],
                    },
                    "task": {
                        "type": "string",
                        "description": "A few words of the task's title, if they named one.",
                    },
                },
                "required": ["action"],
            },
        },
    ]}]


@dataclass
class Turn:
    heard: str = ""
    said: str = ""
    tools_used: list[str] = field(default_factory=list)
    interrupted: bool = False
    # What the server billed for this turn, every generation in it summed
    # (a tool round is a generation of its own) - gemini_live.live_cost_usd.
    usage: dict = field(default_factory=dict)


class RealtimeConversation:
    """A long-lived audio conversation with gemini-3.8-live.

    Connected once and kept open. The event pump runs for the life of the
    session, so an answer starts playing the moment the server decides the
    user has finished speaking — there is nothing for the hotkey to wait for.
    """

    def __init__(
        self,
        *,
        tools: dict[str, ToolHandler],
        on_heard: Optional[Callable[[str], None]] = None,
        on_said: Optional[Callable[[str], None]] = None,
        on_speaking: Optional[Callable[[], None]] = None,
        on_turn_end: Optional[Callable[[Turn], None]] = None,
        on_lost: Optional[Callable[[str], None]] = None,
        recap: Optional[Callable[[], str]] = None,
        voice: str = "Zephyr",
    ):
        self._tools = tools
        self._on_heard = on_heard or (lambda _t: None)
        self._on_said = on_said or (lambda _t: None)
        self._on_speaking = on_speaking or (lambda: None)
        self._on_turn_end = on_turn_end or (lambda _t: None)
        self._on_lost = on_lost or (lambda _r: None)
        # Asked for the last few turns when a session could not be resumed,
        # so a fresh one still knows what "that" means.
        self._recap = recap
        self._voice = voice

        self._session: Optional[LiveSession] = None
        self._pump: Optional[asyncio.Task] = None
        self._opened_at = 0.0
        self._system = ""
        self._player = PcmStreamPlayer()
        self._connecting: Optional[asyncio.Task] = None
        self._resume_handle: Optional[str] = None
        # Bumped whenever a session starts BLANK rather than resuming. Callers
        # that avoid repeating themselves — the per-turn screen context — use
        # it to know that everything they said earlier is gone.
        self.epoch = 0

        self._turn = Turn()
        self._speaking = False
        # Bytes of the answer being spoken, counted from when its first audio
        # arrived; with what is still queued, this says how far the voice
        # has got (voice_clock). Reset when an answer starts after the last
        # one finished playing - not per Turn, which ends at turn_complete
        # while most of the answer is still waiting in the player.
        self._voice_bytes = 0
        self._tool_rounds = 0
        # The latest usageMetadata of the generation under way, folded into
        # the turn when that generation completes (SP-03).
        self._gen_usage: dict = {}
        # True once a turn has ended, until the user speaks again. The user
        # speaking across that boundary is what "a new request" means, and a
        # new request is the only thing that refills the tool budget. Using
        # "is this the first transcript of the turn" instead missed any
        # question that followed a tool-only turn (the turn never finished,
        # so its transcript was never cleared) - which is what Field fix 5
        # papered over by resetting on every turn_complete, disabling the
        # runaway brake entirely.
        self._awaiting_user = True

    # ── Session ──────────────────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        return self._session is not None and self._session.is_open

    @property
    def speaking(self) -> bool:
        """Is Tora's voice still coming out of the speakers?

        Not the same question as "is the server still generating". Gemini
        streams audio far faster than real time — a seven-second answer
        arrives in about two — and `turn_complete` clears `_speaking` the
        moment the last packet lands, while five seconds of that answer is
        still queued in the player. Anything that asks "is she busy" to
        avoid talking over her (the walkthrough, before it speaks a step)
        got False and spoke anyway, so the user heard two voices at once
        saying nearly the same thing.
        """
        if self._speaking:
            return True
        try:
            return self._player.queued_seconds > 0.05
        except Exception:
            return False

    def voice_clock(self) -> Optional[tuple[float, float]]:
        """How far Tora's voice has got through the answer she is speaking:
        (seconds heard so far, seconds of it received), or None when she is
        not speaking.

        The words arrive as fast as the audio - a seven-second answer in
        about two - so a caption that shows them on arrival runs seconds
        ahead of the voice. This is what lets it keep time instead. Safe to
        read from any thread: two numbers and a locked buffer length.
        """
        if not self.speaking:
            return None
        player = self._player
        received = self._voice_bytes / player.bytes_per_second
        played = received - player.queued_seconds - player.latency
        return max(0.0, played), received

    @property
    def connecting(self) -> bool:
        """A connect() is in progress and has not resolved yet."""
        t = self._connecting
        return t is not None and not t.done()

    def _setup(self, system_prompt: str) -> dict:
        return {
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": self._voice}
                    }
                },
            },
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            # Both transcriptions on: the panel shows the conversation as
            # text, and the journal needs something to log.
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "tools": tool_declarations(),
            # Automatic activity detection stays ON: the server decides when
            # the user stopped talking. Disabling it hands that job to a local
            # timer that can only guess from silence, and every guess costs
            # a wait.
            #
            # The two knobs below are worth half a second on every question,
            # measured from the last word to the first spoken reply:
            #     defaults                         1027 ms
            #     silenceDurationMs=400             555 ms
            #     silenceDurationMs=200 + HIGH      501 ms
            # A shorter silence window on its own did nothing (1009 ms);
            # it is the sensitivity that lets the short window bite.
            "realtimeInputConfig": {
                "automaticActivityDetection": {
                    "silenceDurationMs": 200,
                    "endOfSpeechSensitivity": "END_SENSITIVITY_HIGH",
                }
            },
            # Filled in by _connect: a handle continues the conversation.
            "sessionResumption": {},
            # The Live API bills the WHOLE context window on every turn -
            # system instruction, tools, and every earlier turn. Without a
            # cap, turn 30 of a chat costs twice what turn 1 did and the
            # total grows with the square of the conversation. The sliding
            # window keeps the recent context (system instruction and the
            # prefix are never dropped) and holds the per-turn price flat;
            # it is also what makes an unbounded session possible.
            "contextWindowCompression": {
                "triggerTokens": str(COMPRESSION_TRIGGER_TOKENS),
                "slidingWindow": {"targetTokens": str(COMPRESSION_TARGET_TOKENS)},
            },
        }

    def _stale(self) -> bool:
        return (
            not self.ready
            or (time.monotonic() - self._opened_at) > MAX_SESSION_AGE_S
        )

    async def connect(self, system_prompt: str) -> bool:
        """Open a session if there isn't a usable one. Safe to call often.

        Concurrent callers share one attempt — the hotkey and the startup
        warm-up can both land here, and two sockets to the same conversation
        is two sets of answers.
        """
        if not self._stale() and system_prompt == self._system:
            return True
        if self._connecting is not None and not self._connecting.done():
            try:
                return await self._connecting
            except Exception:
                return False

        self._connecting = asyncio.ensure_future(self._connect(system_prompt))
        try:
            return await self._connecting
        except Exception as e:
            _log.warning("realtime connect failed: %s", e)
            # Out of credit or a refused key arrives here first, as a 1011 on
            # the websocket. It used to end at this log line (APP-02).
            from ai import gemini
            gemini.report_problem(e)
            return False
        finally:
            self._connecting = None

    async def _connect(self, system_prompt: str) -> bool:
        """Open a session, continuing the previous conversation when we can.

        A Live session is closed by the server every few minutes. Before
        this, every reconnect started a blank conversation: the tools still
        worked, but "open that" had nothing to point at, so Tora looked like
        it had lost the ability to do things. The resumption handle is the
        server's own way of saying "carry on where you were".
        """
        await self.close()
        resume = self._resume_handle if system_prompt == self._system else None
        session = LiveSession(Models.REALTIME, self._setup(system_prompt), resume_handle=resume)
        try:
            await session.connect()
        except Exception:
            if not resume:
                raise
            # A handle can expire or be refused; a fresh session is better
            # than no session, and the recap below carries the thread.
            _log.info("could not resume the conversation; starting a new session")
            self._resume_handle = None
            session = LiveSession(Models.REALTIME, self._setup(system_prompt))
            await session.connect()
            resume = None
        self._session = session
        self._system = system_prompt
        self._opened_at = time.monotonic()
        if not resume:
            self.epoch += 1
        self._pump = asyncio.ensure_future(self._run_pump(session))
        _log.info("realtime session open (%s)%s", Models.REALTIME,
                  " - resumed" if resume else "")
        from ai import gemini
        gemini.report_ok()           # an account problem, if any, is over
        if not resume:
            await self._reseed()
        return True

    async def _reseed(self) -> None:
        """Hand a new session the last few turns, so references survive.

        Resumption is the good path; this is the fallback for when the
        handle is gone (first session of the day, an expired handle, a
        changed system prompt). It costs one short text turn.
        """
        recap = self._recap() if self._recap else ""
        if not recap:
            return
        try:
            await self._session.send_text(
                "[Continuing the same conversation. What was said so far:\n"
                + recap + "\nDo not greet the user again; carry on.]",
                turn_complete=False)
        except Exception:  # noqa: BLE001 - a recap is a nicety, never a blocker
            _log.debug("could not re-seed the session", exc_info=True)

    async def close(self) -> None:
        pump, self._pump = self._pump, None
        if pump is not None:
            pump.cancel()
        session, self._session = self._session, None
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass
        self._player.stop()
        self._speaking = False

    # ── Talking to it ────────────────────────────────────────────────────────

    async def send_context(self, text: str) -> None:
        """Tell the model about the screen, without ending a turn.

        Sent as the mic opens, so it is already in front of the model by the
        time the question finishes.
        """
        if text and self.ready:
            try:
                await self._session.send_text(text, turn_complete=False)
            except Exception:
                _log.debug("send_context failed", exc_info=True)

    async def say_now(self, note: str, *, wait_s: float = 25.0) -> bool:
        """Hand the model something to say, unprompted, and let it speak.

        `send_context` deliberately does NOT end the turn — it is background
        for a question that is still being asked. This does end it, which is
        what makes the model answer. It is how something that finished after
        the conversation moved on ("you approved it; the playlist is
        playing") gets said out loud instead of being silently dropped.

        It waits for a gap first: barging in on a half-spoken sentence is
        worse than being a few seconds late. Returns False if no gap came.
        """
        if not note:
            return False
        deadline = time.monotonic() + wait_s
        while self.speaking and time.monotonic() < deadline:
            await asyncio.sleep(0.15)
        if not self.ready or self.speaking:
            return False
        # A volunteered turn is a request of its own; whatever it does with
        # tools must not be charged to whatever came before it.
        self._tool_rounds = 0
        self._awaiting_user = False
        try:
            await self._session.send_text(note, turn_complete=True)
            return True
        except Exception:
            _log.debug("say_now failed", exc_info=True)
            return False

    async def send_image(self, jpeg_b64: str) -> None:
        if jpeg_b64 and self.ready:
            try:
                await self._session.send_image(jpeg_b64)
            except Exception:
                _log.debug("send_image failed", exc_info=True)

    async def push_audio(self, pcm16: bytes) -> None:
        if self.ready:
            try:
                await self._session.send_audio(pcm16)
            except Exception:
                _log.debug("push_audio failed", exc_info=True)

    async def end_user_audio(self) -> None:
        """Close the question by letting the server HEAR the silence after it.

        This is the single most important detail in the whole realtime path,
        and it is entirely counter-intuitive: the trailing silence is not
        wasted time, it is what makes the answer instant.

        The server's VAD decides a turn ended by hearing speech stop. Cut the
        stream off the moment the user stops talking and it never hears that,
        so it waits. Keep streaming quiet frames at the pace a microphone
        would produce them and the server detects the end DURING them and
        starts generating, so the first word lands right after the last frame.

        ⚠ It does NOT call audioStreamEnd, and that is deliberate and load-
        bearing. audioStreamEnd tells the server the audio input is finished
        for good; on a session kept open across questions, the SECOND
        question's audio then lands on a stream the server considers closed
        and is silently ignored — the first turn answers, every turn after it
        is met with silence. Paced trailing silence ends the turn on its own
        (VAD hears the pause), the stream is never declared finished, and the
        next question is simply more audio. This matches how a real
        continuous microphone behaves.

        Pacing is the other half of it. Blasting the silence as fast as the
        socket will take it makes the server work through a backlog rather
        than listen in real time, which is slower, not faster.
        """
        if not self.ready:
            return
        frame = bytes(CHUNK_BYTES)          # 100 ms of digital silence
        try:
            for _ in range(TAIL_SILENCE_FRAMES):
                await self._session.send_audio(frame)
                await asyncio.sleep(0.1)    # real-time pace, deliberately
                if self._speaking:
                    break                   # already answering — stop early
        except Exception:
            _log.debug("end_user_audio failed", exc_info=True)

    def barge_in(self) -> None:
        """The user started talking over the answer. Stop speaking NOW.

        Local and immediate — under a millisecond. The server is told
        separately, by the audio it is already receiving; waiting for it to
        agree would spend the whole budget on a round trip.
        """
        if self._speaking:
            self._player.clear()
            self._turn.interrupted = True

    def cancel(self) -> None:
        """Esc, or a new question. Same as a barge-in from our side."""
        self._player.clear()
        self._speaking = False

    # ── The pump ─────────────────────────────────────────────────────────────

    async def _run_pump(self, session: LiveSession) -> None:
        """Read events for the life of the session."""
        reason = "closed"
        try:
            async for ev in session.events():
                if session is not self._session:
                    return                      # superseded by a reconnect

                if ev.kind == "input_transcript":
                    if self._awaiting_user:
                        # The user speaking after a turn boundary is a new
                        # request, and only a new request refills the tool
                        # budget. See _awaiting_user in __init__.
                        self._tool_rounds = 0
                        self._awaiting_user = False
                    self._turn.heard += ev.text
                    self._on_heard(ev.text)

                elif ev.kind == "output_transcript":
                    if not self._turn.said:
                        self._on_speaking()
                    self._turn.said += ev.text
                    self._on_said(ev.text)

                elif ev.kind == "audio":
                    if not self._speaking:
                        if self._player.queued_seconds <= 0.05:
                            self._voice_bytes = 0       # a new answer, not more of the last
                        self._speaking = True
                        self._player.start()
                    self._voice_bytes += len(ev.audio)
                    self._player.feed(ev.audio)

                elif ev.kind == "interrupted":
                    self._player.clear()
                    self._turn.interrupted = True

                elif ev.kind == "tool_call":
                    _log.info("tool call: %s", ", ".join(c.name for c in ev.calls))
                    self._tool_rounds += 1
                    if self._tool_rounds > HARD_TOOL_ROUNDS:
                        _log.warning("tool loop cut off after %d rounds - closing the session; "
                                     "the conversation resumes in a fresh one",
                                     self._tool_rounds - 1)
                        reason = "tool loop"
                        try:
                            await session.close()
                        except Exception:  # noqa: BLE001 - it is being dropped anyway
                            pass
                        break
                    if self._tool_rounds > MAX_TOOL_ROUNDS:
                        _log.warning("tool loop did not settle after %d rounds in this request — "
                                     "answering anyway", self._tool_rounds - 1)
                        await session.send_tool_response([
                            (c, {"error": "too many tool calls; just answer"})
                            for c in ev.calls
                        ])
                        continue
                    results = []
                    for call in ev.calls:
                        self._turn.tools_used.append(call.name)
                        results.append((call, await self._run_tool(call)))
                    await session.send_tool_response(results)

                elif ev.kind == "usage":
                    self._gen_usage = ev.raw

                elif ev.kind == "turn_complete":
                    # A turn that produced only tool calls is not the answer;
                    # the answer is the turn that follows the tool results.
                    #
                    # This does NOT refill the tool budget. It used to (Field
                    # fix 5), and on the Live API every tool round ends with
                    # its own turn_complete - so the counter went 1, 0, 1, 0
                    # and MAX_TOOL_ROUNDS could never trip. It fired zero
                    # times in the whole log while the model called
                    # guide_step 39 times in 24 seconds and walked off the end
                    # of a walkthrough. The budget refills when the USER
                    # speaks next (input_transcript) or when a spoken answer
                    # finishes the request (_finish_turn).
                    self._awaiting_user = True
                    if self._gen_usage:
                        self._turn.usage = add_usage(self._turn.usage, self._gen_usage)
                        self._gen_usage = {}
                    if not ev.produced_output:
                        continue
                    self._finish_turn()

                elif ev.kind == "go_away":
                    # Retire this session after the current answer.
                    self._opened_at = 0.0
                    reason = "server closed the session"

                elif ev.kind == "closed":
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            reason = str(e)
            _log.warning("realtime pump ended: %s", e)

        if session is self._session:
            # Keep the handle: the next connect resumes this conversation.
            self._resume_handle = session.resume_handle or self._resume_handle
            self._session = None
            self._speaking = False
            self._on_lost(reason)

    def _finish_turn(self) -> None:
        turn, self._turn = self._turn, Turn()
        self._speaking = False
        self._tool_rounds = 0
        self._awaiting_user = True
        if turn.heard or turn.said:
            self._on_turn_end(turn)

    async def _run_tool(self, call: ToolCall) -> dict:
        handler = self._tools.get(call.name)
        if handler is None:
            _log.warning("tool %s is not registered in this session", call.name)
            return {"error": f"unknown tool {call.name}"}
        try:
            out = await handler(call.args or {})
            result = out if isinstance(out, dict) else {"result": out}
        except Exception as e:
            _log.exception("tool %s failed", call.name)
            return {"error": str(e)}
        # Logged at INFO on purpose: "it stopped doing things" is otherwise
        # invisible in tora.log, which is the first place anyone looks.
        _log.info("tool %s -> %s", call.name, _outcome_of(result))
        return result
