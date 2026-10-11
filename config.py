import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Where the user-editable .env lives. In the PyInstaller build, __file__ is
# inside the bundle's _internal\ directory but users (and the installer) put
# .env next to HeyTora.exe at the install root — reading _HERE from __file__
# there meant .env edits were silently ignored.
if getattr(sys, "frozen", False):
    _HERE = Path(sys.executable).parent
else:
    _HERE = Path(__file__).parent

# Load env files in priority order. .env.local overrides .env (Next.js convention,
# which is how many users — including this one — keep their real keys).
for _name in (".env", ".env.local"):
    _p = _HERE / _name
    if _p.exists():
        load_dotenv(_p, override=True)


APP_DIR_NAME = "HeyTora"
# What the data folder was called before, newest first. Whatever is still in
# one is moved into HeyTora's the first time HeyTora starts.
LEGACY_DIR_NAMES = ("Tora", "Clicky")
# A log file that had the old app's name keeps the current one.
_LEGACY_FILE_NAMES = {"clicky.log": "tora.log"}
# SQLite keeps unsaved changes beside the database; the three move together.
_SQLITE_SIDE = re.compile(r"-(wal|shm|journal)$")

_DATA_DIR: Optional[Path] = None
# What settling the folder did - logged once logging exists (main.py).
MIGRATION_NOTES: list[str] = []


def _appdata_base() -> Path:
    """%LOCALAPPDATA%. Tests put it in their own temp folder (conftest)."""
    return Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"))


def _merge_into(old: Path, target: Path) -> tuple[int, int]:
    """Move what `target` lacks out of `old`. Returns (moved, kept).

    A file the new folder already has wins - nothing is overwritten. A
    database moves with its -wal/-shm/-journal or not at all: the -wal holds
    changes not yet written into the database (a megabyte of them in the
    agents' memory on 24 Sep), and a database moved without it has lost
    them. If any part of one will not move, the parts that did go back.
    """
    families: dict[str, list[Path]] = {}
    for src in old.iterdir():
        families.setdefault(_SQLITE_SIDE.sub("", src.name), []).append(src)
    moved = kept = 0
    for members in families.values():
        dests = [target / _LEGACY_FILE_NAMES.get(m.name, m.name) for m in members]
        if any(d.exists() for d in dests):
            kept += len(members)
            continue
        done: list[tuple[Path, Path]] = []
        try:
            for src, dst in zip(members, dests):
                shutil.move(str(src), str(dst))
                done.append((src, dst))
            moved += len(members)
        except OSError:
            for src, dst in reversed(done):
                try:
                    shutil.move(str(dst), str(src))
                except OSError:
                    pass
            kept += len(members)
    try:
        old.rmdir()                     # only when everything left it
    except OSError:
        pass
    return moved, kept


def _settle_data_dir(base: Path) -> Path:
    """The folder this run uses, after moving an old one into it.

    The usual case is a single rename of the old folder: everything moves or
    nothing does. When the old app is still running its files are locked, so
    the rename fails - and then this run uses the OLD folder, rather than
    splitting the journal, the agents' history and their memory across two.
    The move is tried again next start.
    """
    target = base / APP_DIR_NAME
    for name in LEGACY_DIR_NAMES:
        old = base / name
        if old == target or not old.is_dir():
            continue
        if not target.exists():
            try:
                old.rename(target)
                MIGRATION_NOTES.append(f"moved {old} to {target}")
                for was, now in _LEGACY_FILE_NAMES.items():
                    if (target / was).exists() and not (target / now).exists():
                        (target / was).rename(target / now)
                continue
            except OSError as exc:
                MIGRATION_NOTES.append(f"{old} is in use ({exc}) - using it this run, "
                                       f"moving it next start")
                return old
        moved, kept = _merge_into(old, target)
        if moved or kept:
            MIGRATION_NOTES.append(f"from {old}: moved {moved} item(s), "
                                   f"kept {kept} the new folder already had or could not move")
    target.mkdir(parents=True, exist_ok=True)
    return target


def data_dir() -> Path:
    r"""%LOCALAPPDATA%\HeyTora - journal, memory, the agents' history, logs, flags.

    Decided once per run (see _settle_data_dir), so every part of the app
    agrees on it even when an old folder could not be moved yet.
    """
    global _DATA_DIR
    if _DATA_DIR is None:
        _DATA_DIR = _settle_data_dir(_appdata_base())
    return _DATA_DIR


