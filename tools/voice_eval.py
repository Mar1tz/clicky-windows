"""The voice test bench (docs/SHOW_DONT_TELL_POLISH.md, SP-02).

The look is measured by tools/look_eval.py; this measures what the VOICE does
with it. Nothing had checked that since wave 1: that the live model calls
look_at_screen for "what should I do?", says the one `say` line and stops, and
never narrates what the look saw. It was never tested live because that needs
someone speaking - so here the question is typed instead.

It opens the app's own live session (RealtimeConversation, the app's system
prompt and tool list), sends the per-turn screen line the app would send, then
the question as text. The audio is thrown away - nothing plays - and the words
are kept from the output transcription. Every tool answers from a recorded
result: no screen is captured, nothing opens, nothing is pointed at.

Each question is checked for:

    tool         the right tool was called (or none, for small talk)
    short        at most two sentences were said
    no narrating no "let me" after the tool answered, no counting, and none
                 of the words that were only in the look's readings
    the line     the `say` line (or its key words) made it out

with the time to the first word and to the answer, and - once SP-03 reads the
live server's usage - the tokens and cost of the whole answer.

PAID: about $0.15 a run (15 questions; 12 cost $0.116 on 6 Oct) - each tool round re-bills the ~3,900-token
prompt - on the owner's own key (TORA_ACCESS=own_key), never the plan.
--max-dollars stops it (default $0.25).

    python tools/voice_eval.py --dry                   # the questions and checks, free
    python tools/voice_eval.py                         # all of them
    python tools/voice_eval.py --only chess,how_to
    python tools/voice_eval.py --against <report.json> # and what changed since that run
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402,F401 - loads .env
os.environ["TORA_ACCESS"] = "own_key"          # straight to Google with the owner's key, not the plan

OUT = Path(tempfile.gettempdir()) / "tora-voice-eval"

@dataclass
class Case:
    name: str
    question: str
    title: str                      # the window title
    kind: str                       # web | named | opaque - tutor.look_hint's app classes
    controls: list[str]             # what point_at can name
    expect: set[str]                # the first tool called; {"none"} = no tool at all
    look: dict = field(default_factory=dict)       # look_at_screen's recorded result
    readings: list[str] = field(default_factory=list)   # in the look, not in `say`: never said
    must: list[str] = field(default_factory=list)  # any one of these is said
    forbid: set[str] = field(default_factory=lambda: {"search_web", "delegate_task"})
    args: dict = field(default_factory=dict)       # some call carries these (SP-16)


def _look(say: str, *drawn: tuple[str, str, str]) -> dict:
    """A look_at_screen result shaped exactly as _tool_look_at_screen returns it."""
    return {"say": say,
            "drawn": [f"{kind}: {label}" for kind, label, _ in drawn],
            "highlights": [{"label": label, "tag": tag} for _, label, tag in drawn]}


BROWSER = ["Back", "Forward", "Reload", "Address and search bar", "New Tab", "Bookmarks"]
WORD = ["File", "Home", "Insert", "Layout", "Bold (Ctrl+B)", "Italic (Ctrl+I)", "Underline (Ctrl+U)",
        "Font", "Font Size", "Styles", "Find", "Replace", "Comments", "Editing", "Save"]

CASES = [
    Case("chess", "what's the best move here?", "Play chess online - lichess.org - Google Chrome", "web",
         BROWSER, {"look_at_screen"},
         look=_look("Knight to f7 - it forks the king and the queen.",
                    ("arrow", "Nf7", "[ARROW:560,610->640,300:tora]")),
         readings=["pawn", "bishop", "rook", "eight", "seven"],
         must=["f7", "knight"]),
    Case("click_next", "what should I click next?", "Checkout - Northwind Store - Google Chrome", "web",
         BROWSER, {"look_at_screen"},
         look=_look("Click Continue to payment.",
                    ("rect", "Continue to payment", "[RECT:620,780,880,840:tora]")),
         must=["continue"]),
    Case("pick_one", "which one of these should I pick?", "Desk fans - Northwind Store - Google Chrome", "web",
         BROWSER, {"look_at_screen"},
         look=_look("Pick the Arctic Fan - it is the best rated for the price.",
                    ("rect", "Arctic Fan", "[RECT:380,300,560,620:tora]")),
         must=["arctic"]),
    Case("misleading", "what should I do here?", "Setup wizard", "opaque", [], {"look_at_screen"},
         look=_look("Click Continue at the bottom right.",
                    ("rect", "Continue", "[RECT:820,880,960,930:tora]"),
                    ("circle", "Back", "[CIRCLE:700,905,30:Back:tora]"),
                    ("circle", "Help", "[CIRCLE:60,905,30:Help:tora]"),
                    ("circle", "Licence terms link", "[CIRCLE:300,600,40:Licence terms:tora]")),
         readings=["back", "help", "licence", "license", "terms", "circle"],
         must=["continue"]),
    Case("error", "what does this error mean?", "Excel", "opaque", [], {"look_at_screen"},
         look=_look("The file is open in another program, so Excel can only open it read-only.",
                    ("rect", "error message", "[RECT:300,400,700,560:tora]")),
         must=["another program", "read-only", "read only", "open somewhere", "open in"]),
    Case("tictactoe", "where should I go to win?", "Tic Tac Toe - Google Chrome", "web",
         BROWSER, {"look_at_screen"},
         look=_look("Take the top right corner to win.",
                    ("rect", "top right square", "[RECT:600,200,700,300:tora]")),
         must=["top right", "top-right", "corner"]),
    Case("where_named", "where is the bold button?", "Report.docx - Word", "named", WORD,
         {"point_at"}, must=["bold", "home", "top", "there", "here"]),
    Case("where_unlisted", "where do I share this?", "Report.docx - Word", "named", WORD,
         {"point_at", "look_at_screen"},
         look=_look("Share is at the top right, next to Comments.",
                    ("rect", "Share", "[RECT:900,40,960,80:tora]"))),
    Case("where_web", "where's the sign in button?", "Northwind Store - Google Chrome", "web",
         BROWSER + ["Sign in", "Cart", "Search products"], {"point_at"}, must=["sign in", "top", "there", "here"]),
    Case("how_to", "how do I export this as a PDF?", "Report.docx - Word", "named", WORD,
         {"show_me_how"}, must=["walk", "through", "show"]),
    Case("thanks", "thanks, that's perfect", "Report.docx - Word", "named", WORD, {"none"},
         forbid={"search_web", "delegate_task", "look_at_screen", "point_at", "draw", "show_me_how"}),
    Case("skip_song", "skip this song", "Spotify Premium", "opaque", [], {"desktop_action"},
         forbid={"search_web", "delegate_task", "look_at_screen"}),
    # SP-16: the long tool descriptions hold field fixes - a song is Spotify,
    # never a web page; building is an agent's job; "how's it going" asks one.
    Case("play_song", "play Blinding Lights", "Report.docx - Word", "named", WORD, {"desktop_action"},
         forbid={"search_web", "delegate_task", "look_at_screen"}, args={"app": "spotify"}),
    Case("build_app", "build me a React app called MyShop", "Report.docx - Word", "named", WORD,
         {"delegate_task"}, forbid={"search_web", "look_at_screen", "desktop_action"}),
    Case("agent_status", "how's my build going?", "Report.docx - Word", "named", WORD, {"manage_agents"},
         forbid={"search_web", "delegate_task", "look_at_screen"}, args={"action": "status"},
         must=["install", "running", "working", "going"]),
]

# What every other tool answers - shaped as the app's handlers answer.
CANNED = {
    "point_at": lambda a: {"found": True, "pointed_at": a.get("name", "")},
    "draw": lambda a: {"ok": True, "drawn": 1},
    "clear_drawings": lambda a: {"ok": True},
    "show_me_how": lambda a: {"started": True, "instruction": (
        "Say ONE short sentence RIGHT NOW - just that you'll walk them through it, nothing "
        "else. Do NOT say the first step, or any step, or what they should click: Tora speaks "
        "step one out loud a second from now, and if you say it too they hear the same "
        "instruction twice. 'Sure, let me walk you through it.' and then stop talking.")},
    "guide_step": lambda a: {"ok": True},
    "search_web": lambda a: {"results": "Nothing useful found."},
    "recall": lambda a: {"memories": []},
    "delegate_task": lambda a: {"started": True, "title": a.get("title") or "Create React app MyShop",
                                "steps": ["Create the MyShop folder", "Scaffold a React app with Vite",
                                          "Install the packages"]},
    "desktop_action": lambda a: {"ok": True, "result": "Playing Blinding Lights by The Weeknd."
                                 if a.get("action") in ("play", "play_playlist") else "Skipped to the next track."},
    "read_skill": lambda a: {"error": "no such skill", "skills": []},
    "manage_agents": lambda a: {"tasks": [{"title": "Create React app MyShop", "state": "running",
                                           "step": "Install the packages (3 of 3)"}]},
}

# Going back for another go once the answer is in ("let me recount"). Not
# "let me know", and not show_me_how's own "let me walk you through it".
_LET_ME = re.compile(r"\blet me (re)?(count|check|look|see|think|read|describe|go over|explain)", re.I)
_COUNT = re.compile(r"\b(\d+|two|three|four|five|six|seven|eight|nine|ten)\s+(pieces?|pawns?|items?|buttons?|"
                    r"options?|things?|products?|circles?|arrows?|boxes|squares?|links?)\b", re.I)


def sentences(text: str) -> int:
    """Spoken sentences: ends of . ! ? that are followed by more words."""
    text = re.sub(r"\b([A-Za-z])\.(?=[A-Za-z]\.)", r"\1", text.strip())   # e.g. -> eg
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text) if re.search(r"\w", p)]
    return len(parts)


def context_line(case: Case) -> str:
    """The [context] line the app sends as the question starts (_screen_context)."""
    from tutor import look_hint
    line = f'Screen: "{case.title}"; focused: document.'
    if case.controls:
        line += " Controls you can point at by name: " + ", ".join(case.controls) + "."
    line += look_hint(case.kind)
    return f"[context] Today is {datetime.now():%A, %B %d, %Y}. {line}"


def check(case: Case, said: str, answer: str, tools: list[str], args: list[dict] = ()) -> dict:
    """Every check, by name: True passed."""
    first = tools[0] if tools else "none"
    low = said.lower()
    look = case.look.get("say", "").lower()
    out = {
        "tool": first in case.expect,
        "no_forbidden": not (set(tools) & case.forbid),
        "short": 0 < sentences(said) <= 2,
        "no_let_me_after": not (tools and _LET_ME.search(answer)),
        "no_counting": not _COUNT.search(said),
        "no_readings": not [w for w in case.readings if w in low and w not in look],
        "the_line": not case.must or any(m in low for m in case.must),
        "args": not case.args or any(all(str(a.get(k, "")).lower() == v for k, v in case.args.items())
                                     for a in args),
        # Its own planning, spoken (6 Oct: "Does it use markdown? No. ...
        # **Finalize Output.**"). SP-18.
        "no_planning": not re.search(r"\*\*|(^|\n)\s*\*\s", said),
    }
    if "look_at_screen" in case.expect:
        # What the look drew IS the answer: no rings or labels on top of it.
        out["nothing_drawn_on_top"] = "draw" not in tools
    return out


# -- one question --------------------------------------------------------------------------------------------------------

class Mute:
    """PcmStreamPlayer, without the speaker: the bench never plays anything."""
    queued_seconds = 0.0
    latency = 0.0
    bytes_per_second = 48_000

    def start(self): pass
    def open(self): pass
    def feed(self, pcm): pass
    def clear(self): pass
    def stop(self): pass
    def is_open(self): return False


TRACE: list = []          # the wire, for the question being asked: tool calls, usage, turn ends


def trace_the_wire() -> None:
    """Note when the server sends usage against its turn ends - SP-03 folds a
    generation's usage into the turn at turnComplete, and this shows it can."""
    from ai import gemini_live
    events = gemini_live.LiveSession.events

    async def traced(self):
        async for ev in events(self):
            if ev.kind in ("usage", "tool_call", "turn_complete"):
                TRACE.append((round(time.monotonic(), 2), ev.kind,
                              ev.raw if ev.kind == "usage" else [c.name for c in ev.calls]))
            yield ev
    gemini_live.LiveSession.events = traced


