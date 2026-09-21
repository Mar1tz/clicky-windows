"""Computer control - move, click, type - on the user's real desktop.

Two rules shape everything here.

**Targets are names, not coordinates.** `click("Save")` resolves through
`ai/hybrid_pointer.find_target` - Windows UI Automation first, OCR second,
both local and sub-100 ms - for the same reason `point_at` takes a name:
Windows already knows where "Save" is to the pixel, and a model guessing
pixels from a screenshot is slower and wrong more often. Coordinates are
accepted, but only as a fallback for things nothing can name.

**Nothing here ever runs unasked.** Every acting tool sets `always_ask`,
which `Registry.execute` honours above any policy, ceiling or standing
"always allow": the registry asks a human every single time. That is not
a default the user can lower; it is the floor. On top of that a global
kill switch - wired to Esc - trips every computer-control action into a
failure and cancels the tasks behind them.

Input goes through a small `Backend` so tests inject a recorder and
never move the real mouse. The real one is pynput, already a dependency.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Callable, Optional

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec

log = logging.getLogger("agents.tools.computer")

CATEGORY = "computer"
TYPE_DELAY_S = 0.01        # per character, so apps see keystrokes rather than a paste


# -- kill switch ---------------------------------------------------------------

class KillSwitch:
    """Global stop for computer control. Trip it and every action fails
    until it is reset; `on_trip` callbacks let the manager cancel tasks."""

    def __init__(self) -> None:
        self._tripped = threading.Event()
        self._callbacks: list[Callable[[], None]] = []

    @property
    def tripped(self) -> bool:
        return self._tripped.is_set()

    def trip(self) -> None:
        if self._tripped.is_set():
            return
        self._tripped.set()
        log.warning("computer control kill switch tripped")
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception:  # noqa: BLE001
                log.exception("kill switch callback failed")

    def reset(self) -> None:
        self._tripped.clear()

    def on_trip(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)


kill_switch = KillSwitch()


# -- input backends ------------------------------------------------------------

class Backend:
    """What a tool needs from the OS. Real one below; tests inject a fake."""

    def move(self, x: int, y: int) -> None: ...
    def click(self, x: int, y: int, *, button: str = "left", count: int = 1) -> None: ...
    def type_text(self, text: str) -> None: ...
    def press(self, keys: list[str]) -> None: ...
    def position(self) -> tuple[int, int]: ...


class PynputBackend(Backend):
    def __init__(self) -> None:
        from pynput import keyboard, mouse
        self._mouse = mouse.Controller()
        self._kb = keyboard.Controller()
        self._Button = mouse.Button
        self._Key = keyboard.Key

    def move(self, x, y):
        self._mouse.position = (x, y)

    def click(self, x, y, *, button="left", count=1):
        self._mouse.position = (x, y)
        time.sleep(0.05)
        self._mouse.click(getattr(self._Button, button, self._Button.left), count)

    def type_text(self, text):
        for ch in text:
            if ch == "\n":
                self._kb.press(self._Key.enter)
                self._kb.release(self._Key.enter)
            else:
                self._kb.type(ch)
            time.sleep(TYPE_DELAY_S)

    def press(self, keys):
        resolved = [getattr(self._Key, k.lower(), None) or k for k in keys]
        for k in resolved:
            self._kb.press(k)
        for k in reversed(resolved):
            self._kb.release(k)

    def position(self):
        x, y = self._mouse.position
        return int(x), int(y)


_backend: Optional[Backend] = None


def get_backend() -> Backend:
    global _backend
    if _backend is None:
        _backend = PynputBackend()
    return _backend


def set_backend(backend: Optional[Backend]) -> None:
    """Tests inject a recorder; None restores the real one lazily."""
    global _backend
    _backend = backend


# -- target resolution ---------------------------------------------------------

def resolve_target(name: str) -> Optional[dict[str, Any]]:
    """Name -> {x, y, label, source, confidence} via the pointing stack.

    Runs in a thread by callers: UIA walks the live window tree, OCR
    captures the screen, and neither belongs on the agent loop.
    """
    try:
        from ai.hybrid_pointer import find_target
    except Exception:  # noqa: BLE001 - pointing stack unavailable (headless CI)
        return None
    target = find_target(name)
    if target is None:
        return None
    return {"x": target.x, "y": target.y, "label": target.label,
            "source": target.source, "confidence": round(target.confidence, 2)}


_resolver: Callable[[str], Optional[dict[str, Any]]] = resolve_target


def set_resolver(fn: Optional[Callable[[str], Optional[dict[str, Any]]]]) -> None:
    global _resolver
    _resolver = fn or resolve_target


async def _locate(args: dict[str, Any]) -> tuple[Optional[tuple[int, int]], dict[str, Any], str]:
    """(xy, details, error) from either a name or explicit coordinates."""
    name = str(args.get("name") or "").strip()
    if name:
        found = await asyncio.to_thread(_resolver, name)
        if found is None:
            return None, {}, (f"could not find {name!r} on screen. Use find_on_screen to check "
                              "what is visible, or give x/y if it has no name.")
        return (int(found["x"]), int(found["y"])), found, ""
    if "x" in args and "y" in args:
        try:
            return (int(args["x"]), int(args["y"])), {"source": "coordinates"}, ""
        except (TypeError, ValueError):
            return None, {}, "x and y must be integers"
    return None, {}, "give a name (preferred) or x and y"


# -- tools ---------------------------------------------------------------------

class _ComputerTool(Tool):
    always_ask = True            # the floor: see Registry.execute

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if kill_switch.tripped:
            return self.fail("computer control is stopped (kill switch). Reset it to continue.")
        try:
            return await self.act(args, ctx)
        except Exception as exc:  # noqa: BLE001
            return self.fail(f"{type(exc).__name__}: {str(exc)[:200]}")

    async def act(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError


_TARGET_PROPS = {
    "name": {"type": "string", "description": "What to target, by its visible name, e.g. 'Save'. Preferred."},
    "x": {"type": "integer", "description": "Fallback: screen X in pixels."},
    "y": {"type": "integer", "description": "Fallback: screen Y in pixels."},
}


class FindOnScreen(Tool):
    """Look before you leap: SAFE, because it moves nothing."""
    spec = make_spec(
        "find_on_screen", "Locate a control by name without touching it. Returns its position.",
        risk=RiskLevel.SAFE, category=CATEGORY,
        properties={"name": {"type": "string", "description": "Visible name, e.g. 'File menu'."}},
        required=["name"],
    )

    async def run(self, args, ctx):
        name = str(args.get("name") or "").strip()
        found = await asyncio.to_thread(_resolver, name)
        if found is None:
            return self.fail(f"could not find {name!r} on screen", found=False)
        return self.ok(f"{found.get('label', name)} at ({found['x']}, {found['y']}) via "
                       f"{found.get('source')}", found=True, **found)


class MoveCursor(_ComputerTool):
    spec = make_spec(
        "move_cursor", "Move the mouse to a named control or to coordinates.",
        risk=RiskLevel.HIGH, category=CATEGORY, properties=_TARGET_PROPS,
    )

    async def act(self, args, ctx):
        xy, found, err = await _locate(args)
        if err:
            return self.fail(err)
        await asyncio.to_thread(get_backend().move, *xy)
        return self.ok(f"moved to ({xy[0]}, {xy[1]})", **{**found, "x": xy[0], "y": xy[1]})


class Click(_ComputerTool):
    spec = make_spec(
        "click", "Click a named control (or coordinates). Left button unless told otherwise.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={**_TARGET_PROPS,
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "double": {"type": "boolean", "description": "Double-click. Default false."}},
    )

    async def act(self, args, ctx):
        xy, found, err = await _locate(args)
        if err:
            return self.fail(err)
        button = str(args.get("button") or "left")
        count = 2 if args.get("double") else 1
        await asyncio.to_thread(get_backend().click, *xy, button=button, count=count)
        what = found.get("label") or args.get("name") or f"({xy[0]}, {xy[1]})"
        return self.ok(f"{'double-' if count == 2 else ''}{button}-clicked {what}",
                       **{**found, "x": xy[0], "y": xy[1], "button": button, "count": count})


class TypeText(_ComputerTool):
    spec = make_spec(
        "type_text", "Type text at the current keyboard focus. Newlines press Enter.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"text": {"type": "string", "description": "What to type."}},
        required=["text"],
    )

    async def act(self, args, ctx):
        text = str(args.get("text") or "")
        if not text:
            return self.fail("nothing to type")
        await asyncio.to_thread(get_backend().type_text, text)
        preview = text if len(text) <= 60 else text[:57] + "…"
        return self.ok(f"typed {preview!r}", chars=len(text))


class KeyPress(_ComputerTool):
    spec = make_spec(
        "key_press", "Press a key or chord, e.g. ['ctrl', 's'] or ['enter'].",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"keys": {"type": "array", "items": {"type": "string"},
                             "description": "Keys held together, in order."}},
        required=["keys"],
    )

    async def act(self, args, ctx):
        keys = [str(k) for k in (args.get("keys") or []) if str(k).strip()]
        if not keys:
            return self.fail("no keys given")
        await asyncio.to_thread(get_backend().press, keys)
        return self.ok("pressed " + "+".join(keys), keys=keys)


class Screenshot(Tool):
    """Read-only capture into the workspace. SAFE: it changes nothing."""
    spec = make_spec(
        "screenshot", "Capture the primary screen to a JPEG in the workspace.",
        risk=RiskLevel.SAFE, category=CATEGORY,
        properties={"path": {"type": "string", "description": "Default screenshots/screen.jpg"}},
    )

    async def run(self, args, ctx):
        rel = str(args.get("path") or "screenshots/screen.jpg")
        target, err = self.guard(ctx, rel)
        if err:
            return err
        try:
            import base64

            from screen.capture import capture_primary
            shot = await asyncio.to_thread(capture_primary)
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            # ScreenShot carries the downscaled frame as base64 JPEG - the
            # same bytes the vision model sees, which is what an agent
            # reasoning about the screen should be looking at too.
            if not target.lower().endswith(".jpg") and not target.lower().endswith(".jpeg"):
                target = os.path.splitext(target)[0] + ".jpg"
            with open(target, "wb") as fh:
                fh.write(base64.b64decode(shot.base64_jpeg))
        except Exception as exc:  # noqa: BLE001
            return self.fail(f"screenshot failed: {type(exc).__name__}: {str(exc)[:160]}")
        return self.ok(f"saved {target} ({shot.width}x{shot.height})", path=target,
                       width=shot.width, height=shot.height)


def tools() -> list[Tool]:
    return [FindOnScreen(), MoveCursor(), Click(), TypeText(), KeyPress(), Screenshot()]
