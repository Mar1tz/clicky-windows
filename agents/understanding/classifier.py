"""Intent classification: is this utterance a task to run, or a chat to answer?

The gate at the very front of the agent flow. "Create a folder called MyApp
and build a React foundation in it" must become a background Task; "what is a
React component?" must stay an ordinary answer. Getting this wrong in either
direction is jarring — a spurious task, or a request that quietly does
nothing — so the heuristic is deliberately conservative: a task needs BOTH an
imperative verb AND a concrete work object, and any question shape short-
circuits to chat.

Three classifiers:
  * HeuristicIntentClassifier — rules only, no network, always available.
  * ProviderIntentClassifier — asks an AgentProvider for strict JSON and
    falls back to the heuristic on any hiccup, so it is never worse than the
    rules alone.
  * TieredIntentClassifier — the one to use when a provider exists: rules
    when they are confident, the model only for the ambiguous middle. A
    provider call costs seconds, and this sits on the voice path in front
    of every single utterance, so paying it on "what is a React component?"
    would slow down the thing Tora is supposed to be fastest at.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

from agents.core import (
    AgentProvider, Intent, IntentClassifier, ProviderMessage, TaskKind,
)

log = logging.getLogger("agents.understanding")

# An action word...
_IMPERATIVE = re.compile(
    r"\b(create|build|make|set\s*up|setup|scaffold|generate|install|write|"
    r"refactor|initiali[sz]e|init|clean|tidy|reconcile|compare|organi[sz]e|sort|check|"
    r"analy[sz]e|summari[sz]e|update|fix|open|launch|play|skip|pause)\b",
    re.I,
)
# ...applied to a work object.
_OBJECT = re.compile(
    r"\b(folder|directory|dir|project|app|application|file|files|repo|repository|"
    r"script|website|web\s*site|api|component|spreadsheet|workbook|sheets?|csv|xlsx|"
    r"formulas?|export)\b|\.(xlsx|xlsm|csv)\b",
    re.I,
)
# Utterances that are questions, not commands.
_QUESTION = re.compile(
    r"^\s*(what|how|why|where|which|who|can\s+you\s+explain|explain|"
    r"tell\s+me\s+about)\b",
    re.I,
)

# A leading interrogative auxiliary ("is the project set up?") makes a
# trailing-"?" utterance a question even when it contains an imperative-
# looking word like "set up". can/could/would are excluded: they usually
# front a polite command ("can you create a folder?").
_QUESTION_AUX = re.compile(r"^\s*(is|are|was|were|do|does|did|should|has|have|had)\b", re.I)


# Kind signals, checked in this order (first hit wins).
_RESEARCH = re.compile(r"\b(research|find\s+out|look\s+up|search\s+for)\b", re.I)
_BROWSER = re.compile(r"\b(open\s+(the\s+)?browser|go\s+to|navigate\s+to|"
                      r"open\s+the\s+(web\s*)?page)\b", re.I)
_APPS = (r"notepad|calculator|paint|explorer|settings|terminal|powershell|cmd|word|excel|"
         r"outlook|teams|slack|discord|spotify|chrome|edge|firefox|opera|brave|vs ?code|"
         r"visual studio|devin|cursor|obsidian|notion|figma|photoshop|steam|zoom")
_COMPUTER = re.compile(r"\b(click|type|move\s+(the\s+)?(mouse|cursor)|press\s+the|"
                       r"open\s+(?:the\s+)?(?:" + _APPS + r")(?:\s+app)?|"
                       r"\b(?:in|into|using)\s+(?:" + _APPS + r")\b|"
                       r"open\s+\w+\s+app)\b", re.I)
# "open" + an app name is a work object too, so "open notepad" is a task.
_OBJECT_APP = re.compile(r"\bopen\s+(?:the\s+)?(?:" + _APPS + r"|\w+\s+app)\b", re.I)
# Desktop requests that are a whole task in four words: "spotify next song",
# "pause the music", "turn the volume down", "open bluetooth settings". They
# have no imperative+object pair - "next song" is not "create a project" -
# but they are unmistakably jobs, and the computer worker can do each of
# them through an app's own API in one call.
_DESKTOP = re.compile(
    r"\b(?:next|previous|skip|pause|resume|stop|play)\b[^.?!]{0,24}\b(?:song|track|music|playlist|spotify)\b|"
    r"\bspotify\b[^.?!]{0,24}\b(?:next|previous|skip|pause|resume|play|stop|playlist|volume|song|track)\b|"
    r"\b(?:turn|put)\s+(?:the\s+)?(?:volume|sound|music)\s+(?:up|down|off)\b|"
    r"\b(?:mute|unmute)\b|"
    r"\bwhat(?:'s| is)\s+playing\b|"
    r"\b(?:minimi[sz]e|maximi[sz]e|restore|close)\s+(?:the\s+)?(?:window|[\w ]{0,20}window)\b|"
    r"\bopen\s+(?:the\s+)?(?:bluetooth|wi-?fi|display|sound|notification|power|battery|storage|privacy|"
    r"windows update|printer)\s*(?:settings|page)?\b|"
    r"\bopen\s+(?:my\s+)?(?:downloads|documents|desktop|pictures|music|videos)\s*(?:folder)?\b|"
    r"\bcopy\s+(?:this|that|it)\s+to\s+the\s+clipboard\b",
    re.I)

# Spreadsheet work goes to the file worker, which carries the spreadsheet tools.
_SHEET = re.compile(r"\b(spreadsheet|workbook|excel\s+(?:file|sheet|document|workbook)|sheets?|csv|xlsx|"
                    r"formulas?)\b|\.(xlsx|xlsm|csv)\b", re.I)
_CODING = re.compile(r"\b(react|vue|next(\.?js)?|python|node(\.?js)?|api|"
                     r"website|web\s*site|django|flask|express|typescript|"
                     r"javascript|component|repo|repository|script)\b"
                     # ...or a filename with a code extension ("backup.py").
                     r"|\.(py|js|jsx|ts|tsx|rb|go|rs|java|cs|php|sh|ps1)\b", re.I)

# workspace_hint extractors, most specific first.
_HINT_PATTERNS = [
    re.compile(r"\b(?:folder|directory|dir|project|repo|repository)\s+"
               r"(?:called|named)\s+[\"']?([A-Za-z0-9][\w.\-]*)", re.I),
    re.compile(r"\b(?:called|named)\s+[\"']?([A-Za-z0-9][\w.\-]*)", re.I),
    re.compile(r"\b(?:folder|directory|dir|project)\s+[\"']([^\"']+)[\"']", re.I),
    re.compile(r"\bin\s+[\"']?([A-Za-z0-9][\w.\-]*[\\/][\w.\-\\/]*)", re.I),
]
# words that "in X" must never capture as a workspace name
_HINT_STOP = {"it", "there", "the", "a", "an", "this", "that", "them", "place",
              "order", "which", "case", "general", "particular", "front"}


class HeuristicIntentClassifier(IntentClassifier):
    """Rule-based, deterministic, no network. The always-available default."""

    async def classify(self, text: str, *, context: Optional[dict[str, Any]] = None) -> Intent:
        raw = (text or "").strip()
        low = raw.lower()

        has_imp = bool(_IMPERATIVE.search(low)) or bool(_OBJECT_APP.search(low))
        has_obj = bool(_OBJECT.search(low)) or bool(_OBJECT_APP.search(low))
        is_question = bool(_QUESTION.match(low)) or (
            raw.endswith("?") and (bool(_QUESTION_AUX.match(low)) or not has_imp))
        desktop = bool(_DESKTOP.search(low))
        is_task = (has_imp and has_obj or desktop) and not is_question

        if not is_task:
            return Intent(
                is_task=False, confidence=0.75 if is_question else 0.5,
                title=self._title(raw), goal=raw, kind=TaskKind.GENERAL,
                worker_type="coding", reply="",
            )

        kind = self._kind(low)
        return Intent(
            is_task=True, confidence=0.9,
            title=self._title(raw), goal=raw, kind=kind,
            worker_type=self._worker_type(kind),
            workspace_hint=self._workspace_hint(raw),
            reply=self._reply(raw, kind),
        )

    # -- pieces ---------------------------------------------------------------

    @staticmethod
    def _kind(low: str) -> TaskKind:
        if _DESKTOP.search(low):           # "next song" is the app's own API, not research
            return TaskKind.COMPUTER
        if _RESEARCH.search(low):
            return TaskKind.RESEARCH
        if _COMPUTER.search(low):          # before BROWSER: "open opera" is an app, not a URL
            return TaskKind.COMPUTER
        if _SHEET.search(low):             # after COMPUTER: "open budget.xlsx in excel" is the app
            return TaskKind.FILE
        if _BROWSER.search(low):
            return TaskKind.BROWSER
        if _CODING.search(low):
            return TaskKind.CODING
        return TaskKind.GENERAL

    @staticmethod
    def _worker_type(kind: TaskKind) -> str:
        return {
            TaskKind.RESEARCH: "research",
            TaskKind.BROWSER: "browser",
            TaskKind.COMPUTER: "computer",
            TaskKind.FILE: "file",
        }.get(kind, "coding")            # CODING and GENERAL both -> coding

    @staticmethod
    def _workspace_hint(raw: str) -> Optional[str]:
        for pat in _HINT_PATTERNS:
            m = pat.search(raw)
            if not m:
                continue
            name = m.group(1).strip().strip("\"'")
            if name and name.lower() not in _HINT_STOP:
                return name
        return None

    @staticmethod
    def _title(raw: str) -> str:
        # First clause, capitalised, ≤60 chars.
        clause = re.split(r"\s+and\s+|[.;\n]", raw, maxsplit=1)[0].strip()
        clause = clause or raw.strip()
        if clause:
            clause = clause[0].upper() + clause[1:]
        return clause[:57] + "…" if len(clause) > 60 else clause

    @staticmethod
    def _reply(raw: str, kind: TaskKind) -> str:
        hint = HeuristicIntentClassifier._workspace_hint(raw)
        where = f" {hint}" if hint else ""
        verb = {
            TaskKind.RESEARCH: "look into this",
            TaskKind.BROWSER: "drive the browser for this",
            TaskKind.COMPUTER: "take this on",
            TaskKind.FILE: "work through the files",
        }.get(kind, f"create{where} and set up the foundation")
        return f"Understood. I'll {verb}. Starting now."


class ProviderIntentClassifier(IntentClassifier):
    """Asks a provider to classify, falling back to the heuristic on any
    parse or transport failure — so it degrades to rules, never to nothing."""

    _SYSTEM = (
        "You classify a user's message for a desktop assistant that can run "
        "autonomous tasks. Reply with ONLY a JSON object, no prose, no code "
        "fence, with exactly these keys: is_task (bool), title (string, short "
        "imperative), goal (string, the full objective), kind (one of coding, "
        "research, vision, browser, file, terminal, computer, fast, general), "
        "worker_type (one of coding, file, terminal, research, browser, "
        "computer), workspace_hint (string or null — a folder/project name if "
        "one is stated), reply (a one-sentence acknowledgement to say now). "
        "is_task is true only when the user is asking you to DO something "
        "concrete (create/build/install/run), not answer a question."
    )

    # Classification sits on the voice path: the user is waiting mid-turn,
    # so a slow route must be abandoned rather than waited out. The rules
    # are always there and answer in microseconds.
    DEFAULT_TIMEOUT_S = 8.0

    def __init__(self, provider: AgentProvider,
                 fallback: Optional[IntentClassifier] = None,
                 *, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self._provider = provider
        self._fallback = fallback or HeuristicIntentClassifier()
        self._timeout = timeout

    async def classify(self, text: str, *, context: Optional[dict[str, Any]] = None) -> Intent:
        try:
            resp = await asyncio.wait_for(
                self._provider.complete(
                    [ProviderMessage(role="user", content=(text or "").strip())],
                    tools=None, system=self._SYSTEM, max_tokens=400,
                ),
                timeout=self._timeout,
            )
            data = _parse_json(resp.text)
            if data is None:
                raise ValueError("no JSON object in reply")
            return self._to_intent(data, text)
        except (Exception, asyncio.TimeoutError) as exc:  # noqa: BLE001
            log.info("provider classification unavailable (%s); using rules",
                     type(exc).__name__)
            return await self._fallback.classify(text, context=context)

    def _to_intent(self, data: dict[str, Any], text: str) -> Intent:
        kind = _as_kind(data.get("kind"))
        hint = data.get("workspace_hint")
        if isinstance(hint, str):
            hint = hint.strip() or None
        elif hint is not None:
            hint = None
        wt = data.get("worker_type")
        worker_type = wt if isinstance(wt, str) and wt else self._worker_for(kind)
        return Intent(
            is_task=bool(data.get("is_task", False)),
            confidence=float(data.get("confidence", 0.85) or 0.85),
            title=str(data.get("title") or "").strip() or (text or "").strip()[:60],
            goal=str(data.get("goal") or text or "").strip(),
            kind=kind,
            worker_type=worker_type,
            workspace_hint=hint,
            reply=str(data.get("reply") or "").strip(),
        )

    @staticmethod
    def _worker_for(kind: TaskKind) -> str:
        return {
            TaskKind.RESEARCH: "research", TaskKind.BROWSER: "browser",
            TaskKind.COMPUTER: "computer", TaskKind.FILE: "file",
            TaskKind.TERMINAL: "terminal",
        }.get(kind, "coding")


# -- helpers -----------------------------------------------------------------

def _as_kind(value: Any) -> TaskKind:
    if isinstance(value, str):
        try:
            return TaskKind(value.strip().lower())
        except ValueError:
            pass
    return TaskKind.GENERAL


def _parse_json(text: str) -> Optional[dict[str, Any]]:
    """json.loads that tolerates a code fence or surrounding prose — models
    add both even when told not to."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    try:
        out = json.loads(t)
        return out if isinstance(out, dict) else None
    except ValueError:
        pass
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j <= i:
        return None
    try:
        out = json.loads(t[i:j + 1])
        return out if isinstance(out, dict) else None
    except ValueError:
        return None


