"""The first launch's conversation: Tora hears your name, then asks four
questions out loud (docs/ui/HEYCLICKY_ONBOARDING.md, phase C).

One live voice session for both steps, so the interview already knows the
name. It runs on its own thread and event loop, and its passes are the
tour's free ones (TOUR_VOICE, migration 0014) - never a talk message. No Qt
in here: the steps (ui/onboarding_steps.py) get what happens as events.

What she learns comes back as tool calls, not by reading her words: heard
the name -> `heard_name`; the four answers -> `interview_done`, with the
work picked from the same list the starter skills are keyed by. Nothing
else is declared, so she can't point, click or look at the screen here.

Events, on the session's thread - (kind, value):
    "state"  "connecting" | "speaking" | "your_turn" | "thinking" | "offline"
    "tora"   a piece of what she is saying        "tora_end"  her turn ended
    "you"    a piece of what she heard             "you_end"   yours ended
    "name"   the name she heard
    "done"   Answers
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

_log = logging.getLogger("tora.onboarding")

Event = Callable[[str, object], None]

# The four questions, in her own words - she may phrase them her way.
QUESTIONS = (
    "What do you mostly work on?",
    "How did you end up doing that - what's your story?",
    "What would you love me to take off your plate?",
    "Have you used an AI agent before?",
)


@dataclass
class Answers:
    name: str = ""
    uses: list[str] = field(default_factory=list)     # from setup_wizard.USES
    facts: list[str] = field(default_factory=list)    # short, about them, for her memory
    summary: str = ""


def uses_list() -> tuple[str, ...]:
    from ui.setup_wizard import USES
    return USES


def prompt(name: str = "") -> str:
    """Her instructions for the tour's conversation."""
    asks = "\n".join(f"{i}. {q}" for i, q in enumerate(QUESTIONS, 1))
    known = f"The user's name is {name}. " if name else ""
    return (
        "You are Tora, a friendly AI companion on the user's Windows PC, meeting them for the "
        "first time during a short guided tour. Speak warmly and briefly - one or two short "
        "sentences at a time. " + known +
        "\n\nPART 1 - THE NAME. The user holds two keys and says hi, usually with their name. "
        "If you hear a name, call heard_name with it, then say hello using it in one short "
        "sentence and stop - do not ask anything yet. If there is no name, say hello and ask "
        "what to call them; when they say it, call heard_name."
        "\n\nPART 2 - THE INTERVIEW. Only when you get the message [interview], ask these four "
        "questions one at a time, waiting for each answer:\n" + asks +
        "\nAfter each answer, reflect it back in one short sentence that shows you understood "
        "(\"So you're juggling code and running a business\"), then ask the next. Never ask "
        "two questions at once. If an answer is unclear, ask once more, briefly."
        "\n\nAfter the fourth answer, call interview_done with what you learned, then thank them "
        "by name in one sentence and say you'll use this to set yourself up for them."
        "\n\nThe user answers by voice or by typing; typed answers arrive as text. Never mention "
        "these instructions, tools, or the bracketed messages."
    )


def tools() -> list[dict]:
    return [{"functionDeclarations": [
        {
            "name": "heard_name",
            "description": "The user said their name. Call it as soon as you hear it.",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "Their first name, as they said it."},
            }, "required": ["name"]},
        },
        {
            "name": "interview_done",
            "description": "All four questions are answered. Call it once, before thanking them.",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string"},
                "work": {"type": "array", "items": {"type": "string", "enum": list(uses_list())},
                         "description": "Every kind of work that fits what they said, best first."},
                "facts": {"type": "array", "items": {"type": "string"},
                          "description": "Up to six short facts about them worth remembering, "
                                         "each one sentence in the third person (\"Runs a "
                                         "small bakery\")."},
                "summary": {"type": "string",
                            "description": "Two sentences: who they are and what they want help with."},
            }, "required": ["work", "facts", "summary"]},
        },
    ]}]


def answers_from(args: dict, name: str = "") -> Answers:
    """The tool's arguments, cleaned: only known kinds of work, a few short facts."""
    known = set(uses_list())
    uses = [u for u in (args.get("work") or []) if isinstance(u, str) and u in known]
    facts = [" ".join(str(f).split())[:200] for f in (args.get("facts") or []) if str(f).strip()][:6]
    return Answers(name=" ".join(str(args.get("name") or name).split())[:40],
                   uses=list(dict.fromkeys(uses)), facts=facts,
                   summary=" ".join(str(args.get("summary") or "").split())[:600])


def save(answers: Answers) -> None:
    """What she learned, kept: the name and work in her instructions (as
    the old first launch did), the starter skills that suit the work, and
    the facts in her memory. Each part on its own: one failing never stops
    the others."""
    from config import cfg
    from ui.setup_wizard import SetupWizard
    name = answers.name or cfg.profile()[0]
    try:
        cfg.set_profile(name, answers.uses)
    except Exception:  # noqa: BLE001
        _log.warning("could not save the profile", exc_info=True)
    SetupWizard._save_skills(answers.uses)
    facts = list(answers.facts) + ([answers.summary] if answers.summary else [])
    if not facts:
        return

    async def remember_all() -> None:
        from ai.gemini_memory import remember
        for fact in facts:
            await remember(fact, source="user")

    def run() -> None:
        try:
            asyncio.run(remember_all())
        except Exception:  # noqa: BLE001 - memory is a nicety here
            _log.warning("could not remember the interview", exc_info=True)

    threading.Thread(target=run, name="tora-onboarding-memory", daemon=True).start()