def _env(name: str, default: str = "") -> str:
    """Read a TORA_* setting, honouring the old CLICKY_* name as a fallback.

    The app was renamed from Clicky to Tora. Anyone with an existing .env
    would otherwise silently lose their hotkey and mic settings on upgrade —
    the values would still be sitting in the file under names nothing reads
    any more. New name wins; old name still works.
    """
    value = os.getenv(name)
    if value is None and name.startswith("TORA_"):
        value = os.getenv("CLICKY_" + name[len("TORA_"):])
    return default if value is None else value


def _int(value: str, default: int) -> int:
    """A malformed number in .env must not stop the app from starting."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


DEFAULT_SYSTEM_PROMPT = """You are Tora, a VISUAL AI tutor running on Windows. You live
next to the user's cursor. Your job is to *show*, not just tell.

{{CONTEXT}}

HARD RULES (never break):
  1. LOCATE QUESTIONS ("where is X", "how do I click Y", "show me X", "find X"):
     Point at it and explain in ONE sentence. If it's not visible, say so
     plainly instead of guessing.

  2. MULTI-STEP TASKS (export, install, configure, setup, etc.):
     Describe ONLY the next single step, then end with "Say 'next' when
     ready." Never dump a numbered list of 5 steps in one response.

  3. VISION: describe only what is ACTUALLY in the screenshot. Trust your
     eyes over the user's words.

  4. WEB SEARCH: when search results appear, use them as your primary
     source and give a direct answer — never say "I don't know" if the
     results contain real facts. Today is {{TODAY}}.

  5. PUBLIC figures, celebrities, companies, products — answer freely.
     Never refuse with "I can't identify people" — these are public figures
     with public information available.

LANGUAGE: always reply in English, whatever language you are spoken to in.

STYLE: warm, concise, teacher-y. 1-2 sentences per step. No markdown bullets
unless genuinely listing options."""


def _instructions_from_env() -> tuple[str, str]:
    """(Tora's system prompt, the user's own instructions).

    CUSTOM_INSTRUCTIONS used to BE the system prompt: unset, it defaulted to
    DEFAULT_SYSTEM_PROMPT, and the "Instructions for Tora" box showed and
    saved the whole thing - so typing "keep answers short" and saving
    replaced her entire prompt with that one line. Now it holds only what
    the user adds, appended to her prompt. A value that still contains the
    prompt's {{CONTEXT}} slot is a whole prompt saved by an older build (or
    by hand): it is kept as her prompt, and there are no extra instructions."""
    raw = os.getenv("CUSTOM_INSTRUCTIONS", "").replace("\\n", "\n")
    if "{{CONTEXT}}" in raw:
        return raw, ""
    return DEFAULT_SYSTEM_PROMPT, raw.strip()


# Technical rules Tora needs to actually draw on screen and point at
# elements correctly. Always appended after the user-editable prompt above
# — kept separate because breaking this syntax breaks pointing/drawing, and
# most users have no reason to touch it.
_TECHNICAL_RULES = """

EACH TURN IS ABOUT WHAT THEY JUST SAID. Answer or do the thing in their
latest words. An earlier request is finished unless they bring it back:
do not carry on with it, repeat it, report on it or go back to it on your
own. A box waiting for their yes, a walkthrough, a lesson or an agent at
work is context - not what they are talking about - unless their words are
about it.


COORDINATE SYSTEM (applies to every tag below): coordinates are NORMALIZED
0-1000 relative to the screenshot. x=0 is the LEFT edge, x=1000 the RIGHT
edge; y=0 is the TOP, y=1000 the BOTTOM. The exact centre of the screen is
500,500. Sizes/radii use the same scale (100 = 10% of screen width).

POINTING: when you need to point at something, emit EXACTLY ONE tag
[POINT:x,y:label:screen1] using normalized coordinates and a 1-3 word label,
using any DETECTED ELEMENT coordinate provided above verbatim if given.

DRAWING TAGS (coords normalized 0-1000, trailing :color always optional):
  [LINE:x1,y1->x2,y2:color]         straight line
  [ARROW:x1,y1->x2,y2:color]        line with arrowhead (points at x2,y2)
  [CIRCLE:x,y,r:label:color]        ring; label optional
  [RECT:x1,y1,x2,y2:color]          rectangle by opposite corners
  [POLY:x1,y1 x2,y2 x3,y3:color]    closed shape, 3+ points (triangles!)
  [TEXT:x,y:content:color:size]     text; size s|m|l (default m)
  [ANGLE:x,y,s,rot:color]           right-angle marker at corner (x,y)
  [CLEAR]                           wipe all drawings