# What config._REALTIME_RULES says about speaking before a slow tool, and the
# variants SP-17 measured against it (--filler). Only the bench's prompt is
# changed. "b" is what shipped; "speak" is the rule before it.
FILLER_RULE = ("Call a slow tool straight away. If you speak before it, two words at\n"
               'most - "one sec" - never a sentence.')
FILLERS = {
    "speak": ('Say something IMMEDIATELY. If a tool will take a moment, speak first - "let\n'
              'me look", "one second" - then call it. Silence reads as broken.'),
    "a": ("CALL A SLOW TOOL FIRST. When a question needs look_at_screen or another\n"
          "tool, call it before you say anything - Tora shows them she is on it -\n"
          "then answer from what it returns."),
}
LOOK_SECONDS = 0.0        # --look-seconds: how long the recorded look takes


async def ask(case: Case, system_prompt: str, *, timeout: float = 30.0) -> dict:
    """One question in a fresh session, as the first question of a conversation."""
    from ai.gemini_realtime import RealtimeConversation

    TRACE.clear()
    # A cut hides the leak from the transcript, so it is counted from the log.
    cuts: list[str] = []

    class Cuts(logging.Handler):
        def emit(self, record):
            if record.getMessage().startswith("the voice began to speak its own planning"):
                cuts.append(record.getMessage())
    cut_log = Cuts()
    logging.getLogger("tora.realtime").addHandler(cut_log)
    events: list[tuple[float, str, object]] = []          # (t, kind, what)
    calls: list[dict] = []
    usage: list[dict] = []

    def tool(name):
        async def run(args: dict) -> dict:
            calls.append({"name": name, "args": args})
            events.append((time.monotonic(), "tool", name))
            if name == "look_at_screen":
                await asyncio.sleep(LOOK_SECONDS)
                return dict(case.look) or {"say": "I can't make that out.", "drawn": [], "highlights": []}
            return CANNED[name](args)
        return run

    conv = RealtimeConversation(
        tools={n: tool(n) for n in ["look_at_screen", *CANNED]},
        on_said=lambda t: events.append((time.monotonic(), "said", t)),
        on_turn_end=lambda turn: (events.append((time.monotonic(), "end", turn)),
                                  usage.append(getattr(turn, "usage", None) or {})),
    )
    conv._player = Mute()
    if not await conv.connect(system_prompt):
        return {"name": case.name, "error": "could not open a live session"}
    try:
        await conv.send_context(context_line(case))
        t0 = time.monotonic()
        await conv._session.send_text(case.question, turn_complete=True)
        # The answer is in when a turn has ended after speech that followed the
        # last tool result - and nothing else has started for a moment after.
        deadline = t0 + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            last_tool = max((t for t, k, _ in events if k == "tool"), default=0.0)
            spoke = [t for t, k, _ in events if k == "said" and t > last_tool]
            ended = [t for t, k, _ in events if k == "end" and spoke and t > spoke[0]]
            if ended and time.monotonic() - max(t for t, _, _ in events) > 0.8:
                break
    finally:
        logging.getLogger("tora.realtime").removeHandler(cut_log)
        # Usage the server sent after the last turn ended, or for a turn that
        # never did, is still the answer's.
        usage += [conv._gen_usage, conv._turn.usage]
        await conv.close()

    said_parts = [(t, w) for t, k, w in events if k == "said"]
    said = "".join(w for _, w in said_parts).strip()
    last_tool = max((t for t, k, _ in events if k == "tool"), default=0.0)
    answer = "".join(w for t, w in said_parts if t > last_tool).strip()
    first_word = round(said_parts[0][0] - t0, 2) if said_parts else None
    after = [t for t, _ in said_parts if t > last_tool]
    answer_s = round(after[0] - t0, 2) if after else None
    tools = [c["name"] for c in calls]
    checks = check(case, said, answer, tools, [c["args"] for c in calls])
    # When the first tool ran - what decides when a look's drawing appears -
    # and whether anything was said before it (SP-17).
    first_tool = min((t for t, k, _ in events if k == "tool"), default=None)
    tool_s = round(first_tool - t0, 2) if first_tool is not None else None
    filler = bool(first_tool is not None and said_parts and said_parts[0][0] < first_tool)
    return {"name": case.name, "question": case.question, "ok": all(checks.values()),
            "failed": [k for k, v in checks.items() if not v], "tools": tools,
            "args": [c["args"] for c in calls], "said": said, "answer": answer,
            "first_word_s": first_word, "tool_s": tool_s, "filler": filler,
            "answer_s": answer_s, "usage": [u for u in usage if u], "planning_cut": len(cuts),
            "trace": [(round(t - t0, 2), k, w) for t, k, w in TRACE]}


