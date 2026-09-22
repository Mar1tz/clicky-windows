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
from ai.gemini_live import CHUNK_BYTES, LiveSession, ToolCall
from audio.playback import PcmStreamPlayer

_log = logging.getLogger("tora.realtime")

# A Live session is not open-ended; the server sends goAway before dropping
# one. This is a belt-and-braces age limit so a long-idle session is replaced
# in the background rather than discovered to be dead mid-question.
MAX_SESSION_AGE_S = 8 * 60

# A tool chain that never settles must not hang the conversation.
MAX_TOOL_ROUNDS = 6

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
                "Put Tora's pointer on a control, by name. Use the exact "
                "name from the list of controls you were given. Call this "
                "BEFORE describing where something is — the arrow flies over "
                "while you speak. Instant: no screenshot needed."
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
                "Actually LOOK at the screen and answer a question about what "
                "it shows. Use ONLY when the answer needs the pixels: a game, "
                "a photo, a chart, a chessboard, a diagram, handwriting, an "
                "error with no name, or any 'what is the best…' question. "
                "Do NOT use it to find a named button — point_at is instant "
                "and this takes a second or two. It highlights what it finds "
                "on screen, so describe the highlight in your answer."
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
                        "description": (
                            "True for anything needing real reading or "
                            "judgement — a board position, dense code, the "
                            "best option. Slower and much better."
                        ),
                    },
                },
                "required": ["question"],
            },
        },
        {
            "name": "draw",
            "description": (
                "Draw on the user's screen — arrows, circles, boxes, labels. "
                "Draw ON what you are explaining. Coordinates are normalized "
                "0-1000, so you need to know where things are: call "
                "look_at_screen first unless you are drawing somewhere you "
                "were just told about."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "string",
                        "description": (
                            "One or more tags, coords 0-1000, trailing :color "
                            "optional (blue red green yellow orange purple "
                            "white cyan). [ARROW:x1,y1->x2,y2:color] "
                            "[LINE:x1,y1->x2,y2:color] "
                            "[CIRCLE:x,y,r:label:color] "
                            "[RECT:x1,y1,x2,y2:color] "
                            "[POLY:x1,y1 x2,y2 x3,y3:color] "
                            "[TEXT:x,y:content:color:size] "
                            "Example: [CIRCLE:500,300,80:here:red]"
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
                "export/install/configure/set up…'. Returns INSTANTLY, so "
                "call it and immediately say one short sentence confirming "
                "you'll walk them through it. Tora then speaks and points "
                "at each step and advances on its own as they go — you do NOT "
                "read the steps yourself."
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
                "Control a guided walkthrough. Call this when the user says "
                "'next' (skip ahead), 'back' (repeat the previous step), "
                "'again'/'repeat' (say this step again), 'stop'/'cancel' (end "
                "it), or 'continue'/'resume' (pick a paused guide back up). "
                "The guide advances on its own otherwise — only call this when "
                "the user asks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["next", "back", "repeat", "stop", "resume"],
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
                "Hand real work to a background agent: creating folders and "
                "projects, scaffolding apps, writing files, running installs. "
                "Use when the user asks you to BUILD or MAKE something on "
                "their machine — never for questions, explanations, or "
                "anything about what is on their screen. The agent works "
                "while you keep talking: read the returned steps back "
                "briefly, say it has started, and that they can carry on."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": (
                            "What to build, in one or two sentences, keeping "
                            "every detail the user gave."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label, e.g. 'Create React app MyApp'.",
                    },
                    "workspace_hint": {
                        "type": "string",
                        "description": (
                            "The folder name the user asked for, e.g. 'MyApp', "
                            "exactly as they said it. Omit if they gave none."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["coding", "file", "terminal", "research"],
                    },
                },
                "required": ["goal"],
            },
        },
        {
            "name": "desktop_action",
            "description": (
                "Do one thing in an app on this PC, straight away: Spotify (next, previous, "
                "play_pause, now_playing, play_playlist), VS Code / Cursor (open_project, "
                "open_file, chat, recent_projects), browsers (open_url, search), Windows "
                "(media, volume, window, clipboard_copy, open_folder, open_settings, open_app, "
                "open_url). Uses the app's own API, link or media key - nothing on screen moves. "
                "Use action='list' to see what an app can do. The user confirms anything that acts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string",
                            "description": "spotify, vscode, cursor, edge, brave, opera, windows"},
                    "action": {"type": "string",
                               "description": "The action name, or 'list' to see what exists."},
                    "arguments_json": {
                        "type": "string",
                        "description": "The action's arguments as JSON, e.g. "
                                       "{\"project\": \"clicky-windows-main\"} or "
                                       "{\"prompt\": \"explain this repo\"}.",
                    },
                },
                "required": ["app", "action"],
            },
        },
        {
            "name": "manage_agents",
            "description": (
                "Check on or control background agents. Use when the user "
                "asks how a build is going, whether something is ready, or "
                "says stop / pause / resume / cancel that, or approve / deny "
                "when an agent is waiting for permission. Also turns industry "
                "extensions on and off (e.g. id 'accounting' when the user says "
                "they are an accountant and agrees), opens an extension's "
                "dashboard, and opens the wizard to create a specialist agent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "pause", "resume", "cancel", "approve", "deny",
                                 "list_extensions", "enable_extension", "disable_extension",
                                 "open_dashboard", "create_agent"],
                    },
                    "task": {
                        "type": "string",
                        "description": "A few words from the task's title, if they named one.",
                    },
                    "id": {
                        "type": "string",
                        "description": "Extension id for the extension actions, e.g. 'accounting'.",
                    },
                    "template": {
                        "type": "string",
                        "description": "For create_agent: bookkeeper, accounts_payable, accounts_receivable, "
                                       "month_end, tax_preparation, audit_assistant, payroll_review or "
                                       "management_reporting.",
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
        self._tool_rounds = 0

    # ── Session ──────────────────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        return self._session is not None and self._session.is_open

    @property
    def speaking(self) -> bool:
        return self._speaking

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
                    if not self._turn.heard:
                        # A new question: the tool budget starts again. Before
                        # this, a long conversation quietly used up the six
                        # rounds and Tora stopped being able to do anything.
                        self._tool_rounds = 0
                    self._turn.heard += ev.text
                    self._on_heard(ev.text)

                elif ev.kind == "output_transcript":
                    if not self._turn.said:
                        self._on_speaking()
                    self._turn.said += ev.text
                    self._on_said(ev.text)

                elif ev.kind == "audio":
                    if not self._speaking:
                        self._speaking = True
                        self._player.start()
                    self._player.feed(ev.audio)

                elif ev.kind == "interrupted":
                    self._player.clear()
                    self._turn.interrupted = True

                elif ev.kind == "tool_call":
                    _log.info("tool call: %s", ", ".join(c.name for c in ev.calls))
                    self._tool_rounds += 1
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

                elif ev.kind == "turn_complete":
                    # A turn that produced only tool calls is not the answer;
                    # the answer is the turn that follows the tool results.
                    # Either way the chain for this request has ended, so the
                    # tool budget goes back to full.
                    self._tool_rounds = 0
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
        _log.info("tool %s -> %s", call.name,
                  "error: " + str(result["error"])[:120] if result.get("error") else "ok")
        return result