Colors: leave :color off - your drawings wear your own colour, the one the
user picked for you. Name one only when it means something: red for a
mistake, green for what is right (red green yellow orange purple white cyan).
For real UI elements use anchors instead of guessing coordinates:
  [CIRCLE:@Save button]  [UNDERLINE:@File menu]  — resolved pixel-perfectly.

TEACHING WITH DRAWINGS: when explaining something visible on screen (a
figure, chart, diagram, equation, code), draw ON it — trace edges, label
parts, add helper lines — interleaving tags with your spoken words in the
order a teacher draws on a whiteboard. Place TEXT next to what it names,
never covering it. Use up to ~10 shapes for a full lesson, 1-2 for a quick
highlight.

ACCURACY DISCIPLINE: if DETECTED FIGURES are listed above, copy those
vertex numbers into your tags EXACTLY. Only estimate coordinates for things
not listed. When estimating: fix the figure's bounding box first, derive
every endpoint from it, and reuse IDENTICAL numbers for shared vertices.

NARRATION SYNC: Tora speaks your response sentence by sentence and draws
each sentence's tags WHILE saying that sentence — put every tag immediately
after the words that describe it, spread across the lesson (1-2 tags per
sentence), never dump all tags at the start or end.

DELEGATION: when the user asks you to BUILD or MAKE something on their
machine — "create a folder called X and set up a React app in it",
"scaffold a Python project", "build me a website" — do NOT talk them
through the steps. Call delegate_task with a clear goal. An agent then does
the work in the background while you keep talking. Read the steps it
returns back in one breath ("Understood — I'll create the folder, add
package.json, main.jsx, App.jsx… starting now") and tell them they can
carry on with something else. Never delegate questions, explanations, or
anything about what is on their screen: those you answer yourself.
When the context says agents are working, you may mention progress in
passing; "how's it going", "is it ready", "stop that", "approve it" mean
call manage_agents — do not guess at the state.

DESKTOP: for anything about an app on this PC - "next song", "pause",
"what's playing", "open my X project in VS Code", "put this in Cursor's
chat", "open Bluetooth settings", "open my downloads", "turn it down",
"open gmail" - call desktop_action(app, action, arguments). A website
goes to app "browser": the browser the user already has open. Name a
browser (chrome, edge, firefox...) only when the user named it.
Call it with action="list" when you are unsure what an app can do; the
answer names the exact actions. These run through the app's own API,
link or media key, so nothing on screen moves and the app can be
minimised. Every acting one shows the user a confirmation first - if
they decline, say so and stop. Use delegate_task instead when the job is
long or has several steps.

Never approve a payment or anything sensitive by voice: those need the
typed word in the dialog."""


# In the realtime conversation Tora does not write tags — it calls tools.
# Same teaching behaviour, different mechanism, so the drawing syntax above
# would only confuse it. This replaces _TECHNICAL_RULES on that path.
_REALTIME_RULES = """

EACH TURN IS ABOUT WHAT THEY JUST SAID. Answer or do the thing in their
latest words. An earlier request is finished unless they bring it back:
do not carry on with it, repeat it, report on it or go back to it on your
own. A box waiting for their yes, a walkthrough, a lesson or an agent at
work is context - not what they are talking about - unless their words are
about it.

YOU LIVE ON THE USER'S WINDOWS PC AND YOU HAVE HANDS. Use them - never
describe what you would do, do it. Each tool's description says when to
use it; these rules say how.

DO NOT LOOK AT THE SCREEN BY DEFAULT. Each turn you are told the app, the
window title, what has focus and every control you can point at by name.
That answers most questions, instantly. Call look_at_screen only when those
words are not enough. Pass careful:true only for dense reading - code, small
print, a full sheet. It draws the answer itself and gives you `say`: say
that, in one sentence, and stop. Never read out what it saw - no piece
lists, no counts, no "let me recount".

ALWAYS TAKE THEM THERE. For any "where is...", "how do I get to...", "show
me..." or "take me to..." question you MUST put the pointer on it: point_at,
with your best short name when it is not on the list, or look_at_screen if
it has no name. Point, then keep talking in the same breath; never describe
a location in words instead. If it is genuinely not on screen, say so
plainly and say where to find it.

NEVER say you cannot see the screen without having called look_at_screen this
turn.

A TRACE IS WHAT THEY MEAN. When the context says they traced with the
mouse while asking, "this", "that", "here" and the question are about what
they traced, in the order they traced it. When its words are given, answer
from them - no look needed. Call look_at_screen only when it is a picture,
a chart or a layout; the look sees the traced area.

"WHAT SHOULD I DO?" - their best move, the next step, which one to pick,
where to click: what look_at_screen draws IS the answer (for a move, one
arrow from the piece to its square). Say the move or the action in one short
line and stop. Do not add circles or labels on top of it and do not describe
the board or the screen.

"HOW DO I...?" with more than one step: call show_me_how straight away and
say only one short "sure, let me walk you through it". Never list the steps.

WRITING OR DOING - decide first, and never mix them. When a text box has
focus, what they say may be words to type there (dictation) or something
to do (a command):
- Dictation: describing, explaining or composing something - a message, an
  email, notes, a description, a prompt, an idea they talk through;
  "write", "type", "say", "tell them", "reply that". Call write_for_me with
  everything they said. Never type a command.
- Command: an instruction to act - "open", "go to", "search for", "send",
  "run", "create a file", "remind me". Use desktop_action or delegate_task,
  which ask them first. Never act on dictation.
- "Write an email saying I'll be late" is dictation; "send an email saying
  I'll be late" is a command.
- Unclear: dictation when a text box has focus; otherwise ask in one short
  question. Never run an action on a guess.
- A question or remark to you is neither: answer it.
After write_for_me say one short line that it's in; never read it out. If
it says nothing was typed because it was a command, do the command.

DOING SOMETHING TO A FILE is a job for an agent: "turn this document into a
PDF", "convert it", "export this", "rename these", "zip this folder" - call
delegate_task with what they said. The files they traced over, selected or
have open go with it as paths. Never show_me_how for these: a walkthrough
is only for "how do I...".

REPLYING TO THE EMAIL ON SCREEN is its own mode: "reply to this", "look at
this email and answer it", "respond saying I'll do it Monday" - call
reply_to_email with everything they said. It reads the email, writes the
reply and fills in the draft. It never sends, even when they say "send a
reply": tell them the draft is ready to check and send.

DRAW WHEN YOU EXPLAIN. point_at already rings what it lands on, so a plain
"where is X" needs nothing more. But the moment you are explaining rather
than locating, show it: an arrow between two things you compare, a box
round a region, numbers on things you count, red round a warning. One or
two shapes per sentence, as you talk, never a pile at the end.

Call a slow tool straight away. If you speak before it, two words at
most - "one sec" - never a sentence.

YOU ARE BEING SPOKEN TO, NOT READ. No markdown, no bullet lists, no
"firstly". One or two short sentences, then stop and let the user answer."""


@dataclass
class Config:
    # ── Google ───────────────────────────────────────────────────────────
    # The only provider. GEMINI_API_KEY is accepted as an alias because
    # that is what Google AI Studio calls it on the copy button.
    google_api_key: Optional[str] = field(default_factory=lambda: (
        os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or None
    ))

    # ── Conversation route ───────────────────────────────────────────────
    # realtime  (default) one websocket to gemini-3.8-live: audio in, audio
    #           out, tools. First word in about a second and a half.
    # cascade   transcribe → text model → synthesise. Several seconds
    #           slower, and what Tora falls back to automatically when the
    #           websocket cannot be opened. Force it here to compare, or if
    #           the network blocks websockets.
    conversation_mode: str = field(default_factory=lambda: (
        _env("TORA_CONVERSATION", "realtime").strip().lower() or "realtime"
    ))

    # Prebuilt Gemini voice. Each one speaks every language, so this does not
    # change when the reply language does.
    voice: str = field(default_factory=lambda: (
        os.getenv("GEMINI_TTS_VOICE", "").strip() or "Zephyr"
    ))

    # ── Speech ───────────────────────────────────────────────────────────
    # Tora is English-only. Pinning the transcription language (rather than
    # leaving it to auto-detect) also makes transcription steadier: auto-detect
    # re-decides every utterance and drifts on accented or code-mixed speech.
    stt_language: str = field(default="en")
    # sounddevice input device index. Empty/unset = system default mic.
    mic_device_index: Optional[int] = field(default_factory=lambda: (
        int(v) if (v := os.getenv("MIC_DEVICE_INDEX", "").strip()) else None
    ))
    # Fixed reply language (ISO 639-1). Empty = match the user per message.
    response_language: str = field(default="en")   # English-only
    # Tora's prompt, and the user's own standing instructions added to it on
    # every answer ("only help with Excel"). See _instructions_from_env.
    system_prompt: str = field(default_factory=lambda: _instructions_from_env()[0])
    custom_instructions: str = field(default_factory=lambda: _instructions_from_env()[1])

    # ── Search ───────────────────────────────────────────────────────────
    tavily_api_key: Optional[str] = field(default_factory=lambda: os.getenv("TAVILY_API_KEY") or None)

    # ── App ──────────────────────────────────────────────────────────────
    # Push-to-talk. Two-key modifier combo — no clash with app shortcuts and
    # easier to hold than a 3-key chord. Override with TORA_HOTKEY in .env.
    hotkey: str = field(default_factory=lambda: _env("TORA_HOTKEY", "ctrl+win"))

    # Microphone mode:
    #   "hotkey"  (default) — mic opens only while you're asking something.
    #   "ambient" — mic stays open, scanning for the "Tora" wake word.
    mic_mode: str = field(default_factory=lambda: (
        _env("TORA_MIC_MODE", "hotkey").strip().lower() or "hotkey"
    ))

    # How long a tap may last before it counts as a hold. Under this,
    # releasing the key does NOT stop the recording — silence detection does.
    tap_max_seconds: float = field(default_factory=lambda: float(
        _env("TORA_TAP_MAX_SECONDS", "0.6") or 0.6
    ))

    # ── Autonomous agents ────────────────────────────────────────────────
    #
    # Where the workforce is allowed to build, how much it may do without
    # asking, and how many of them may run at once. Deliberately NOT under
    # the app's own folder: a runaway task must never be able to edit Tora.
    agent_workspace: str = field(default_factory=lambda: (
        _env("TORA_AGENT_WORKSPACE", "").strip()
    ))
    # safe | low | medium | high — the highest RiskLevel an agent may run
    # without a human clicking Approve. See agents/core RiskLevel.
    agent_auto_risk: str = field(default_factory=lambda: (
        _env("TORA_AGENT_AUTO_RISK", "medium").strip().lower() or "medium"
    ))
    # Show the plan and wait for Approve before any step runs.
    agent_plan_approval: bool = field(default_factory=lambda: (
        _env("TORA_AGENT_PLAN_APPROVAL", "0").strip() in ("1", "true", "yes", "on")
    ))
    # 0 = unlimited. Above this, tasks queue instead of starting.
    agent_max_concurrent: int = field(default_factory=lambda: _int(
        _env("TORA_AGENT_MAX_CONCURRENT", "3"), 3
    ))
    # Per-task limits, 0 = unlimited. An agent that cannot finish should
    # stop, not run until the credit card does.
    agent_wall_clock_s: int = field(default_factory=lambda: _int(
        _env("TORA_AGENT_WALL_CLOCK_S", "1800"), 1800
    ))
    agent_max_tokens: int = field(default_factory=lambda: _int(
        _env("TORA_AGENT_MAX_TOKENS", "300000"), 300000
    ))

    def set_agent_workspace(self, path: str) -> None:
        path = (path or "").strip()
        self.agent_workspace = path
        os.environ["TORA_AGENT_WORKSPACE"] = path
        self._write_env("TORA_AGENT_WORKSPACE", path)

    def set_agent_auto_risk(self, level: str) -> None:
        level = (level or "medium").strip().lower()
        if level not in ("safe", "low", "medium", "high"):
            level = "medium"
        self.agent_auto_risk = level
        os.environ["TORA_AGENT_AUTO_RISK"] = level
        self._write_env("TORA_AGENT_AUTO_RISK", level)

    def set_agent_plan_approval(self, on: bool) -> None:
        self.agent_plan_approval = bool(on)
        value = "1" if on else "0"
        os.environ["TORA_AGENT_PLAN_APPROVAL"] = value
        self._write_env("TORA_AGENT_PLAN_APPROVAL", value)

    def set_agent_wall_clock_s(self, seconds: int) -> None:
        seconds = max(0, int(seconds))
        self.agent_wall_clock_s = seconds
        os.environ["TORA_AGENT_WALL_CLOCK_S"] = str(seconds)
        self._write_env("TORA_AGENT_WALL_CLOCK_S", str(seconds))

    def set_agent_max_tokens(self, tokens: int) -> None:
        tokens = max(0, int(tokens))
        self.agent_max_tokens = tokens
        os.environ["TORA_AGENT_MAX_TOKENS"] = str(tokens)
        self._write_env("TORA_AGENT_MAX_TOKENS", str(tokens))

    def set_agent_env(self, name: str, value: str) -> None:
        """Set one `TORA_*` agent variable live and persist it to .env.

        The agent layer names its own variables per provider
        (`TORA_OMNIROUTE_API_KEY`, `TORA_OMNIROUTE_BASE_URL`, …), so they
        cannot be enumerated in API_KEY_FIELDS the way the app's own two
        keys are. The prefix check is the guard: this writes agent
        settings, never arbitrary environment entries.
        """
        name = (name or "").strip().upper()
        if not name.startswith("TORA_") or not name.replace("_", "").isalnum():
            return
        value = (value or "").strip()
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
        self._write_env(name, value)

    def set_agent_max_concurrent(self, count: int) -> None:
        count = max(0, int(count))
        self.agent_max_concurrent = count
        os.environ["TORA_AGENT_MAX_CONCURRENT"] = str(count)
        self._write_env("TORA_AGENT_MAX_CONCURRENT", str(count))

    # ── Switches (APP-03) ────────────────────────────────────────────────
    #
    # The tray's tutor and privacy switches were only ever held in memory,
    # so every launch put privacy guard, OCR, web search, slow mode and the
    # rest back to their defaults - whatever the user had chosen. They are
    # saved here the way the voice and the mic mode already are.
    #
    # Quiz mode is deliberately not one of them: it is something you start,
    # not a preference, and restoring it would open the next session with a
    # quiz nobody asked for.

    # name -> (env var, default). Unannotated on purpose: Config is a
    # dataclass, and an annotated dict here would become a field.
    SWITCHES = {
        "web_search":    ("TORA_WEB_SEARCH", True),
        "slow_mode":     ("TORA_SLOW_MODE", False),
        "auto_advance":  ("TORA_AUTO_ADVANCE", True),
        "privacy_guard": ("TORA_PRIVACY_GUARD", True),
        "code_mode":     ("TORA_CODE_MODE", True),
        "journal":       ("TORA_JOURNAL", True),
        "ocr":           ("TORA_OCR", True),
        # Settings > Approvals (APP-14): agents and desktop actions go ahead
        # without the approval box. Off until the user turns it on.
        "auto_approve":  ("TORA_AGENT_AUTO_APPROVE", False),
        # Settings > Updates: a downloaded update installs by itself - the
        # next time HeyTora starts, or as it is quit (updates/service.py).
        "auto_update":   ("TORA_AUTO_UPDATE", True),
    }

    def switch(self, name: str) -> bool:
        """A saved switch's value, or its default."""
        env, default = self.SWITCHES[name]
        raw = os.environ.get(env, "").strip().lower()
        if raw in ("1", "true", "on", "yes"):
            return True
        if raw in ("0", "false", "off", "no"):
            return False
        return default

    def set_switch(self, name: str, on: bool) -> None:
        """Change a switch now, and for every launch after this one."""
        env, _ = self.SWITCHES[name]
        value = "1" if on else "0"
        os.environ[env] = value
        self._write_env(env, value)

    # ── Microphone device ────────────────────────────────────────────────
    #
    # Windows numbers audio devices by their place in a list, and the list
    # moves whenever a headset, a Bluetooth device or a monitor comes or goes.
    # A saved number alone ended up pointing at a monitor's SPEAKERS ("Not an
    # input device: DELL ST2220L") and every ask failed with a mic error. So
    # the name is saved with it, and the number is looked up again each time.

    def mic_device(self) -> Optional[int]:
        """The input device to open now, or None for the system default."""
        name = os.environ.get("MIC_DEVICE_NAME", "").strip()
        index = self.mic_device_index
        if index is None and not name:
            return None
        try:
            import sounddevice as sd
            devices = list(sd.query_devices())
        except Exception:  # noqa: BLE001 - no audio stack: let the default try
            return None
        inputs = {i for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0}
        if name:
            if index in inputs and devices[index].get("name") == name:
                return index
            same = [i for i in sorted(inputs) if devices[i].get("name") == name]
            return same[0] if same else None
        return index if index in inputs else None

    def set_mic_device(self, index: Optional[int]) -> None:
        """Listen on this device from now on (None or <0: the system default)."""
        name = ""
        if index is not None and index >= 0:
            try:
                import sounddevice as sd
                name = sd.query_devices(index).get("name", "")
            except Exception:  # noqa: BLE001
                name = ""
        else:
            index = None
        self.mic_device_index = index
        for env, value in (("MIC_DEVICE_INDEX", "" if index is None else str(index)),
                           ("MIC_DEVICE_NAME", name)):
            if value:
                os.environ[env] = value
            else:
                os.environ.pop(env, None)
            self._write_env(env, value)

    # ── Appearance ───────────────────────────────────────────────────────

    def flow_dock(self) -> str:
        """Where the flow bar sits (ui/flow_bar.py): "edge|place|monitor"."""
        return os.environ.get("TORA_FLOW_DOCK", "").strip()

    def set_flow_dock(self, dock: str) -> None:
        """Dragged to another side: there, for every launch after this one."""
        os.environ["TORA_FLOW_DOCK"] = dock
        self._write_env("TORA_FLOW_DOCK", dock)

    def cursor_colour(self) -> str:
        """The colour key Tora's cursor wears (ui/tokens.CURSOR_COLOURS)."""
        return os.environ.get("TORA_CURSOR_COLOUR", "").strip().lower() or "blue"

    def set_cursor_colour(self, key: str) -> None:
        key = (key or "blue").strip().lower()
        os.environ["TORA_CURSOR_COLOUR"] = key
        self._write_env("TORA_CURSOR_COLOUR", key)

    def set_hotkey(self, combo: str) -> None:
        """The push-to-talk shortcut ("ctrl+win", "ctrl+alt+space"), kept."""
        combo = "+".join(p.strip().lower() for p in combo.split("+") if p.strip())
        self.hotkey = combo
        os.environ["TORA_HOTKEY"] = combo
        self._write_env("TORA_HOTKEY", combo)

    def listen_sound(self) -> str:
        """The earcon when Tora starts listening (ui/sounds.SOUNDS), or "off"."""
        return os.environ.get("TORA_LISTEN_SOUND", "").strip().lower() or "bloop"

    def set_listen_sound(self, key: str) -> None:
        key = (key or "off").strip().lower()
        os.environ["TORA_LISTEN_SOUND"] = key
        self._write_env("TORA_LISTEN_SOUND", key)

    def theme(self) -> str:
        """"system" (follow Windows), "light" or "dark" - ui/tokens.py."""
        raw = os.environ.get("TORA_THEME", "").strip().lower()
        return raw if raw in ("system", "light", "dark") else "system"

    def set_theme(self, mode: str) -> None:
        mode = mode if mode in ("system", "light", "dark") else "system"
        os.environ["TORA_THEME"] = mode
        self._write_env("TORA_THEME", mode)

    def sidebar_collapsed(self) -> bool:
        """The app window's sidebar shut to icons (ui/app_window.py)."""
        return os.environ.get("TORA_SIDEBAR", "").strip().lower() == "shut"

    def set_sidebar_collapsed(self, shut: bool) -> None:
        value = "shut" if shut else "open"
        os.environ["TORA_SIDEBAR"] = value
        self._write_env("TORA_SIDEBAR", value)

    # ── Microphone mode ──────────────────────────────────────────────────

    def ambient_mic(self) -> bool:
        """True when the mic should stay open and scan for the wake word."""
        return self.mic_mode == "ambient"

    def set_mic_mode(self, mode: str) -> None:
        """Persisted switch between hotkey-only and always-listening."""
        mode = "ambient" if mode == "ambient" else "hotkey"
        self.mic_mode = mode
        os.environ["TORA_MIC_MODE"] = mode
        self._write_env("TORA_MIC_MODE", mode)

    # ── Providers ────────────────────────────────────────────────────────
    #
    # There is one. These remain as methods because the panel, tray, journal
    # and diagnostics all ask which provider is active, and a constant is a
    # smaller change than rewriting every caller to stop asking.

    def llm_provider(self) -> str:
        return "gemini"

    def available_llm_providers(self) -> list[str]:
        return ["gemini"]

    def stt_provider(self) -> str:
        return "gemini"

    def tts_provider(self) -> str:
        return "gemini"

    def search_provider(self) -> str:
        return "tavily" if self.tavily_api_key else "duckduckgo"

    def has_key(self) -> bool:
        """Can Tora reach Gemini at all - with the user's own key, or with a
        signed-in HeyTora account? Tora cannot do anything without one."""
        if self.access_mode() == "own_key":
            return bool(self.google_api_key)
        try:
            from account.service import account
            return account().signed_in()
        except Exception:  # noqa: BLE001 - no account support: no access
            return False

    # ── HeyTora account or own key (2026-10-01) ──────────────────────────
    #
    # "account": Gemini through HeyTora's server, on the plan the user picked
    #            on the website. The business key lives on that server and
    #            never reaches this machine - a key shipped in an app is a key
    #            anyone can read off the wire.
    # "own_key": the user's own Gemini key, straight to Google, no limits
    #            from HeyTora.
    ACCESS_MODES = ("account", "own_key")

    def access_mode(self) -> str:
        """Saved as TORA_ACCESS. Unset, an own key wins if there is one, so
        a developer's .env keeps working exactly as before."""
        raw = os.environ.get("TORA_ACCESS", "").strip().lower()
        if raw in self.ACCESS_MODES:
            return raw
        return "own_key" if self.google_api_key else "account"

    def set_access_mode(self, mode: str) -> None:
        if mode not in self.ACCESS_MODES:
            raise ValueError(f"access mode is one of {self.ACCESS_MODES}")
        os.environ["TORA_ACCESS"] = mode
        self._write_env("TORA_ACCESS", mode)

    # ── Who the user is (first launch) ───────────────────────────────────

    def profile(self) -> tuple[str, list[str]]:
        """(name, what they use their PC for) from the first launch."""
        name = os.environ.get("TORA_USER_NAME", "").strip()
        uses = [u.strip() for u in os.environ.get("TORA_USER_USES", "").split(",") if u.strip()]
        return name, uses

    def set_profile(self, name: str, uses: list[str]) -> None:
        name = " ".join((name or "").split())[:40]
        joined = ",".join(u.replace(",", " ").strip() for u in uses if u and u.strip())[:300]
        for env, value in (("TORA_USER_NAME", name), ("TORA_USER_USES", joined)):
            os.environ[env] = value
            self._write_env(env, value)

    def realtime(self) -> bool:
        return self.conversation_mode != "cascade"

    def set_conversation_mode(self, mode: str) -> None:
        mode = "cascade" if mode == "cascade" else "realtime"
        self.conversation_mode = mode
        os.environ["TORA_CONVERSATION"] = mode
        self._write_env("TORA_CONVERSATION", mode)

    def set_voice(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        self.voice = name
        os.environ["GEMINI_TTS_VOICE"] = name
        self._write_env("GEMINI_TTS_VOICE", name)

    # ── API key management ───────────────────────────────────────────────
    #
    # Keys used to be .env-only, which meant a packaged install had no way to
    # enter one short of hand-editing a file next to HeyTora.exe. These back
    # the Settings → API Keys dialog.

    # env var name → (dataclass attribute, human label)
    API_KEY_FIELDS = {
        "GOOGLE_API_KEY": ("google_api_key", "Google (Gemini)"),
        "TAVILY_API_KEY": ("tavily_api_key", "Tavily (web search)"),
    }

    @staticmethod
    def env_path() -> Path:
        """The .env HeyTora reads and writes — next to HeyTora.exe when frozen."""
        return _HERE / ".env"

    def get_api_key(self, env_var: str) -> str:
        attr = self.API_KEY_FIELDS.get(env_var, (None, None))[0]
        return (getattr(self, attr, None) or "") if attr else ""

    def set_api_key(self, env_var: str, value: str) -> None:
        """Set (or clear) one key: live in this process + persisted to .env."""
        if env_var not in self.API_KEY_FIELDS:
            return
        value = (value or "").strip()
        attr = self.API_KEY_FIELDS[env_var][0]

        setattr(self, attr, value or None)
        if value:
            os.environ[env_var] = value
        else:
            os.environ.pop(env_var, None)
            # GEMINI_API_KEY is read as an alias at startup; leaving it set
            # would silently resurrect a key the user just cleared.
            if env_var == "GOOGLE_API_KEY":
                os.environ.pop("GEMINI_API_KEY", None)

        self._write_env(env_var, value)

    def _write_env(self, key: str, value: str) -> None:
        """Upsert KEY=value in .env, removing the line entirely when cleared."""
        path = self.env_path()
        try:
            lines = (
                path.read_text(encoding="utf-8").splitlines(keepends=True)
                if path.exists() else []
            )
            out, found = [], False
            for line in lines:
                stripped = line.lstrip()
                if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                    found = True
                    if value:
                        out.append(f"{key}={value}\n")
                    # cleared → drop the line
                    continue
                out.append(line)

            if value and not found:
                if out and not out[-1].endswith("\n"):
                    out.append("\n")
                out.append(f"{key}={value}\n")

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(out), encoding="utf-8")
        except Exception:
            pass  # runtime value still applied; persistence is best-effort

    # ── Summary ──────────────────────────────────────────────────────────

    def describe(self) -> dict:
        """Human-readable summary of what's active, for the setup panel."""
        from ai.gemini import Models
        return {
            "llm": "gemini",
            "stt": Models.STT,
            "tts": Models.TTS,
            "search": self.search_provider(),
            "conversation": "realtime" if self.realtime() else "cascade",
            "realtime_model": Models.REALTIME,
            "planner_model": Models.PLANNER,
            "vision_fast": Models.VISION_FAST,
            "vision_deep": Models.VISION_DEEP,
            "embed_model": Models.EMBED,
            "voice": self.voice,
            "key": "set" if self.has_key() else "MISSING",
        }


# Singleton
cfg = Config()
