"""AppControl: what this machine can be asked, and the safest way to ask it.

The agent does not memorise app knowledge. It asks this registry what
exists here ("Spotify: next, play_playlist, now_playing…"; "VS Code:
open_project, chat…"), then names one. Each action knows its own tier, so
the answer also says how quiet it will be: an app API, a deep link, a
system key, or - only if nothing better exists - the window itself.

Adapters are plain objects with `app`, `available()` and `actions()`, so
adding an app is one file, and an app that is not installed simply never
appears.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from .args import BindError, bind
from .model import (Action, Outcome, Refused, Risk, Tier, Unavailable,
                    Unverified)

log = logging.getLogger("agents.control")

REFRESH_S = 60.0


class AppControl:
    def __init__(self, adapters: Optional[list[Any]] = None) -> None:
        self._given = adapters
        self._adapters: list[Any] = []
        self._actions: dict[str, Action] = {}
        self._built_at = 0.0
        self._lock = threading.RLock()

    # -- catalogue ---------------------------------------------------------------
    def _build(self, force: bool = False) -> None:
        with self._lock:
            if self._adapters and not force and (time.monotonic() - self._built_at) < REFRESH_S:
                return
            adapters = list(self._given) if self._given is not None else _discover()
            actions: dict[str, Action] = {}
            for adapter in adapters:
                try:
                    for action in adapter.actions():
                        actions[action.key] = action
                except Exception:  # noqa: BLE001 - one broken adapter must not hide the rest
                    log.exception("adapter %s failed to describe itself", getattr(adapter, "app", "?"))
            self._adapters, self._actions, self._built_at = adapters, actions, time.monotonic()

    def refresh(self) -> None:
        self._build(force=True)

    def apps(self) -> list[dict[str, Any]]:
        self._build()
        out = []
        for a in self._adapters:
            names = sorted({x.name for x in self._actions.values() if x.app == a.app})
            out.append({"app": a.app, "name": getattr(a, "display_name", a.app),
                        "aliases": list(getattr(a, "aliases", ())), "actions": names})
        return out

    def actions(self, app: str = "", query: str = "") -> list[Action]:
        self._build()
        want = self.resolve_app(app) if app else ""
        q = (query or "").strip().lower()
        out = []
        for action in self._actions.values():
            if want and action.app != want:
                continue
            if q and q not in f"{action.key} {action.description}".lower():
                continue
            out.append(action)
        return sorted(out, key=lambda a: (a.app, a.tier, a.name))

    def resolve_app(self, name: str) -> str:
        """'vs code', 'vscode', 'code' -> vscode; 'music' -> spotify."""
        self._build()
        raw = (name or "").strip().lower()
        flat = raw.replace(" ", "").replace("-", "").replace("_", "")
        if not flat:
            return ""
        for a in self._adapters:
            if flat == a.app.replace(" ", ""):
                return a.app
        for a in self._adapters:
            names = [getattr(a, "display_name", a.app), *getattr(a, "aliases", ())]
            if any(flat == str(n).lower().replace(" ", "") for n in names):
                return a.app
        for a in self._adapters:
            names = [a.app, getattr(a, "display_name", a.app), *getattr(a, "aliases", ())]
            if any(flat in str(n).lower().replace(" ", "") or str(n).lower().replace(" ", "") in flat
                   for n in names):
                return a.app
        return ""

    def find(self, app: str, action: str) -> Optional[Action]:
        self._build()
        resolved = self.resolve_app(app)
        want = (action or "").strip().lower().replace(" ", "_").replace("-", "_")
        if not resolved or not want:
            return None
        direct = self._actions.get(f"{resolved}.{want}")
        if direct is not None:
            return direct
        for a in self._actions.values():
            if a.app == resolved and (want in a.aliases or want.replace("_", "") == a.name.replace("_", "")):
                return a
        for a in self._actions.values():                    # "skip" -> "next"
            if a.app == resolved and (want in a.name or a.name in want):
                return a
        return None

    def suggestions(self, app: str = "", action: str = "") -> str:
        self._build()
        resolved = self.resolve_app(app)
        if not resolved:
            apps = ", ".join(a.app for a in self._adapters)
            return f"I do not control {app!r}. Apps I can drive directly: {apps}. " \
                   "For anything else use open_app / ui_control, or the browser tools."
        names = sorted({a.name for a in self._actions.values() if a.app == resolved})
        return f"{resolved} has no action {action!r}. It can: {', '.join(names)}"

    # -- running ---------------------------------------------------------------------
    async def run(self, app: str, action: str, arguments: Optional[dict[str, Any]] = None) -> Outcome:
        found = self.find(app, action)
        if found is None:
            return Outcome(ok=False, message=self.suggestions(app, action), app=app, action=action)
        # Bind, never guess. An unrecognised name, a value outside the
        # declared choices or a missing required argument all stop the call
        # here with a sentence. Dropping them and running on defaults is
        # what turned "reduce the volume" into a volume increase.
        try:
            args = bind(found.key, found.params, dict(arguments or {}))
        except BindError as exc:
            log.warning("%s refused its arguments: %s", found.key, exc)
            return Outcome(ok=False, app=found.app, action=found.name, tier=found.tier,
                           message=str(exc), data={"expected": exc.expected})
        started = time.monotonic()
        attempts: list[str] = []
        before: Any = None
        if found.snapshot is not None:
            try:
                before = await found.snapshot()
            except Exception:  # noqa: BLE001 - no snapshot just means no proof
                log.debug("%s could not be sampled beforehand", found.key, exc_info=True)
        try:
            message = await found.run(**args)
        except Refused as exc:
            log.info("%s refused: %s", found.key, exc)
            return Outcome(ok=False, message=str(exc), app=found.app, action=found.name, tier=found.tier,
                           attempts=attempts)
        except Unavailable as exc:
            log.info("%s unavailable via %s: %s", found.key, found.tier.label, exc)
            attempts.append(f"{found.tier.label}: {exc}")
            return Outcome(ok=False, app=found.app, action=found.name, tier=found.tier, attempts=attempts,
                           message=f"{found.key} is not available on this machine: {exc}")
        except Exception as exc:  # noqa: BLE001 - an app can fail in any way
            log.exception("%s failed", found.key)
            return Outcome(ok=False, app=found.app, action=found.name, tier=found.tier, attempts=attempts,
                           message=f"{found.key} failed: {type(exc).__name__}: {exc}")
        tier = found.tier
        if isinstance(message, tuple):                      # an action may report the tier it used
            message, tier = message
        message = str(message)

        # Did it actually happen? Without this, "ok" only ever meant "the
        # call did not raise" - a media key tap reported success whether or
        # not anything was listening to it.
        verified: Optional[bool] = None
        if found.verify is not None:
            try:
                observed = await (found.verify(before=before, **args)
                                  if found.snapshot is not None
                                  else found.verify(**args))
            except Unverified as exc:
                log.info("%s ran but did not take: %s", found.key, exc)
                return Outcome(ok=False, message=str(exc), tier=tier, app=found.app,
                               action=found.name, attempts=attempts,
                               data={"verified": False})
            except Exception:  # noqa: BLE001 - a check that breaks is not a failure
                log.debug("%s could not be verified", found.key, exc_info=True)
                observed = None
            if observed:
                verified, message = True, str(observed)
            else:
                verified = None

        log.info("%s tier=%s args=%s -> ok%s in %dms", found.key, tier.label,
                 args or "{}", "" if verified else " (unverified)",
                 int((time.monotonic() - started) * 1000))
        return Outcome(ok=True, message=message, tier=tier, app=found.app,
                       action=found.name, attempts=attempts,
                       data={"verified": bool(verified)})


def _discover() -> list[Any]:
    from .apps import browsers, editors, media, windows_os
    out: list[Any] = []
    for module in (windows_os, media, editors, browsers):
        try:
            out.extend(module.adapters())
        except Exception:  # noqa: BLE001
            log.exception("could not load %s adapters", module.__name__)
    return out


_control: Optional[AppControl] = None


def control() -> AppControl:
    global _control
    if _control is None:
        _control = AppControl()
    return _control


def set_control(instance: Optional[AppControl]) -> None:
    """Tests inject a registry of fake adapters; nothing real is touched."""
    global _control
    _control = instance
