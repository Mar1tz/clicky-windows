"""What "it" means (OR-06, the Massive Direction Plan).

"Summarise it", "send that to Sam", "make this bigger": the order in which
a reference is resolved, each answer with the evidence that decided it:

    name        the request names the thing ("the budget sheet")
    last_said   the last thing pointed at or talked about, a moment ago
    active_job  the job running now
    recent      the newest thing they showed or selected (a trace, a file)
    ask         nothing it could mean - ask which

Pure: the manager hands in what it knows, gets back a Resolution, and logs
its evidence; a job told "it" is told what "it" was and why.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

LAST_SAID_S = 120.0                 # "that" means the last thing said for this long
RECENT_S = 600.0
_DEICTIC = re.compile(r"\b(it|that|this|these|those|the same|them)\b", re.I)
_STOP = {"this", "that", "with", "from", "into", "file", "make", "send", "open", "please", "these",
         "those", "them", "same", "the", "and", "for"}


@dataclass
class Thing:
    kind: str                       # file | job | control | answer | selection | trace
    name: str
    detail: str = ""                # a path, a job id
    at: float = field(default_factory=time.time)

    def describe(self) -> str:
        what = {"job": "the job", "file": "the file", "control": "the control", "trace": "what they showed",
                "selection": "the selected text", "answer": "what Tora just said about"}.get(self.kind, self.kind)
        return f"{what} {self.name!r}" + (f" ({self.detail})" if self.detail and self.kind != "job" else "")


@dataclass
class Resolution:
    thing: Optional[Thing]
    link: str                       # name | last_said | active_job | recent | ask | none
    evidence: str

    def to_dict(self) -> dict:
        return {"link": self.link, "evidence": self.evidence, "thing": asdict(self.thing) if self.thing else None}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 4 and w not in _STOP}


_POINTERS = {"it", "that", "this", "these", "those", "them", "same"}
_CONJUNCTION_NEXT = {"the", "a", "an", "it", "they", "we", "i", "you", "he", "she", "there", "my", "our", "your"}


def refers(text: str) -> bool:
    """Does the request point at something outside itself? "Send it to the
    team" does; "make a folder called MyApp and build the app inside it"
    names its own "it", and "check that the site builds" uses "that" as a
    joining word. So: a pointer near the start, or in a short request."""
    words = re.findall(r"[a-z']+", (text or "").lower())
    pointers = [i for i, w in enumerate(words) if w in _POINTERS
                and not (w == "that" and i + 1 < len(words) and words[i + 1] in _CONJUNCTION_NEXT)]
    if not pointers:
        return False
    return pointers[0] < 6 or len(words) <= 7


def resolve(text: str, *, last_said: Optional[Thing] = None, active_job: Optional[Thing] = None,
            recent: tuple = (), now: Optional[float] = None) -> Resolution:
    now = time.time() if now is None else now
    pool = [t for t in (last_said, active_job, *recent) if t is not None]
    asked = _words(text)
    named = [t for t in pool if asked & _words(t.name)]
    if named:
        best = max(named, key=lambda t: (len(asked & _words(t.name)), t.at))
        return Resolution(best, "name", f"the request names {best.name!r}")
    if not refers(text):
        return Resolution(None, "none", "nothing is referred to")
    if last_said is not None and now - last_said.at < LAST_SAID_S:
        return Resolution(last_said, "last_said", f"the last thing pointed at or talked about, "
                                                  f"{now - last_said.at:.0f}s ago")
    if active_job is not None:
        return Resolution(active_job, "active_job", "the job running now")
    fresh = [t for t in recent if now - t.at < RECENT_S]
    if fresh:
        newest = max(fresh, key=lambda t: t.at)
        return Resolution(newest, "recent", f"the newest thing they showed or selected, "
                                            f"{(now - newest.at) / 60:.0f} min ago")
    return Resolution(None, "ask", "nothing recent it could mean - ask which")