def priced(row: dict) -> float:
    """The answer's cost, from the usage the live server reported (SP-03)."""
    from ai.gemini_live import live_cost_usd
    return sum(live_cost_usd(u) for u in row.get("usage", []))


def compare(before: dict, after: dict) -> None:
    was = {r["name"]: r for r in before.get("rows", [])}
    for r in after["rows"]:
        b = was.get(r["name"])
        if b is None:
            continue
        if b.get("ok") != r.get("ok"):
            print(f"  {r['name']:15} {'PASS' if b.get('ok') else 'FAIL'} -> {'PASS' if r.get('ok') else 'FAIL'}"
                  f"  {r.get('failed') or ''}")
    for k in ("passed", "p50_answer_s", "avg_cost"):
        print(f"  {k:14} {before['summary'].get(k)} -> {after['summary'].get(k)}")


# FS-08: one long conversation, in one session. The field's voice context
# grew to 33k tokens an answer (9 Oct) - every generation re-bills all of
# it - while each SP-02 question starts blank. A prefilled earlier chat
# takes the context past the compression trigger, as a real session is;
# then six questions, two of which refer back.
CONVERSATION = [
    ("where is the bold button?", {"point_at"}, ["bold"]),
    ("and italic?", {"point_at"}, ["italic"]),
    ("where do I make the text bigger?", {"point_at"}, ["size", "font"]),
    ("what did I ask you about first, just now?", {"none"}, ["bold"]),
    ("thanks", {"none"}, []),
    ("show me that italic one again", {"point_at"}, ["italic"]),
]


