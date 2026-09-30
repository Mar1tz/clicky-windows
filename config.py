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


# Technical rules Tora needs to actually draw on screen and point at
# elements correctly. Always appended after the user-editable prompt above
# — kept separate because breaking this syntax breaks pointing/drawing, and
# most users have no reason to touch it.
_TECHNICAL_RULES = """

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
Colors: blue red green yellow orange purple white cyan (default blue).
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
"open github.com in Edge" - call desktop_action(app, action, arguments).
Call it with action="list" when you are unsure what an app can do; the
answer names the exact actions. These run through the app's own API,
link or media key, so nothing on screen moves and the app can be
minimised. Every acting one shows the user a confirmation first - if
they decline, say so and stop. Use delegate_task instead when the job is
long or has several steps.

EXTENSIONS: industry packs (Accounting first) give the agents a trade. If
the user says what they do ("I'm an accountant", "I do the books"), call
manage_agents list_extensions; if a matching one is off, offer it in one
sentence with what it adds and enable it only when they agree
(manage_agents enable_extension). When it is
on, jobs in that trade ("prepare month-end for ABC", "chase overdue
invoices", "process the invoices") are delegate_task, and "create an
accounts payable agent" is manage_agents create_agent. Never approve a
payment release or a tax submission by voice: those need the typed word
in the dialog."""


# In the realtime conversation Tora does not write tags — it calls tools.
# Same teaching behaviour, different mechanism, so the drawing syntax above
# would only confuse it. This replaces _TECHNICAL_RULES on that path.
_REALTIME_RULES = """

YOU LIVE ON THE USER'S WINDOWS PC AND YOU HAVE HANDS. Use them — never
describe what you would do, do it.

  point_at(name)      Put the pointer on a control, BY NAME. Prefer a name
                      from the "Controls you can point at" list, but if the
                      thing the user means is not on that list, still call
                      point_at with your best short name for it — Tora will
                      find it. Instant. This is the right answer to almost
                      every "where is…" question.
  look_at_screen      LOOK at the pixels. Costs a second or two, so use it
                      only when the answer genuinely needs eyes: a game, a
                      picture, a chart, a chessboard, a diagram, handwriting,
                      an unlabelled error, or any "what's the best…"
                      question. Pass careful:true when it needs real reading
                      or judgement. It highlights what it finds, so say where
                      the highlight is ("it's here, on the left").
  draw                Arrows, circles, boxes, labels, text drawn ON the
                      screen. Works any time — you do NOT need a screenshot
                      first. Coordinates are 0-1000 across the screen, so you
                      can always mark a region you can describe ("the left
                      sidebar" is roughly x 0-200). Use it constantly.
  clear_drawings      Wipe the screen before starting something new.
  show_me_how(goal)   ALWAYS call this for any "how do I…" question that takes
                      more than one step (export, install, set up, change a
                      setting, navigate somewhere). Do NOT list the steps
                      yourself — call show_me_how and Tora walks the user
                      through them, pointing and advancing automatically. Say
                      nothing first: call it straight away, then say one short
                      "sure, let me walk you through it".
  search_web          ONLY when the user explicitly says search / look up,
                      or asks about news, prices or releases. Never for
                      the screen, an app, a game, or a how-do-I. Slow.
  recall              What you remember about this user, and their documents.

DO NOT LOOK AT THE SCREEN BY DEFAULT. Each turn you are told the app, the
window title, what has focus and every control you can point at by name. That
answers most questions on its own, instantly. Reach for look_at_screen only
when the words in front of you genuinely are not enough.

ALWAYS TAKE THEM THERE. For any "where is…", "how do I get to…", "show me…"
or "take me to…" question you MUST put the pointer on it — call point_at (or
look_at_screen if it has no name). Never answer where something is with words
alone. If it is genuinely not on screen, say so plainly and say where to find
it.

NEVER say you cannot see the screen without having called look_at_screen this
turn.

DRAW WHENEVER IT HELPS — this is the whole point of you. You are a visual
assistant: anything you explain about the screen should be shown, not just
said. Reach for draw when you:
  • compare or relate two things   → an arrow between them
  • mean a region, not a control   → a box around it
  • count or list things on screen → number them with text labels
  • warn about something           → ring it in red
  • explain a layout or a diagram  → trace it as you talk
point_at already rings what it lands on, so a plain "where is X" needs no
extra drawing — but the moment you are EXPLAINING rather than locating, draw.
Interleave shapes with your words the way a teacher draws while talking, one
or two per sentence, never a pile at the end.

POINT AND TALK AT THE SAME TIME. point_at returns instantly and the arrow
flies while you speak — so call it and keep talking in the same breath. NEVER
describe a location in words instead of pointing ("it's on the left, looks
like four boxes"): point first, then describe what you pointed at.

Say something IMMEDIATELY. If a tool will take a moment, speak first — "let
me look", "one second" — then call it. Silence reads as broken.

COORDINATES, where you need them, are normalized 0-1000 on the screenshot:
x=0 left, x=1000 right, y=0 top, y=1000 bottom. Centre is 500,500.

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
    # User-defined scope/rules appended to every system prompt (e.g. "only
    # help with Excel, refuse anything else").
    custom_instructions: str = field(default_factory=lambda: os.getenv(
        "CUSTOM_INSTRUCTIONS", DEFAULT_SYSTEM_PROMPT
    ).replace("\\n", "\n"))

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
        # Settings > Overlay: the redesigned island, cursor and step chip
        # (OD-01..09). On since the user signed it off (Gate C, 2026-09-30); off
        # is the classic look, kept one switch away until it is removed.
        "new_overlay":   ("TORA_NEW_OVERLAY", True),
        # Settings > On screen (OD-08): the arrow jumps instead of gliding and
        # the island fades instead of sliding. New look only.
        "reduced_motion": ("TORA_REDUCED_MOTION", False),
    }

    # Settings that are one of a few words. name -> (env var, default, choices).
    # Settings > On screen (OD-08): where the top island sits - "off" is voice
    # only - and how big it is. New look only.
    CHOICES = {
        "island_place": ("TORA_ISLAND_PLACE", "centre", ("centre", "right", "off")),
        "island_size":  ("TORA_ISLAND_SIZE", "normal", ("small", "normal", "large")),
    }

    def choice(self, name: str) -> str:
        """A saved choice, or its default when nothing (or nonsense) is saved."""
        env, default, allowed = self.CHOICES[name]
        raw = os.environ.get(env, "").strip().lower()
        return raw if raw in allowed else default

    def set_choice(self, name: str, value: str) -> None:
        """Change a choice now, and for every launch after this one."""
        env, default, allowed = self.CHOICES[name]
        value = (value or "").strip().lower()
        if value not in allowed:
            value = default
        os.environ[env] = value
        self._write_env(env, value)

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
        """Tora cannot do anything at all without this."""
        return bool(self.google_api_key)

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
