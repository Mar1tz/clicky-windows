"""Waitpoints: what a job is waiting for, as a thing of its own (OR-32).

Borrowed from trigger.dev's v4 engine (docs/BORROWED_LOGIC_PLAN.md). A job
used to wait in one of a few states - WAITING_APPROVAL, WAITING_USER - with
the thing it waited for held in a future nobody else could see. Now each
wait is a Waitpoint: its kind (approval, answer, time, event), the jobs it
blocks, what it is waiting for in words, a deadline, and how it ended -
completed with an output, timed out, or cancelled with its job.

* One waitpoint can block several jobs: the same idempotency key while it
  is still waiting returns the one already there - "which month?" asked by
  two jobs is one question.
* A time or event wait lets go of the screen and the browser (OR-10
  claims) while it waits, so a job waiting for 9:00 does not hold the mouse
  all night; the manager gives them back - in turn - before it goes on.
* Waits are idempotent by construction: "until 09:00" is an absolute time,
  "until report.pdf exists" a fact on disk. A job a restart cut off re-runs
  the wait (the wait tools are replay-safe, OR-31) and lands on the same
  deadline.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Awaitable, Callable, Optional

from agents.core import EventBus, EventKind, RunControl, Waitpoint, WaitKind, WaitStatus

log = logging.getLogger("agents.waitpoints")

POLL_S = 1.0                     # how often a time or event wait looks again
MAX_WAIT_S = 7 * 24 * 3600       # nothing waits longer than a week


class Waitpoints:
    """Every waitpoint of this run of HeyTora; resolved from any thread."""

    def __init__(self, events: EventBus) -> None:
        self.events = events
        self._items: dict[str, Waitpoint] = {}
        self._lock = threading.Lock()
        # The manager's hooks: a job's state and claims follow its waits.
        self.on_wait: Optional[Callable[[str, Waitpoint], None]] = None
        self.on_resume: Optional[Callable[[str, Waitpoint], Awaitable[None]]] = None

    # -- making one ----------------------------------------------------------

    def create(self, kind: WaitKind, task_id: str, *, prompt: str, until: Optional[float] = None,
               condition: Optional[dict] = None, idempotency_key: str = "") -> Waitpoint:
        with self._lock:
            if idempotency_key:
                same = next((w for w in self._items.values() if w.idempotency_key == idempotency_key
                             and w.status is WaitStatus.WAITING), None)
                if same is not None:
                    if task_id and task_id not in same.task_ids:
                        same.task_ids.append(task_id)
                    return same
            wp = Waitpoint(kind=kind, task_ids=[task_id] if task_id else [], prompt=prompt,
                           until=min(until, time.time() + MAX_WAIT_S) if until else None,
                           condition=dict(condition or {}), idempotency_key=idempotency_key)
            self._items[wp.id] = wp
        self._emit(wp, f"Waiting: {prompt}")
        return wp

    # -- resolving one ---------------------------------------------------------

    def complete(self, wp_id: str, output: Any = None) -> bool:
        return self._end(wp_id, WaitStatus.COMPLETED, output)

    def answer(self, text: str, wp_id: str = "") -> Optional[Waitpoint]:
        """The person's answer to a job's question: the one named, else the
        oldest question still open."""
        with self._lock:
            open_ = [w for w in self._items.values()
                     if w.kind is WaitKind.ANSWER and w.status is WaitStatus.WAITING]
        wp = next((w for w in open_ if w.id == wp_id), None) if wp_id else (open_[0] if open_ else None)
        if wp is not None and self.complete(wp.id, text):
            return wp
        return None

    def cancel_for(self, task_id: str) -> int:
        """A job ended: its waits end with it (unless another job still waits on one)."""
        n = 0
        with self._lock:
            mine = [w for w in self._items.values() if task_id in w.task_ids and w.status is WaitStatus.WAITING]
        for w in mine:
            w.task_ids.remove(task_id)
            if not w.task_ids:
                n += self._end(w.id, WaitStatus.CANCELLED, None)
        return n

    def _end(self, wp_id: str, status: WaitStatus, output: Any) -> bool:
        with self._lock:
            wp = self._items.get(wp_id)
            if wp is None or wp.status is not WaitStatus.WAITING:
                return False
            wp.status, wp.output, wp.resolved_at = status, output, time.time()
        self._emit(wp, {WaitStatus.COMPLETED: "Done waiting", WaitStatus.TIMED_OUT: "Stopped waiting (time ran out)",
                        WaitStatus.CANCELLED: "Wait cancelled"}[status] + f": {wp.prompt}")
        return True

    # -- waiting on one --------------------------------------------------------

    async def wait(self, wp: Waitpoint, task_id: str, control: Optional[RunControl] = None) -> Waitpoint:
        """Until it ends. Pause and cancel are honoured while waiting (a
        cancelled job's wait is cancelled with it)."""
        if self.on_wait is not None and task_id:
            self.on_wait(task_id, wp)
        try:
            while wp.status is WaitStatus.WAITING:
                if control is not None:
                    await control.checkpoint()          # raises when the job is cancelled
                now = time.time()
                if wp.kind is WaitKind.TIME and wp.until is not None and now >= wp.until:
                    self.complete(wp.id, {"at": now})
                elif wp.kind is WaitKind.EVENT and self._happened(wp):
                    self.complete(wp.id, dict(wp.condition))
                elif wp.kind is not WaitKind.TIME and wp.until is not None and now >= wp.until:
                    self._end(wp.id, WaitStatus.TIMED_OUT, None)
                else:
                    await asyncio.sleep(POLL_S)
        except asyncio.CancelledError:
            self.cancel_for(task_id)
            raise
        finally:
            if self.on_resume is not None and task_id:
                await self.on_resume(task_id, wp)
        return wp

    @staticmethod
    def _happened(wp: Waitpoint) -> bool:
        path = wp.condition.get("file")
        if path:
            return os.path.exists(path) and (not wp.condition.get("nonempty") or os.path.getsize(path) > 0)
        return False

    # -- reading ----------------------------------------------------------------

    def get(self, wp_id: str) -> Optional[Waitpoint]:
        return self._items.get(wp_id)

    def pending(self, task_id: str = "") -> list[Waitpoint]:
        with self._lock:
            return [w for w in self._items.values() if w.status is WaitStatus.WAITING
                    and (not task_id or task_id in w.task_ids)]

    def _emit(self, wp: Waitpoint, message: str) -> None:
        self.events.emit(EventKind.WAITPOINT, message, task_id=wp.task_ids[0] if wp.task_ids else None,
                         data={"waitpoint": wp.to_dict()})