def earlier_chat(tokens: int) -> list[dict]:
    """About `tokens` tokens of an earlier, unrelated chat, as real turns -
    user, then Tora - so the server's sliding window can drop the oldest
    ones, as it does in a real session. One big message it cannot: the
    session died on the first question at 8k."""
    apps = ["Excel", "Spotify", "Chrome", "Outlook", "Photoshop", "Teams", "Explorer", "Settings"]
    turns, n, size = [], 0, 0
    while size < tokens * 4:
        app = apps[n % len(apps)]
        ask = f"In {app}, where do I find the option for item {n}? I have looked everywhere for it."
        said = (f"In {app}, item {n} is under the menu at the top; I pointed at it. It is the third "
                f"entry, and it opens a small panel with its settings, where you can change it.")
        turns += [{"role": "user", "parts": [{"text": ask}]}, {"role": "model", "parts": [{"text": said}]}]
        size += len(ask) + len(said)
        n += 1
    return turns


async def converse(system_prompt: str, prefill_tokens: int) -> list[dict]:
    from ai.gemini_realtime import RealtimeConversation
    word = next(c for c in CASES if c.name == "where_named")
    rows: list[dict] = []
    turn: dict = {}

    def tool(name):
        async def run(args: dict) -> dict:
            turn.setdefault("tools", []).append(name)
            turn["last_tool"] = time.monotonic()
            if name == "look_at_screen":
                return {"say": "It's in the Home tab.", "drawn": [], "highlights": []}
            return CANNED[name](args)
        return run

    def said(text):
        turn.setdefault("said", []).append((time.monotonic(), text))

    def ended(t):
        turn.setdefault("usage", []).append(getattr(t, "usage", None) or {})
        turn["ended"] = time.monotonic()

    conv = RealtimeConversation(tools={n: tool(n) for n in ["look_at_screen", *CANNED]},
                                on_said=said, on_turn_end=ended)
    conv._player = Mute()
    assert await conv.connect(system_prompt), "could not open a live session"
    try:
        await conv._session._send({"clientContent": {"turns": earlier_chat(prefill_tokens), "turnComplete": False}})
        for question, expect, must in CONVERSATION:
            turn.clear()
            await conv.send_context(context_line(word))
            t0 = time.monotonic()
            await conv._session.send_text(question, turn_complete=True)
            while time.monotonic() < t0 + 30:
                await asyncio.sleep(0.1)
                spoke = [t for t, _ in turn.get("said", []) if t > turn.get("last_tool", 0)]
                if spoke and turn.get("ended", 0) > spoke[0] and time.monotonic() - turn["ended"] > 0.8:
                    break
            words = "".join(w for _, w in turn.get("said", [])).strip()
            tools = turn.get("tools", [])
            ok_tool = (tools[0] if tools else "none") in expect
            ok_words = not must or any(m in words.lower() for m in must)
            usage = [u for u in turn.get("usage", []) if u] + [u for u in (conv._gen_usage, conv._turn.usage) if u]
            cost = sum(live_cost_usd_(u) for u in usage)
            prompt = sum(int(u.get("promptTokenCount", 0)) for u in usage)
            rows.append({"question": question, "ok": ok_tool and ok_words, "tools": tools, "said": words,
                         "cost": round(cost, 5), "prompt_tokens": prompt})
            print(f"  {'PASS' if rows[-1]['ok'] else 'FAIL'} {question!r:45} {','.join(tools) or 'no tool':10} "
                  f"{prompt:6} in ${cost:.4f} | {words[:60]!r}")
    finally:
        await conv.close()
    return rows


