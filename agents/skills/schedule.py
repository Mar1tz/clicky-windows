"""Skills that run on their own: a skill's `every:` line, read as a trigger
for agents/manager/scheduler.py.

The two trigger shapes the scheduler understands live here, now that packs
(which had them first) are gone. A skill's schedule is words the user
wrote, so reading it is forgiving and a line it can't read is reported,
never guessed at:

    every: day 08:00            every: weekdays 9am
    every: Friday 16:00         every: month 1 09:00
    every: last day of the month 17:00
    every: new file in C:\\Users\\me\\Downloads\\Receipts
    every: new *.pdf in Documents\\Inbox
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agents.core import Task, TaskKind


@dataclass
class Trigger:
    """When a job runs without being asked. See agents/manager/scheduler."""
    kind: str = "manual"             # manual | schedule | folder

    def describe(self) -> str:
        return self.kind


@dataclass
class Schedule(Trigger):
    """every: daily | weekdays | weekly | monthly. `at` is local HH:MM.
    weekly uses `weekday` (0=Mon); monthly uses `day` (1-28, or -1 = the
    last day of the month)."""
    kind: str = "schedule"
    every: str = "daily"
    at: str = "08:00"
    weekday: int = 0
    day: int = 1

    def describe(self) -> str:
        if self.every == "weekly":
            return f"every {DAYS[self.weekday % 7].capitalize()} at {self.at}"
        if self.every == "monthly":
            return f"monthly on the {'last day' if self.day < 0 else _nth(self.day)} at {self.at}"
        return f"{'every day' if self.every == 'daily' else 'weekdays'} at {self.at}"


@dataclass
class FolderWatch(Trigger):
    """Fire when new files matching `patterns` appear under `path`."""
    kind: str = "folder"
    path: str = ""
    patterns: list[str] = field(default_factory=lambda: ["*"])

    def describe(self) -> str:
        what = "file" if self.patterns == ["*"] else ", ".join(self.patterns)
        return f"a new {what} in {self.path}"


DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class ScheduleError(ValueError):
    """An `every:` line that can't be read, and how to write it."""


def parse_every(text: str) -> Trigger:
    raw = " ".join((text or "").split())
    low = raw.lower()
    if not low:
        raise ScheduleError("it's empty")
    m = re.match(r"^(?:a\s+)?new\s+(.+?)\s+in\s+(.+)$", raw, re.I)
    if m:
        what, folder = m.group(1).strip(), m.group(2).strip().strip('"')
        patterns = ["*"] if re.fullmatch(r"files?|documents?", what, re.I) else \
            [p.strip() for p in re.split(r"[,\s]+(?:or\s+)?", what) if p.strip() and p.lower() != "or"]
        return FolderWatch(path=_folder(folder), patterns=patterns or ["*"])
    at = _time(low)
    rest = re.sub(r"\b(at|on|the|every|of)\b", " ", _strip_time(low))
    rest = " ".join(rest.split())
    if re.fullmatch(r"(day|daily|morning|evening|night)?", rest):
        return Schedule(every="daily", at=at)
    if re.fullmatch(r"(week ?days?|weekdays?|working days?|work days?)", rest):
        return Schedule(every="weekdays", at=at)
    for i, day in enumerate(DAYS):
        if re.fullmatch(rf"({day}|{day[:3]})s?", rest):
            return Schedule(every="weekly", at=at, weekday=i)
    if re.fullmatch(r"(last day|last)( month)?", rest) or rest in ("month end", "end month", "month-end"):
        return Schedule(every="monthly", at=at, day=-1)
    m = re.fullmatch(r"(?:month(?:ly)?\s*(?:day\s*)?)?(\d{1,2})(?:st|nd|rd|th)?(?:\s*(?:day\s*)?month(?:ly)?)?", rest)
    if m and ("month" in rest) and 1 <= int(m.group(1)) <= 28:
        return Schedule(every="monthly", at=at, day=int(m.group(1)))
    if rest in ("month", "monthly"):
        return Schedule(every="monthly", at=at, day=1)
    raise ScheduleError("try 'day 08:00', 'weekdays 09:00', 'Friday 16:00', 'month 1 09:00', "
                        "'last day of the month 17:00' or 'new file in <folder>'")


def _time(low: str) -> str:
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b(?!\s*(?:st|nd|rd|th|day))", low)
    if not m or (m.group(2) is None and m.group(3) is None):
        return "09:00"
    h, mins = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and h < 12:
        h += 12
    if m.group(3) == "am" and h == 12:
        h = 0
    if h > 23 or mins > 59:
        raise ScheduleError(f"{m.group(0)} isn't a time of day")
    return f"{h:02d}:{mins:02d}"


def _strip_time(low: str) -> str:
    return re.sub(r"\b\d{1,2}(?::\d{2})\s*(am|pm)?\b|\b\d{1,2}\s*(am|pm)\b", " ", low)


def _nth(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _folder(text: str) -> str:
    """A folder as the user wrote it: absolute, ~, or under their home."""
    path = os.path.expandvars(os.path.expanduser(text))
    if not os.path.isabs(path):
        path = str(Path.home() / path)
    return os.path.normpath(path)


# -- the scheduler's jobs, one per scheduled skill ---------------------------------------

JOB_PREFIX = "skill:"


def sync_jobs(manager: Any) -> dict[str, str]:
    """Make the scheduler's skill jobs match the skills that are on and have
    an `every:` line. Returns {skill id: why its schedule was not used}."""
    scheduler, library = getattr(manager, "scheduler", None), getattr(manager, "skills", None)
    if scheduler is None or library is None:
        return {}
    from agents.manager.scheduler import ScheduledJob
    problems: dict[str, str] = {}
    wanted: dict[str, Any] = {}
    for skill in library.enabled():
        if not skill.every:
            continue
        try:
            trigger = parse_every(skill.every)
        except ScheduleError as e:
            problems[skill.id] = f"'every: {skill.every}' - {e}"
            continue
        wanted[JOB_PREFIX + skill.id] = (skill, trigger)
    for job in scheduler.jobs():
        if job["id"].startswith(JOB_PREFIX) and job["id"] not in wanted:
            scheduler.remove(job["id"])
    current = {j["id"]: j for j in scheduler.jobs()}
    for job_id, (skill, trigger) in wanted.items():
        held = current.get(job_id)
        if held is not None and held.get("trigger") == trigger.describe():
            continue
        if held is not None:
            scheduler.remove(job_id)
        scheduler.add(ScheduledJob(
            id=job_id, name=skill.name, trigger=trigger, owner="skills",
            folder=getattr(trigger, "path", ""), fire_on_existing=False,
            make_task=_maker(manager, skill.id, skill.name, trigger.describe())))
    return problems


def _maker(manager: Any, skill_id: str, name: str, when: str):
    def make(extra: dict[str, Any]):
        goal = f"Do '{name}' now: it is scheduled for {when}."
        files = extra.get("files") or extra.get("new_files")
        if files:
            goal += " The new files: " + ", ".join(str(f) for f in list(files)[:20])
        task = Task(title=name, goal=goal, workspace=manager.workspace_root, kind=TaskKind.COMPUTER,
                    worker_type="computer", metadata={"skill": skill_id, "request": goal, **extra})
        return task, None
    return make