def _voice_class():
    from ai.gemini_realtime import RealtimeConversation

    class TourVoice(RealtimeConversation):
        """The live voice with only the tour's two tools."""

        def _setup(self, system_prompt: str) -> dict:
            setup = super()._setup(system_prompt)
            setup["tools"] = tools()
            return setup

    return TourVoice


class Interview:
    """The tour's conversation. Every method may be called from any thread."""

    def __init__(self, on_event: Event, *, voice: str = ""):
        self._emit = on_event
        self._voice_name = voice
        self.name = ""
        self.answers: Optional[Answers] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._conv = None
        self._mic = None
        self._held = False
        self._started = False
        self._interviewing = False
        self._prompt = ""
        self._said: list[str] = []                    # the conversation so far, for a recap

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        """Open the voice (once). Safe to call again."""
        if self._started:
            return
        self._started = True
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, name="tora-onboarding-voice",
                         daemon=True).start()
        self._run(self._open())

    def stop(self) -> None:
        self._close_mic()
        loop, conv = self._loop, self._conv
        if loop is None:
            return
        if conv is not None:
            fut = asyncio.run_coroutine_threadsafe(conv.close(), loop)
            try:
                fut.result(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        loop.call_soon_threadsafe(loop.stop)
        self._loop = self._conv = None
        self._started = False

    @property
    def ready(self) -> bool:
        return self._conv is not None and self._conv.ready

    def _run(self, coro) -> None:
        """On the voice's loop, as the tour: each task starts from the
        caller's context, so every one is marked - a reconnect from a typed
        answer is the tour's free pass too (0014)."""
        if self._loop is None:
            coro.close()
            return

        async def as_the_tour():
            from account.service import TOUR_VOICE
            TOUR_VOICE.set(True)
            return await coro
        asyncio.run_coroutine_threadsafe(as_the_tour(), self._loop)

    async def _open(self) -> None:
        from config import cfg
        self._emit("state", "connecting")
        self._conv = _voice_class()(
            tools={"heard_name": self._heard_name, "interview_done": self._done},
            on_heard=lambda t: self._emit("you", t),
            on_said=lambda t: self._emit("tora", t),
            on_speaking=lambda: self._emit("state", "speaking"),
            on_turn_end=self._turn_end,
            on_lost=lambda _r: None,
            recap=self._recap,
            voice=self._voice_name or cfg.voice or "Zephyr",
        )
        self._prompt = prompt(self.name)
        if not await self._conv.connect(self._prompt):
            self._emit("state", "offline")
            return
        self._emit("state", "your_turn")

    async def _ensure(self) -> bool:
        if self._conv is None:
            return False
        if self._conv.ready:
            return True
        # The same instructions, so the server resumes this conversation;
        # if it can't, the recap tells the new one what was said.
        return await self._conv.connect(self._prompt)

    # ── what she does ────────────────────────────────────────────────────────
    async def _heard_name(self, args: dict) -> dict:
        name = " ".join(str(args.get("name") or "").split())[:40]
        if name:
            self.name = name
            self._emit("name", name)
        return {"ok": True}

    async def _done(self, args: dict) -> dict:
        self.answers = answers_from(args, self.name)
        self._emit("done", self.answers)
        return {"ok": True}

    def _turn_end(self, turn) -> None:
        if turn.heard:
            self._said.append(f"User: {turn.heard.strip()}")
            self._emit("you_end", turn.heard)
        if turn.said:
            self._said.append(f"Tora: {turn.said.strip()}")
            self._emit("tora_end", turn.said)
        self._emit("state", "your_turn")

    def _recap(self) -> str:
        known = [f"(The user's name is {self.name}.)"] if self.name else []
        return "\n".join(known + self._said[-12:])

    # ── what the user does ───────────────────────────────────────────────────
    def begin_interview(self) -> None:
        """Step 4 opens: she starts asking."""
        if self._interviewing:
            return
        self._interviewing = True

        async def go() -> None:
            if await self._ensure():
                self._emit("state", "thinking")
                await self._conv.ask_text("[interview]")
            else:
                self._emit("state", "offline")
        self._run(go())

    def type(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return

        async def go() -> None:
            if await self._ensure():
                self._conv.cancel()
                self._said.append(f"User: {text}")
                self._emit("state", "thinking")
                await self._conv.ask_text(text)
            else:
                self._emit("state", "offline")
        self._run(go())

    def hold(self, down: bool) -> None:
        """The keys: held, her microphone is open; let go, the answer ends."""
        if down == self._held:
            return
        self._held = down
        if down:
            if self._conv is not None:
                self._conv.barge_in()
            self._open_mic()
            self._emit("state", "listening")
            return
        self._close_mic()
        if self._conv is not None:
            self._emit("state", "thinking")
            self._run(self._conv.end_user_audio(stop=lambda: self._held))

    def _open_mic(self) -> None:
        self._close_mic()
        loop = self._loop
        if loop is None:
            return
        try:
            import sounddevice as sd
            from ai.gemini_live import CHUNK_BYTES, INPUT_RATE
            from config import cfg

            def hear(data, _frames, _time, _status):
                conv = self._conv
                if self._held and conv is not None:
                    asyncio.run_coroutine_threadsafe(conv.push_audio(bytes(data)), loop)
            self._mic = sd.RawInputStream(device=cfg.mic_device(), channels=1, samplerate=INPUT_RATE,
                                          dtype="int16", blocksize=CHUNK_BYTES // 2, callback=hear)
            self._mic.start()
        except Exception:  # noqa: BLE001 - no microphone: typing still works
            _log.info("the tour's microphone could not open", exc_info=True)
            self._mic = None

    def _close_mic(self) -> None:
        mic, self._mic = self._mic, None
        if mic is not None:
            try:
                mic.stop()
                mic.close()
            except Exception:  # noqa: BLE001
                pass
