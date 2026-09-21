"""Intent classification: is this utterance a task to run, or a chat to answer?

The gate at the very front of the agent flow. "Create a folder called MyApp
and build a React foundation in it" must become a background Task; "what is a
React component?" must stay an ordinary answer. Getting this wrong in either
direction is jarring — a spurious task, or a request that quietly does
nothing — so the heuristic is deliberately conservative: a task needs BOTH an
imperative verb AND a concrete work object, and any question shape short-
circuits to chat.

Two classifiers:
  * HeuristicIntentClassifier — rules only, no network, always available.
  * ProviderIntentClassifier — asks an AgentProvider for strict JSON and
    falls back to the heuristic on any hiccup, so it is never worse than the
    rules alone.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from agents.core import (
    AgentProvider, Intent, IntentClassifier, ProviderMessage, TaskKind,
)

# An action word...
_IMPERATIVE = re.compile(
    r"\b(create|build|make|set\s*up|setup|scaffold|generate|install|write|"
    r"refactor|initiali[sz]e|init)\b",
    re.I,
)
# ...applied to a work object.
_OBJECT = re.compile(
    r"\b(folder|directory|dir|project|app|application|file|repo|repository|"
    r"script|website|web\s*site|api|component)\b",
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
_COMPUTER = re.compile(r"\b(click|type|move\s+(the\s+)?(mouse|cursor)|"
                       r"press\s+the)\b", re.I)
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

        has_imp = bool(_IMPERATIVE.search(low))
        has_obj = bool(_OBJECT.search(low))
        is_question = bool(_QUESTION.match(low)) or (
            raw.endswith("?") and (bool(_QUESTION_AUX.match(low)) or not has_imp))
        is_task = has_imp and has_obj and not is_question

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
        if _RESEARCH.search(low):
            return TaskKind.RESEARCH
        if _BROWSER.search(low):
            return TaskKind.BROWSER
        if _COMPUTER.search(low):
            return TaskKind.COMPUTER
        if _CODING.search(low):
            return TaskKind.CODING
        return TaskKind.GENERAL

    @staticmethod
    def _worker_type(kind: TaskKind) -> str:
        return {
            TaskKind.RESEARCH: "research",
            TaskKind.BROWSER: "browser",
            TaskKind.COMPUTER: "computer",
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

    def __init__(self, provider: AgentProvider,
                 fallback: Optional[IntentClassifier] = None) -> None:
        self._provider = provider
        self._fallback = fallback or HeuristicIntentClassifier()

    async def classify(self, text: str, *, context: Optional[dict[str, Any]] = None) -> Intent:
        try:
            resp = await self._provider.complete(
                [ProviderMessage(role="user", content=(text or "").strip())],
                tools=None, system=self._SYSTEM, max_tokens=400,
            )
            data = _parse_json(resp.text)
            if data is None:
                raise ValueError("no JSON object in reply")
            return self._to_intent(data, text)
        except Exception:
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
