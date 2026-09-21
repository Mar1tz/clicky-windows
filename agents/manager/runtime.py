"""AgentRuntime: the background thread every agent runs on.

Mirrors CompanionManager: one asyncio loop on a daemon thread, so agents
keep working while the user closes the chat panel, opens another
conversation or does anything else on the GUI thread. The GUI never
blocks on this loop; it talks to it through submit()/call() and hears
back through the EventBus.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from typing import Any, Coroutine, Optional, TypeVar

log = logging.getLogger("agents.runtime")

T = TypeVar("T")


class AgentRuntime:
    def __init__(self, name: str = "agent-runtime"):
        self._name = name
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._tasks: set[asyncio.Task] = set()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> "AgentRuntime":
        if self.running:
            return self
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            pending = [t for t in self._tasks if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()
            self._loop = None

    def stop(self, timeout: float = 5.0) -> None:
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._loop is not None and self._thread is not None and self._thread.is_alive()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("AgentRuntime is not running - call start()")
        return self._loop

    # -- scheduling ----------------------------------------------------------

    def submit(self, coro: Coroutine[Any, Any, T]) -> Future:
        """Fire-and-forget from any thread. Returns a concurrent Future for
        callers that want the result; exceptions are logged if nobody asks."""
        loop = self.loop
        fut: Future = Future()

        def _start() -> None:
            task = loop.create_task(coro)
            self._tasks.add(task)

            def _done(t: asyncio.Task) -> None:
                self._tasks.discard(t)
                if t.cancelled():
                    fut.cancel()
                    return
                exc = t.exception()
                if exc is not None:
                    log.error("runtime task failed: %r", exc)
                    fut.set_exception(exc)
                else:
                    fut.set_result(t.result())

            task.add_done_callback(_done)

        loop.call_soon_threadsafe(_start)
        return fut

    def call(self, coro: Coroutine[Any, Any, T], timeout: Optional[float] = 30) -> T:
        """Run `coro` on the loop and block the calling thread for the
        result. Never call from the loop's own thread."""
        if threading.current_thread() is self._thread:
            raise RuntimeError("AgentRuntime.call() from the runtime thread would deadlock")
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def call_soon(self, fn, *args: Any) -> None:
        self.loop.call_soon_threadsafe(fn, *args)

    def in_loop_thread(self) -> bool:
        return threading.current_thread() is self._thread
