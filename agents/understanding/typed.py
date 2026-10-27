"""A request, typed (OR-02): what it should end in, about what, how risky.

"Email Lee the March invoice by Friday, don't cc anyone" is not just a
goal string. Typed, it is: outcome a sent message; entities Lee, the March
invoice, Friday; constraints by Friday and no cc; risk class send; wanted
output a message. The planner gets this beside the words (so "don't cc"
is a rule, not a hint it may lose), the verifier judges against the
outcome, and a send, payment or deletion with an unknown left in it is
asked about before anything starts.

Rules only - this sits in front of every job, and a model call here would
cost seconds on the voice path. What rules cannot know (who "Lee" is,
what "this" is) is resolved elsewhere (OR-03/04, OR-05) and passed in.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# Highest first: a request that sends and also writes is a send.
_RISK = (
    ("control", r"\btake over\b|\buse my (mouse|keyboard|computer|pc)\b|\bcontrol my\b"),
    ("pay", r"\b(pay|buy|purchase|order|checkout|check out|book|subscribe|donate|transfer)\b"),
    ("delete", r"\b(delete|remove|erase|wipe|uninstall|empty the (bin|trash)|clear (all|out))\b"),
    # "check my email" and "add text to it" are not sends: a send verb, or
    # email/text/message followed by who (matched on the original case).
    ("send", r"(?i:\b(send|tweet|reply|dm|publish|invite)\b|\bpost (it|this|that|on|to)\b|"
             r"\bshare (it|this|that|with)\b|\b(e-?mail|text|message) (him|her|them|me|it|this|that|the|my|our|everyone|all)\b)|"
             r"\b(?i:e-?mail|text|message) [A-Z][a-z]+"),
    ("change", r"\b(create|make|build|write|draft|edit|update|change|rename|move|copy|install|set ?up|"
               r"convert|fix|add|save|fill|organi[sz]e|sort|tidy|clean|schedule|type|open|play|launch)\b"),
)
_OUTPUT = (
    ("file", r"\b(document|doc|report|spreadsheet|sheet|workbook|pdf|poster|presentation|deck|slides|csv|"
             r"file|folder|website|app|page|image|picture|logo|list)\b"),
    ("answer", r"^(what|who|when|where|why|how|which|is|are|does|do|can)\b|\b(find out|tell me|check|look up|"
               r"summari[sz]e|compare|research)\b"),
    ("action", r"."),
)
_EXT = r"(?:docx?|xlsx?|xlsm|csv|pdf|pptx?|txt|md|json|png|jpe?g|gif|svg|html?|py|js|ts|zip)"
_FILE = re.compile(r'"([^"]+\.' + _EXT + r')"|((?:[A-Za-z]:\\|\.{0,2}/)?(?:[\w\-.()]+[\\/])*[\w\-()]+(?:\.[\w\-()]+)*\.'
                   + _EXT + r')\b', re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_URL = re.compile(r"\bhttps?://\S+|\b[\w-]+\.(?:com|org|net|io|co\.uk|dev|app)\b(?:/\S*)?", re.I)
_WHEN = re.compile(r"\b(today|tonight|tomorrow|yesterday|this (morning|afternoon|evening|week|weekend)|"
                   r"next (week|month|\w+day)|(mon|tues|wednes|thurs|fri|satur|sun)day|"
                   r"\d{1,2}(:\d{2})?\s?(am|pm)|\d{1,2}(st|nd|rd|th)? (of )?(jan|feb|mar|apr|may|jun|jul|aug|sep|"
                   r"oct|nov|dec)\w*)\b", re.I)
_MONEY = re.compile(r"[£$€]\s?\d[\d,.]*|\b\d[\d,.]*\s?(dollars|pounds|euros|usd|gbp|eur|kr|nok|dkk)\b", re.I)
_REF = re.compile(r"\b(this|that|these|those|it|them|here|the one)\b", re.I)
_CONSTRAINT = re.compile(
    r"\b(?:don'?t|do not|never|without|only|no more than|at most|at least|under|below|before|by|until|"
    r"keep|make sure|must|exactly)\b[^,.;!?]*", re.I)
_TO_WHOM = re.compile(r"\b(?i:to|for|email|e-mail|message|text|reply to|send)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b")


@dataclass
class TypedIntent:
    outcome: str                                   # what done looks like, in a line
    kind: str                                      # the job's kind (coding, computer, ...)
    risk: str                                      # read | change | send | delete | pay | control
    output: str                                    # file | message | answer | action
    entities: dict[str, list[str]] = field(default_factory=dict)   # files, people, emails, urls, when, money
    references: list[str] = field(default_factory=list)            # "this", "it" - and what they resolved to
    constraints: list[str] = field(default_factory=list)           # "don't cc anyone", "by Friday"
    unknowns: list[str] = field(default_factory=list)              # what must be asked before it starts

    @property
    def consequential(self) -> bool:
        return self.risk in ("send", "pay", "delete", "control")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def lines(self) -> list[str]:
        """For the planner: the request's parts as rules, not hints."""
        out = [f"The request, typed: outcome - {self.outcome}; output - {self.output}; risk - {self.risk}."]
        if self.constraints:
            out.append("Constraints they gave (each one is a rule): " + "; ".join(self.constraints))
        named = "; ".join(f"{k}: {', '.join(v)}" for k, v in self.entities.items() if v)
        if named:
            out.append("Named in it: " + named)
        return out


