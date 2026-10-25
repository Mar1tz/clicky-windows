"""Follow-ups find their job (OR-07, the Massive Direction Plan).

"Make that longer", "continue the website", the same request said twice
while it is still being built: each used to start a new job from nothing,
in a new folder. Before a job is started, what was asked is matched
against the jobs of the last hour:

    duplicate   the same request while that job still runs - say so, start nothing
    continue    "continue / finish / resume <it>" - that job, resumed if paused
    revise      a change to what a job made - a new job in ITS folder, told to
                work on what is there

Words, not a model: what was asked is matched to a job's title and goal by
its longer words; "that" / "it" with nothing named is the latest job.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from agents.core import Task, TaskStatus

RECENT_S = 3600.0
_CONTINUE = re.compile(r"\b(continue|keep going|carry on|finish|resume|pick up|get back to)\b", re.I)
_REVISE = re.compile(
    r"\b(make|change|update|edit|fix|improve|shorten|lengthen|add|remove|rename|redo|rewrite|tweak)\b"
    r"[^.?!]{0,60}\b(it|that|this|the same|the \w+ you (?:made|built|wrote|did))\b|"
    r"\b(it|that)\b[^.?!]{0,20}\b(longer|shorter|bigger|smaller|better|darker|lighter|simpler|nicer)\b",
    re.I)
_DEICTIC = re.compile(r"\b(it|that|this|the same)\b", re.I)
# A change to something named ("make the essay longer", "add a conclusion
# to the essay") - not "make a new essay", which is a new job.
_CHANGE = re.compile(r"\b(change|update|edit|fix|improve|shorten|lengthen|add|remove|rename|redo|rewrite|"
                     r"tweak)\b|\b(longer|shorter|bigger|smaller|better|darker|lighter|simpler|nicer|"
                     r"clearer|more \w+|less \w+)\b", re.I)
_NEW = re.compile(r"\b(new|another|second|different)\b", re.I)
_STOP = {"make", "build", "create", "write", "with", "that", "this", "your", "mine", "them", "into",
         "from", "please", "could", "would", "should", "about", "some", "more", "less", "the", "and",
         "continue", "finish", "resume", "longer", "shorter", "change", "update", "keep", "going"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 4 and w not in _STOP}


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def match(asked: str, tasks: list[Task], now: Optional[float] = None) -> tuple[Optional[Task], str]:
    """(the job it belongs to, "duplicate" | "continue" | "revise"), or (None, "")."""
    now = time.time() if now is None else now
    recent = [t for t in tasks
              if not t.status.is_terminal or now - (t.updated_at or t.created_at) < RECENT_S]
    recent = [t for t in recent if not (t.metadata or {}).get("parent_id")]
    if not recent:
        return None, ""
    recent.sort(key=lambda t: t.updated_at or t.created_at, reverse=True)
    mine = _words(asked)
    scored = [(_overlap(mine, _words(f"{t.title} {t.goal}")), t) for t in recent]
    best_score, best = max(scored, key=lambda s: s[0])

    live = [s for s in scored if not s[1].status.is_terminal]
    if live:
        dup_score, dup = max(live, key=lambda s: s[0])
        if dup_score >= 0.7:
            return dup, "duplicate"
    if _CONTINUE.search(asked):
        if best_score >= 0.2:
            return best, "continue"
        if _DEICTIC.search(asked) or not mine:
            return recent[0], "continue"
    if _NEW.search(asked):
        return None, ""
    if _REVISE.search(asked) or _CHANGE.search(asked):
        if best_score >= 0.2:
            return best, "revise"
        if _REVISE.search(asked) and _DEICTIC.search(asked):
            return recent[0], "revise"
    return None, ""


def revision_goal(asked: str, job: Task) -> str:
    return (f"{asked}\n\nThis changes what an earlier job made ({job.title!r}) - its files are in this "
            "folder. Work on them; do not start again from nothing.")
