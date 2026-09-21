"""EventBus: the one channel every agent component reports through.

Design:
  * publish() is synchronous and runs subscribers in the publisher's
    thread. Subscribers must be fast and must not raise (exceptions are
    swallowed and logged so one bad listener never kills an agent).
  * The Qt UI never subscribes directly - agents/ui wraps the bus in a
    QObject whose pyqtSignal re-emits on the GUI thread (same pattern as
    CompanionManager's signals).
  * A bounded in-memory ring buffer gives "last N events" for a fresh UI
    without touching disk. Durable history lives in persistence.TaskStore,
    which subscribes like anyone else.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Any, Callable, Iterable, Optional

from .models import Event, EventKind

log = logging.getLogger("agents.events")

Subscriber = Callable[[Event], None]
Unsubscribe = Callable[[], None]


class EventBus:
    def __init__(self, history_size: int = 2000):
        self._lock = threading.RLock()
        self._subs: list[tuple[int, Subscriber, Optional[frozenset[EventKind]], Optional[str]]] = []
        self._next = 1
        self._history: deque[Event] = deque(maxlen=history_size)

    # -- subscribe -----------------------------------------------------------

    def subscribe(
        self,
        callback: Subscriber,
        *,
        kinds: Optional[Iterable[EventKind]] = None,
        task_id: Optional[str] = None,
    ) -> Unsubscribe:
        """Register a listener. Filter by event kinds and/or a task id.
        Returns a zero-arg function that removes the subscription."""
        kind_set = frozenset(kinds) if kinds is not None else None
        with self._lock:
            sid = self._next
            self._next += 1
            self._subs.append((sid, callback, kind_set, task_id))

        def _off() -> None:
            with self._lock:
                self._subs = [s for s in self._subs if s[0] != sid]

        return _off

    def async_queue(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        kinds: Optional[Iterable[EventKind]] = None,
        task_id: Optional[str] = None,
    ) -> tuple[asyncio.Queue, Unsubscribe]:
        """Bridge to asyncio: events are put on the returned queue via
        loop.call_soon_threadsafe, so it is safe from any thread."""
        q: asyncio.Queue = asyncio.Queue()

        def _push(ev: Event) -> None:
            loop.call_soon_threadsafe(q.put_nowait, ev)

        return q, self.subscribe(_push, kinds=kinds, task_id=task_id)

    # -- publish -------------------------------------------------------------

    def publish(self, event: Event) -> None:
        with self._lock:
            self._history.append(event)
            subs = list(self._subs)
        for _sid, cb, kind_set, tid in subs:
            if kind_set is not None and event.kind not in kind_set:
                continue
            if tid is not None and event.task_id != tid:
                continue
            try:
                cb(event)
            except Exception:  # noqa: BLE001 - a listener must never break the agent
                log.exception("event subscriber raised on %s", event.kind.value)

    def emit(
        self,
        kind: EventKind,
        message: str = "",
        *,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
        level: str = "info",
    ) -> Event:
        ev = Event(kind=kind, message=message, task_id=task_id, agent_id=agent_id,
                   data=data or {}, level=level)
        self.publish(ev)
        return ev

    # -- history -------------------------------------------------------------

    def history(self, *, task_id: Optional[str] = None, limit: int = 500) -> list[Event]:
        with self._lock:
            evs = list(self._history)
        if task_id is not None:
            evs = [e for e in evs if e.task_id == task_id]
        return evs[-limit:]

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()
