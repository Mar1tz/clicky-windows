"""
The autonomous guide — walks the user through a multi-step task and advances
on its own, watching the screen instead of waiting for "next".

    present step ─► watch its completion condition ─► it comes true ─► advance
         ▲                                                               │
         └───────────────────────── next step ◄─────────────────────────┘

The whole point is that the user never has to say "next". Each planned step
carries a machine-checkable completion condition (ai.gemini_planner.Step ·
ai.guide_state); this runner polls that condition a few times a second and,
the moment the step is done, speaks the next one and points at it.

Everything it needs from the app is injected as callbacks, so the loop below
has no idea what a screenshot or a websocket is and can be tested against
scripted state:

    speak_step(idx)  say step idx aloud and point at its target
    check(step)      is this step done? True / False / None (can't tell)
    say(text)        speak one line (a nudge, "you're done")
    is_busy()        True while Tora is answering something else
    on_stop()        the guide ended — clean up

Three details earn their keep:

  • Arming. A condition describes the state AFTER the step is performed, and
    usually that state is false when the step begins ("Background" is not on
    screen until you open Personalization). The runner only fires on a
    false→true transition, so a condition that is already true at the start —
    which cannot tell "done" from "was already like that" — never skips the
    step instantly. It waits, nudges, and lets the user say next.

  • Debounce. A condition has to hold for two consecutive polls before the
    runner believes it, so a control that flickers into the tree for one
    frame does not skip a step.

  • Not talking over you. If you ask a question mid-guide, is_busy() is true
    while Tora answers, and the runner holds the next step until it is quiet
    again rather than speaking on top of the reply.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

_log = logging.getLogger("tora.guide")

Command = str   # "next" | "back" | "repeat" | "stop"


class GuideRunner:
    def __init__(
        self,
        guide,
        *,
        speak_step: Callable[[int], Awaitable[None]],
        check: Callable[[object], Awaitable[Optional[bool]]],
        say: Callable[[str], Awaitable[None]],
        is_busy: Callable[[], bool],
        on_stop: Callable[[], None],
        look_ahead: Optional[Callable[[int], Awaitable[Optional[int]]]] = None,
        on_stuck: Optional[Callable[[int], Awaitable[None]]] = None,
        observer=None,
        start_index: int = 0,
        poll_s: float = 0.3,
        confirm_polls: int = 2,
        step_timeout_s: float = 35.0,
        stuck_after_s: float = 15.0,
        present_delay_s: float = 0.8,
        repeat_guard_s: float = 1.5,
    ):
        self._guide = guide
        self._speak_step = speak_step
        self._check = check
        self._say = say
        self._is_busy = is_busy
        self._on_stop = on_stop
        # Detects the user getting AHEAD of the guide (doing two steps quickly,
        # or clicking past one). Returns the furthest later step already done,
        # or None. Optional — without it the guide only ever moves one at a time.
        self._look_ahead = look_ahead
        # Called once when a step stops making progress — the screen has not
        # changed at all for a while and the step is still outstanding. That
        # is a user who is stuck, as opposed to one who is simply taking their
        # time, and the difference is worth acting on.
        self._on_stuck = on_stuck
        self._observer = observer

        self._poll_s = poll_s
        self._confirm_polls = confirm_polls
        self._step_timeout_s = step_timeout_s
        self._stuck_after_s = stuck_after_s
        self._present_delay_s = present_delay_s
        # Look-ahead is a little expensive (it reads screen state), so it runs
        # once every few polls, not every one.
        self._lookahead_every = 6

        # Repeat guard for request(); see the note there.
        self._repeat_guard_s = repeat_guard_s
        self._last_command: Optional[Command] = None
        self._last_command_at = 0.0

        self._start_index = max(0, start_index)
        self._idx = -1
        self._completed = False
        self._pending: Optional[Command] = None
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Public ───────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def index(self) -> int:
        return self._idx

    @property
    def completed(self) -> bool:
        """True if the guide reached the end, False if stopped part-way."""
        return self._completed

    @property
    def total(self) -> int:
        return len(self._guide.steps)

    async def start(self) -> None:
        if self.running:
            return
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.ensure_future(self._run())

    def request(self, command: Command) -> bool:
        """Manual override from the user — next / back / repeat / stop.

        Safe to call from ANY thread, and it is: Esc arrives on the keyboard
        hook's thread and the tray menu on Qt's, while _wake belongs to the
        manager's asyncio loop. asyncio.Event.set() is not thread-safe, so the
        wake is marshalled onto the loop; the plain _pending attribute is what
        the loop actually reads, so the command is never lost either way.

        Returns False when the command was swallowed as a repeat. The live
        model fires a tool three times in a second when it is unsure whether
        the first one landed — the log has three guide_step calls inside 1.4
        seconds — and three "next"es on a two-step guide end the walkthrough
        before the user has done anything. One "next" is one step.
        """
        now = time.monotonic()
        if (command == self._last_command
                and now - self._last_command_at < self._repeat_guard_s):
            _log.info("ignoring a repeated '%s' %.1fs after the last one",
                      command, now - self._last_command_at)
            return False
        self._last_command, self._last_command_at = command, now
        self._pending = command
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._wake.set)
                return True
            except RuntimeError:
                pass          # loop already closing — fall through
        self._wake.set()
        return True

    def stop(self) -> None:
        self.request("stop")

    # ── The loop ─────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        try:
            # Let whatever announced the guide ("sure, let me walk you
            # through it") finish before the first step is spoken.
            await self._interruptible_sleep(self._present_delay_s)
            if self._pending == "stop":
                return
            self._idx = min(self._start_index, self.total - 1)
            await self._show(self._idx)

            while 0 <= self._idx < self.total:
                outcome = await self._await_step(self._guide.steps[self._idx])
                if outcome == "stop":
                    break
                if outcome == "repeat":
                    await self._show(self._idx)
                    continue
                if outcome == "back":
                    self._idx = max(0, self._idx - 1)
                    await self._show(self._idx)
                    continue
                if outcome.startswith("jump:"):
                    # The user got ahead — pick up at the step they're on.
                    self._idx = int(outcome.split(":", 1)[1])
                    if self._idx >= self.total - 1:
                        self._completed = True
                        await self._say_safe(
                            "Looks like you're already there — nicely done!"
                        )
                        break
                    self._idx += 1
                    await self._show(self._idx)
                    continue
                # "done" or "next"
                self._idx += 1
                if self._idx >= self.total:
                    self._completed = True
                    await self._say_safe(
                        "That's the last step — you're all done!"
                    )
                    break
                await self._show(self._idx)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("guide runner crashed")
        finally:
            # on_stop runs while _idx still holds where we stopped, so a caller
            # can save it for resume; only then is the index cleared.
            try:
                self._on_stop()
            except Exception:
                _log.debug("guide on_stop failed", exc_info=True)
            self._idx = -1

    async def _show(self, idx: int) -> None:
        await self._wait_idle()
        try:
            await self._speak_step(idx)
        except Exception:
            _log.debug("speak_step failed", exc_info=True)

    async def _await_step(self, step) -> str:
        """Wait until the step is done (or the user takes over). Returns the
        outcome: done | next | back | repeat | stop."""
        # Arm only if the condition is currently NOT satisfied — otherwise we
        # cannot distinguish "the user did it" from "it already looked like
        # this", and firing would skip the step.
        # Armed unless the check is ALREADY true at step start — that would
        # mean the screen looked done before the user did anything, and
        # firing would skip the step. An unsure (None) start still arms:
        # the watcher reports None while it waits for the user to act.
        armed = (await self._check_quiet(step)) is not True
        seen_true = 0
        started = time.monotonic()
        nudged = False
        helped = False
        polls = 0
        if self._observer is not None:
            self._observer.reset()

        while True:
            cmd = self._take_command()
            if cmd is not None:
                return cmd

            # Has the user jumped ahead? Checked occasionally — it reads screen
            # state, so not on every poll.
            polls += 1
            if self._look_ahead is not None and polls % self._lookahead_every == 0:
                try:
                    j = await self._look_ahead(self._idx)
                except Exception:
                    j = None
                if j is not None and j > self._idx and not self._is_busy():
                    return f"jump:{j}"

            val = await self._check_quiet(step)
            if val is True:
                if armed:
                    seen_true += 1
                    if seen_true >= self._confirm_polls and not self._is_busy():
                        return "done"
            else:
                seen_true = 0
                if val is False:
                    armed = True     # went false — now a real transition can fire

            # Nothing on screen has moved and the step is still not done —
            # that is stuck, not slow. Offer a hand once, early, instead of
            # waiting out the full timeout in silence.
            if (not helped and self._on_stuck is not None
                    and self._observer is not None):
                self._observer.poll()
                if self._observer.idle_seconds() > self._stuck_after_s:
                    helped = True
                    if not self._is_busy():
                        try:
                            await self._on_stuck(self._idx)
                        except Exception:
                            _log.debug("on_stuck failed", exc_info=True)
                        self._observer.reset()

            if not nudged and (time.monotonic() - started) > self._step_timeout_s:
                await self._say_safe(
                    "Take your time. Say 'next' when you've done it, 'back' to "
                    "hear it again, or 'stop' to finish here."
                )
                nudged = True

            await self._interruptible_sleep(self._poll_s)

    async def _check_quiet(self, step) -> Optional[bool]:
        try:
            return await self._check(step)
        except Exception:
            _log.debug("guide check failed", exc_info=True)
            return None

    def _take_command(self) -> Optional[Command]:
        cmd, self._pending = self._pending, None
        self._wake.clear()
        if cmd in ("next", "back", "repeat", "stop"):
            return cmd
        return None

    async def _interruptible_sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        # Left set for _take_command to observe on the next loop turn.

    async def _wait_idle(self, timeout: float = 12.0) -> None:
        start = time.monotonic()
        while self._is_busy() and (time.monotonic() - start) < timeout:
            await asyncio.sleep(0.15)

    async def _say_safe(self, text: str) -> None:
        await self._wait_idle()
        try:
            await self._say(text)
        except Exception:
            _log.debug("guide say failed", exc_info=True)