def type_request(text: str, *, kind: str = "general", resolved: str = "",
                 known_people: Optional[dict[str, str]] = None) -> TypedIntent:
    """Type one request. `resolved`: what "this"/"it" turned out to be
    (OR-03/04), if anything; `known_people`: name -> address they told Tora."""
    t = " ".join((text or "").split())
    low = t.lower()
    risk = next((name for name, rx in _RISK if re.search(rx, t if name == "send" else low)), "read")
    output = next((name for name, rx in _OUTPUT if re.search(rx, low)), "action")
    if risk == "send":
        output = "message"
    elif output == "action" and _FILE.search(t) and risk == "change":
        output = "file"
    people = [m for m in _TO_WHOM.findall(t) if m.split()[0] not in _NOT_NAMES]
    emails = _uniq(_EMAIL.findall(t))
    entities = {"files": _uniq((a or b).strip() for a, b in _FILE.findall(t)), "emails": emails,
                "urls": _uniq(u for u in _URL.findall(t) if not any(u in e for e in emails)),
                "people": _uniq(people), "when": _uniq(m.group(0) for m in _WHEN.finditer(t)),
                "money": _uniq(m.group(0) for m in _MONEY.finditer(t))}
    entities = {k: v for k, v in entities.items() if v}
    refs = _uniq(m.lower() for m in _REF.findall(t))
    references = [f"{r} -> {resolved}" if resolved else r for r in refs]
    constraints = _uniq(c.strip() for c in _CONSTRAINT.findall(t) if len(c.split()) >= 2)
    unknowns: list[str] = []
    if risk == "send" and not entities.get("emails"):
        who = [p for p in entities.get("people", []) if p.lower() not in (known_people or {})]
        if who:
            unknowns.append(f"{who[0]}'s email address")
        elif not entities.get("people"):
            unknowns.append("who it goes to")
    if risk == "pay" and not entities.get("money"):
        unknowns.append("how much they are willing to pay")
    if refs and not resolved and risk in ("send", "delete", "pay"):
        unknowns.append(f"what '{refs[0]}' is")
    return TypedIntent(outcome=_outcome(t, risk, output), kind=kind, risk=risk, output=output,
                       entities=entities, references=references, constraints=constraints, unknowns=unknowns)


# Capitalised words after "to"/"for" that are not people.
_NOT_NAMES = {"The", "My", "Our", "Your", "This", "That", "It", "PDF", "Word", "Excel", "Notepad", "Gmail",
              "Outlook", "Slack", "Teams", "Google", "Chrome", "Edge", "Monday", "Tuesday", "Wednesday",
              "Thursday", "Friday", "Saturday", "Sunday", "Desktop", "Documents", "Downloads", "GitHub",
              "Notion", "Linear", "Jira", "English", "Spanish", "French", "German", "Danish", "Norwegian"}


def _outcome(text: str, risk: str, output: str) -> str:
    verb = {"send": "sent", "pay": "paid for (after their yes)", "delete": "deleted (after their yes)",
            "control": "done on their PC, under their eye"}.get(risk)
    head = text[:140]
    if verb:
        return f"{head} - {verb}"
    return {"file": f"a file that does this: {head}", "answer": f"an answer: {head}",
            "message": f"a message: {head}"}.get(output, f"done: {head}")


def _uniq(items) -> list[str]:
    out: list[str] = []
    for i in items:
        if i and i not in out:
            out.append(i)
    return out[:8]
