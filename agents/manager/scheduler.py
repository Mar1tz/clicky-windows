"""Scheduler: background work nobody asked for this minute.

"Every morning, chase overdue invoices." "When a PDF lands in the inbox,
process it." The manager runs tasks when told; this is what tells it.

A job is a trigger plus a `make_task()` that returns (Task, AgentSpec).
Jobs are registered in code (an extension registers its own on enable,
from its settings), so nothing executable is ever read back from disk.
What IS persisted is each job's memory: when it last fired and which
files it has already seen. That is what makes a restart safe - a daily
job does not fire twice in one day, and a folder watch does not
re-process the whole inbox.

Rules that keep a background job from becoming a nuisance:
  * a job never overlaps itself - if its last task is still going, the
    tick is skipped;
  * a new job does not fire for slots before it existed (creating a
    "daily at 08:00" job at 09:00 waits for tomorrow);
  * a missed slot fires ONCE on the next tick (the PC was off at 08:00:
    run when it comes back), never once per missed day.

Its own daemon thread, not the agent loop: submitting a task from inside
the runtime's loop would block on it.
"""
from __future__ import annotations

import calendar
import fnmatch
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from agents.core import AgentSpec, EventKind, Task

log = logging.getLogger("agents.scheduler")

TICK_S = 30.0
MAX_FILES_PER_FIRE = 50


@dataclass
class ScheduledJob:
    id: str
    name: str
    trigger: Any                                   # agents.extensions.hooks Schedule | FolderWatch
    make_task: Callable[[dict[str, Any]], tuple[Task, Optional[AgentSpec]]]
    owner: str = ""                                # extension id
    enabled: bool = True
    folder: str = ""                               # resolved path for a FolderWatch
    fire_on_existing: bool = True                  # first scan of a folder: process what is there


