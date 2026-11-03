"""Jobs made ready by the first launch: "Ready to start" cards on Home.

HeyClicky ends its tour by handing you jobs it prepared from what you told
it (docs/ui/HEYCLICKY_ONBOARDING.md, step 8). Ours: Tora makes two or three
from the interview (ready_jobs, ui/onboarding_interview.py) - or, with no
voice, they come from the starter skills for the work they picked - and
they wait on Home until started or put away. Starting one is asking her in
those words (AppBridge.ask): she does it or hands it to an agent, as with
any question.

A small JSON file in the data folder; no Qt here.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_log = logging.getLogger("tora.ready")

MOST = 3


@dataclass
class ReadyJob:
    title: str            # a few words: "Find a lighter editing workflow"
    why: str = ""         # one sentence: why it is here, from what they said
    ask: str = ""         # what starting it says to her
    made: float = 0.0


def _path() -> Path:
    from config import data_dir
    return data_dir() / "ready_jobs.json"


def clean(raw) -> list[ReadyJob]:
    """At most MOST, each with a title and something to ask."""
    out: list[ReadyJob] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split())[:70]
        ask = " ".join(str(item.get("ask") or title).split())[:300]
        if title and ask:
            out.append(ReadyJob(title, " ".join(str(item.get("why") or "").split())[:200], ask,
                                float(item.get("made") or time.time())))
    return out[:MOST]


def load() -> list[ReadyJob]:
    try:
        return clean(json.loads(_path().read_text(encoding="utf-8")))
    except FileNotFoundError:
        return []
    except Exception:  # noqa: BLE001 - a broken file is no jobs, never a broken Home
        _log.warning("could not read the ready jobs", exc_info=True)
        return []


def save(jobs: list[ReadyJob]) -> None:
    try:
        _path().write_text(json.dumps([asdict(j) for j in jobs[:MOST]], indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        _log.warning("could not keep the ready jobs", exc_info=True)


def take(index: int) -> ReadyJob | None:
    """Started or put away: off the list, and returned."""
    jobs = load()
    if not 0 <= index < len(jobs):
        return None
    job = jobs.pop(index)
    save(jobs)
    return job


def from_skills(uses) -> list[ReadyJob]:
    """With no voice to make them: the starter skills for their work, each
    card's own example ("Invoice Sarah for last week")."""
    from ui.setup_wizard import SKILL_CARDS, SKILLS_FOR_EVERYONE, skills_for
    ids = skills_for(uses) or list(SKILLS_FOR_EVERYONE)
    out = []
    for sid in ids:
        if sid in SKILL_CARDS:
            words = SKILL_CARDS[sid][1]
            out.append(ReadyJob(words, "One of the skills I start with for your work.", words, time.time()))
        if len(out) >= MOST:
            break
    return out