class TieredIntentClassifier(IntentClassifier):
    """Rules first; the model only for the cases rules are unsure about.

    Classification sits on the voice path. Sending every utterance to a
    provider costs ~4 s on a gateway, which would be paid on *every*
    question the user asks, including plain chat that was never going to
    be a task. That is the wrong trade for an assistant whose whole point
    is answering before you finish the sentence.

    The heuristic reports how sure it is: 0.9 when an imperative verb
    meets a work object ("create a folder called MyApp"), 0.75 for a
    clear question, 0.5 when it is merely guessing. So take the rules'
    answer when it is confident, and spend a provider call only in the
    0.5 band — the utterances where a wrong call is actually likely.

    Net effect: no added latency on clear chat or clear commands, and a
    real model on "sort out my project" and the like.
    """

    def __init__(self, provider: AgentProvider,
                 *, threshold: float = 0.7, timeout: float = 8.0,
                 heuristic: Optional[IntentClassifier] = None) -> None:
        self._heuristic = heuristic or HeuristicIntentClassifier()
        self._provider_classifier = ProviderIntentClassifier(
            provider, fallback=self._heuristic, timeout=timeout)
        self.threshold = threshold

    async def classify(self, text: str, *, context: Optional[dict[str, Any]] = None) -> Intent:
        quick = await self._heuristic.classify(text, context=context)
        if quick.confidence >= self.threshold:
            return quick
        return await self._provider_classifier.classify(text, context=context)