def live_cost_usd_(usage: dict) -> float:
    from ai.gemini_live import live_cost_usd
    return live_cost_usd(usage)


async def main():
    ap = argparse.ArgumentParser(description="The voice test bench (SP-02).")
    ap.add_argument("--conversation", action="store_true",
                    help="FS-08: one long conversation in one session instead of the questions")
    ap.add_argument("--prefill", type=int, default=9000, help="tokens of earlier chat before it (FS-08)")
    ap.add_argument("--compress", default="", help="TRIGGER,TARGET tokens for the session (FS-08)")
    ap.add_argument("--dry", action="store_true", help="print the questions and checks, free")
    ap.add_argument("--only", default="", help="comma-separated case names")
    ap.add_argument("--max-dollars", type=float, default=0.25)
    ap.add_argument("--against", default="", help="an earlier report.json to compare with")
    ap.add_argument("--look-seconds", type=float, default=0.0,
                    help="the recorded look takes this long, as a real one does (SP-06: 2.2)")
    ap.add_argument("--filler", default="", choices=["", *FILLERS],
                    help="try a variant of the speak-before-a-tool rule (SP-17)")
    args = ap.parse_args()

    cases = CASES
    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        cases = [c for c in CASES if c.name in wanted]
    if args.dry:
        for c in cases:
            print(f"{c.name:15} {c.question!r:42} expects {'/'.join(sorted(c.expect))}")
            print(f"{'':15} {context_line(c)[:150]}")
        return

    from ai import gemini
    from companion_manager import _build_system_prompt
    assert gemini.own_key() and gemini.api_key(), "no own key in .env (GOOGLE_API_KEY)"
    # The app's prompt, less the owner's name and uses from the first launch,
    # so a run on any PC is compared with the same words.
    config.cfg.profile = lambda: ("", [])
    system_prompt = _build_system_prompt(language_code="en", realtime=True)
    global LOOK_SECONDS
    LOOK_SECONDS = args.look_seconds
    if args.filler:
        assert FILLER_RULE in system_prompt, "the rule's words changed - update FILLER_RULE"
        system_prompt = system_prompt.replace(FILLER_RULE, FILLERS[args.filler])
    trace_the_wire()
    OUT.mkdir(exist_ok=True)
    if args.conversation:
        from ai import gemini_realtime
        if args.compress:
            trig, target = (int(x) for x in args.compress.split(","))
            gemini_realtime.COMPRESSION_TRIGGER_TOKENS, gemini_realtime.COMPRESSION_TARGET_TOKENS = trig, target
        print(f"conversation: compression {gemini_realtime.COMPRESSION_TRIGGER_TOKENS} -> "
              f"{gemini_realtime.COMPRESSION_TARGET_TOKENS}, {args.prefill} tokens of earlier chat")
        conv_rows = await converse(system_prompt, args.prefill)
        total = sum(r["cost"] for r in conv_rows)
        print(f"passed {sum(r['ok'] for r in conv_rows)}/{len(conv_rows)}, avg ${total / len(conv_rows):.4f} "
              f"an answer, total ${total:.3f}")
        path = OUT / f"conversation-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps({"compress": [gemini_realtime.COMPRESSION_TRIGGER_TOKENS,
                                                 gemini_realtime.COMPRESSION_TARGET_TOKENS],
                                    "rows": conv_rows}, indent=1), encoding="utf-8")
        print("report:", path)
        return
    rows, spent = [], 0.0
    for c in cases:
        if spent >= args.max_dollars:
            print(f"stopped: ${spent:.3f} reached the ${args.max_dollars:.2f} cap")
            break
        r = await ask(c, system_prompt)
        r["cost"] = round(priced(r), 5)
        spent += r["cost"]
        rows.append(r)
        if r.get("error"):
            print(f"{c.name:15} ERROR {r['error']}")
            continue
        print(f"{c.name:15} {'PASS' if r['ok'] else 'FAIL'} {','.join(r['tools']) or 'no tool':28} "
              f"first {r['first_word_s']}s tool {r['tool_s']}s answer {r['answer_s']}s "
              f"{'filler ' if r['filler'] else ''}{'CUT ' if r['planning_cut'] else ''}"
              f"${r['cost']:.4f} | {r['said'][:80]!r}"
              + (f"  failed: {r['failed']}" if r["failed"] else ""))
    done = [r for r in rows if not r.get("error")]
    if not done:
        return
    times = sorted(r["answer_s"] for r in done if r["answer_s"] is not None) or [0]
    summary = {"passed": f"{sum(r['ok'] for r in done)}/{len(rows)}", "p50_answer_s": times[len(times) // 2],
               "planning_cut": sum(r["planning_cut"] for r in done),
               "avg_cost": round(spent / len(done), 5), "total_cost": round(spent, 4)}
    print(f"\npassed {summary['passed']}, answer p50 {summary['p50_answer_s']}s, "
          f"avg ${summary['avg_cost']:.4f}, total ${spent:.3f}")
    report = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "model": gemini.Models.REALTIME,
              "prompt_chars": len(system_prompt), "rows": rows, "summary": summary}
    path = OUT / f"report-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("report:", path)
    if args.against:
        print("\nsince", args.against)
        compare(json.loads(Path(args.against).read_text(encoding="utf-8")), report)


if __name__ == "__main__":
    asyncio.run(main())