class Scheduler:
    def __init__(self, manager: Any, *, state_path: Optional[str] = None,
                 tick_s: float = TICK_S, clock: Callable[[], float] = time.time) -> None:
        self.manager = manager
        self.state_path = state_path
        self.tick_s = tick_s
        self.clock = clock
        self._jobs: dict[str, ScheduledJob] = {}
        self._state: dict[str, dict[str, Any]] = self._load()
        self._last_task: dict[str, str] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- jobs --------------------------------------------------------------------

    def add(self, job: ScheduledJob) -> None:
        with self._lock:
            self._jobs[job.id] = job
            st = self._state.setdefault(job.id, {})
            st.setdefault("created", self.clock())
            st.setdefault("last_fired", 0.0)
            st.setdefault("seen", [])
            st["name"] = job.name
            self._save()

    def remove(self, job_id: str) -> bool:
        with self._lock:
            return self._jobs.pop(job_id, None) is not None

    def jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for j in self._jobs.values():
                st = self._state.get(j.id, {})
                out.append({"id": j.id, "name": j.name, "owner": j.owner, "enabled": j.enabled,
                            "trigger": j.trigger.describe() if hasattr(j.trigger, "describe") else str(j.trigger),
                            "last_fired": st.get("last_fired", 0.0),
                            "next": self.next_run(j)})
            return out

    # -- the loop --------------------------------------------------------------------

    def start(self) -> "Scheduler":
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="agent-scheduler", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.tick_s):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the loop must outlive any one job
                log.exception("scheduler tick failed")

    def tick(self, now: Optional[float] = None) -> list[Task]:
        """Fire everything due. Public so tests (and "Run now") drive it."""
        now = self.clock() if now is None else now
        fired: list[Task] = []
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.enabled]
        for job in jobs:
            if self._still_running(job):
                continue
            extra = self._due(job, now)
            if extra is None:
                continue
            task = self.fire(job, now=now, extra=extra)
            if task is not None:
                fired.append(task)
        return fired

    def fire(self, job: ScheduledJob, *, now: Optional[float] = None,
             extra: Optional[dict[str, Any]] = None) -> Optional[Task]:
        now = self.clock() if now is None else now
        try:
            task, spec = job.make_task(dict(extra or {}))
        except Exception:  # noqa: BLE001
            log.exception("job %s could not build its task", job.id)
            return None
        task.metadata.setdefault("scheduled_job", job.id)
        task.metadata.setdefault("trigger", job.trigger.describe() if hasattr(job.trigger, "describe") else "")
        submitted = self.manager.submit_task(task, agent=spec)
        with self._lock:
            st = self._state.setdefault(job.id, {})
            st["last_fired"] = now
            st["last_task"] = submitted.id
            self._last_task[job.id] = submitted.id
            self._save()
        events = getattr(self.manager, "events", None)
        if events is not None:
            events.emit(EventKind.LOG, f"Scheduled: {job.name}", task_id=submitted.id,
                        data={"job": job.id, "owner": job.owner})
        return submitted

    # -- due-ness ------------------------------------------------------------------------

    def _still_running(self, job: ScheduledJob) -> bool:
        tid = self._last_task.get(job.id) or self._state.get(job.id, {}).get("last_task")
        if not tid:
            return False
        get = getattr(self.manager, "get_task", None)
        task = get(tid) if get else None
        return task is not None and not task.status.is_terminal

    def _due(self, job: ScheduledJob, now: float) -> Optional[dict[str, Any]]:
        kind = getattr(job.trigger, "kind", "manual")
        if kind == "schedule":
            slot = last_slot(job.trigger, datetime.fromtimestamp(now))
            if slot is None:
                return None
            st = self._state.get(job.id, {})
            slot_ts = slot.timestamp()
            if slot_ts <= st.get("created", now) or slot_ts <= st.get("last_fired", 0.0):
                return None
            return {"slot": slot.isoformat(timespec="minutes")}
        if kind == "folder":
            return self._new_files(job)
        return None

    def _new_files(self, job: ScheduledJob) -> Optional[dict[str, Any]]:
        folder = job.folder or getattr(job.trigger, "path", "")
        if not folder or not os.path.isdir(folder):
            return None
        patterns = [p.lower() for p in getattr(job.trigger, "patterns", ["*"])] or ["*"]
        found = []
        for dirpath, _dirs, files in os.walk(folder):
            if os.path.basename(dirpath).lower() in ("processed", "filed", ".tora"):
                continue
            for f in files:
                if any(fnmatch.fnmatch(f.lower(), p) for p in patterns):
                    full = os.path.join(dirpath, f)
                    try:
                        found.append(f"{full}|{int(os.path.getmtime(full))}")
                    except OSError:
                        continue
        with self._lock:
            st = self._state.setdefault(job.id, {})
            seen = set(st.get("seen", []))
            first_scan = not st.get("scanned")
            st["scanned"] = True
            new = [f for f in found if f not in seen]
            st["seen"] = sorted(seen | set(found))[-5000:]
            self._save()
        if not new or (first_scan and not job.fire_on_existing):
            return None
        return {"files": [n.rsplit("|", 1)[0] for n in new[:MAX_FILES_PER_FIRE]]}

    def next_run(self, job: ScheduledJob) -> str:
        if getattr(job.trigger, "kind", "") != "schedule":
            return "on new files" if getattr(job.trigger, "kind", "") == "folder" else "manual"
        nxt = next_slot(job.trigger, datetime.fromtimestamp(self.clock()))
        return nxt.isoformat(timespec="minutes") if nxt else ""

    # -- persistence ------------------------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.state_path or not os.path.isfile(self.state_path):
            return {}
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        if not self.state_path:
            return
        tmp = self.state_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh)
            os.replace(tmp, self.state_path)
        except OSError:
            log.exception("could not save scheduler state")


# -- calendar arithmetic (pure) ------------------------------------------------------------

def _hm(at: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in (at or "08:00").split(":")[:2])
        return max(0, min(23, h)), max(0, min(59, m))
    except ValueError:
        return 8, 0


def _month_day(year: int, month: int, day: int) -> int:
    last = calendar.monthrange(year, month)[1]
    return last if day < 0 or day > last else max(1, day)


def _candidates(trigger: Any, around: datetime) -> list[datetime]:
    """Slots in a window around `around`, sorted."""
    h, m = _hm(getattr(trigger, "at", "08:00"))
    every = getattr(trigger, "every", "daily")
    out: list[datetime] = []
    if every == "monthly":
        y, mo = around.year, around.month
        for delta in (-2, -1, 0, 1, 2):
            yy, mm = y + (mo - 1 + delta) // 12, (mo - 1 + delta) % 12 + 1
            d = _month_day(yy, mm, int(getattr(trigger, "day", 1)))
            out.append(datetime(yy, mm, d, h, m))
    else:
        for delta in range(-8, 9):
            day = (around + timedelta(days=delta)).replace(hour=h, minute=m, second=0, microsecond=0)
            if every == "weekdays" and day.weekday() >= 5:
                continue
            if every == "weekly" and day.weekday() != int(getattr(trigger, "weekday", 0)) % 7:
                continue
            out.append(day)
    return sorted(out)


def last_slot(trigger: Any, now: datetime) -> Optional[datetime]:
    past = [c for c in _candidates(trigger, now) if c <= now]
    return past[-1] if past else None


def next_slot(trigger: Any, now: datetime) -> Optional[datetime]:
    future = [c for c in _candidates(trigger, now) if c > now]
    return future[0] if future else None
