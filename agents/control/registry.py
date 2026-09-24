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
from .provider import AppProfile

log = logging.getLogger("agents.control")

REFRESH_S = 60.0

# Web addresses a desktop app on this machine owns. Sending one of these to
# a browser is doing the thing the long way round: open.spotify.com shows a
# page about a song, spotify.play plays it.
_OWNED_DOMAINS = {
    "open.spotify.com": "spotify",
    "spotify.com": "spotify",
    "vscode.dev": "vscode",
    "github.dev": "vscode",
}


class AppControl:
    def __init__(self, adapters: Optional[list[Any]] = None,
                 providers: Optional[list[Any]] = None) -> None:
        self._given = adapters
        self._given_providers = providers
        self._adapters: list[Any] = []
        # Capability providers (agents/control/provider.py): one mechanism
        # each, asked about ANY app. Their apps() is cheap and runs on
        # rebuild; their actions() can be slow and runs only when someone
        # names that app, cached below per app.
        self._providers: list[Any] = []
        self._profiles: dict[str, AppProfile] = {}
        self._provided: dict[str, tuple[float, list[Action]]] = {}
        self._actions: dict[str, Action] = {}
        # Adapters that raised while describing themselves. Remembered, not
        # just logged: one bad adapter used to disappear in silence, taking
        # every action that app had with it, and Tora then reached for a
        # browser instead — "it only thinks of things on the browsers, not
        # actual apps". A thing that is broken has to say so.
        self._broken: dict[str, str] = {}
        self._built_at = 0.0
        self._lock = threading.RLock()

    # -- catalogue ---------------------------------------------------------------
    def _build(self, force: bool = False) -> None:
        with self._lock:
            if self._adapters and not force and (time.monotonic() - self._built_at) < REFRESH_S:
                return
            adapters = list(self._given) if self._given is not None else _discover()
            actions: dict[str, Action] = {}
            broken: dict[str, str] = {}
            for adapter in adapters:
                name = getattr(adapter, "app", "?")
                try:
                    for action in adapter.actions():
                        actions[action.key] = action
                except Exception as exc:  # noqa: BLE001 - one bad adapter must not hide the rest
                    log.exception("adapter %s failed to describe itself", name)
                    broken[name] = f"{type(exc).__name__}: {exc}"
            providers = (list(self._given_providers)
                         if self._given_providers is not None else _discover_providers())
            profiles: dict[str, AppProfile] = {}
            for provider in providers:
                try:
                    for profile in provider.apps():
                        # First provider to name an app wins; an adapter for
                        # the same app still outranks all of them.
                        profiles.setdefault(profile.app, profile)
                except Exception as exc:  # noqa: BLE001 - one provider must not sink the rest
                    log.exception("provider %s failed to list apps",
                                  getattr(provider, "name", "?"))
                    broken[f"provider:{getattr(provider, 'name', '?')}"] = str(exc)
            self._adapters, self._actions = adapters, actions
            self._providers, self._profiles = providers, profiles
            self._provided = {}
            self._broken, self._built_at = broken, time.monotonic()
            if broken:
                log.error("NO actions available this session for: %s",
                          ", ".join(sorted(broken)))

    def refresh(self) -> None:
        self._build(force=True)

    def apps(self, include_discovered: bool = True) -> list[dict[str, Any]]:
        """Every app that can be named. Adapters first: they are the ones
        with hand-written actions, so they are what "what can you do" should
        lead with. Discovered apps follow, with no action list — finding out
        what one of THEM can do means asking, and asking costs half a
        second, so it happens when someone names it, not now."""
        self._build()
        out, seen = [], set()
        for a in self._adapters:
            names = sorted({x.name for x in self._actions.values() if x.app == a.app})
            seen.add(a.app)
            out.append({"app": a.app, "name": getattr(a, "display_name", a.app),
                        "aliases": list(getattr(a, "aliases", ())), "actions": names})
        if include_discovered:
            for profile in self._profiles.values():
                if profile.app in seen:
                    continue
                out.append({"app": profile.app, "name": profile.display_name,
                            "aliases": list(profile.aliases), "actions": [],
                            "via": profile.source})
        return out

    def profiles(self) -> dict[str, AppProfile]:
        self._build()
        return dict(self._profiles)

    def _provider_actions(self, app: str) -> list[Action]:
        """What the providers say `app` can do, cached.

        Only ever called for an app someone has NAMED. Walking a UI tree is
        half a second; doing it for every app on the machine would be
        minutes, and putting the result in front of the model every turn
        would cost more than the conversation.
        """
        if not app:
            return []
        with self._lock:
            hit = self._provided.get(app)
            if hit and (time.monotonic() - hit[0]) < REFRESH_S:
                return list(hit[1])
        found: list[Action] = []
        for provider in self._providers:
            try:
                found.extend(provider.actions(app) or [])
            except Exception:  # noqa: BLE001 - a provider with nothing to say is fine
                log.debug("provider %s could not describe %s",
                          getattr(provider, "name", "?"), app, exc_info=True)
        # An adapter's action always outranks a synthesised one of the same
        # name: it was written by someone who knew the app.
        found = [a for a in found if f"{a.app}.{a.name}" not in self._actions]
        with self._lock:
            self._provided[app] = (time.monotonic(), list(found))
        return found

    def actions(self, app: str = "", query: str = "") -> list[Action]:
        self._build()
        want = self.resolve_app(app) if app else ""
        if app and not want:
            # Asked about an app nobody has. Returning every action of every
            # app - which is what an empty filter did - is worse than
            # returning nothing: the model reads it as "here is what that
            # app can do" and picks one belonging to something else.
            return []
        q = (query or "").strip().lower()
        pool = list(self._actions.values())
        if want:
            pool += self._provider_actions(want)
        out = []
        for action in pool:
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
        # Nothing hand-written matches. Everything a provider discovered is
        # addressable too — but there are 223 of them here, so the matching
        # has to be stricter than it can afford to be over eight adapters.
        # A bare substring rule lets "word" claim "WordPad" and "a" claim
        # whatever sorts first; both are worse than saying "I don't know".
        for profile in self._profiles.values():
            if any(flat == _flat(n) for n in profile.names):
                return profile.app
        if len(flat) < 3:
            return ""
        spoken = raw.split()
        best, best_rank = "", ()
        for profile in self._profiles.values():
            for n in profile.names:
                words = str(n).lower().split()
                flat_n = _flat(n)
                if not flat_n:
                    continue
                if all(w in words for w in spoken):          # "word" -> Microsoft Word
                    # A whole word beats a prefix: someone saying "word"
                    # means Word, not WordPad, even though WordPad starts
                    # with it.
                    rank = (0, len(flat_n))
                elif flat_n.startswith(flat):                # "note" -> Notepad
                    rank = (1, len(flat_n))
                elif flat in flat_n:                         # "paint" -> Paint 3D
                    rank = (2, len(flat_n))
                else:
                    continue
                if not best or rank < best_rank:
                    best, best_rank = profile.app, rank
        return best

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
        provided = self._provider_actions(resolved)
        for a in provided:
            if a.name == want or want in a.aliases:
                return a
        for a in provided:
            if want in a.name or a.name in want:
                return a
        return None

    def suggestions(self, app: str = "", action: str = "") -> str:
        self._build()
        resolved = self.resolve_app(app)
        if not resolved:
            apps = ", ".join(a.app for a in self._adapters)
            return f"I do not control {app!r}. Apps I can drive directly: {apps}. " \
                   "For anything else use open_app / ui_control, or the browser tools."
        names = sorted({a.name for a in self._actions.values() if a.app == resolved}
                       | {a.name for a in self._provider_actions(resolved)})
        if resolved in self._broken and names:
            # A generic provider can still open it, so the app is not
            # actionless - but the reason most of it is missing has to
            # survive, or the next person debugs the symptom.
            return (f"{resolved}'s own controls failed to load this session "
                    f"({self._broken[resolved]}), so only the generic ones are "
                    f"left: {', '.join(names)}. Say that restarting Tora "
                    f"usually brings the rest back; do not silently use a "
                    f"browser instead.")
        if not names:
            # It used to say "It can: " and then nothing, and the model read
            # that as "use something else" - which is how a Spotify request
            # turned into a web search in a browser.
            why = self._broken.get(resolved, "it registered no actions")
            return (f"{resolved} is installed but its controls failed to load "
                    f"this session ({why}). Tell the user that plainly - do NOT "
                    f"quietly do it through a browser instead. Restarting Tora "
                    f"usually fixes it.")
        return f"{resolved} has no action {action!r}. It can: {', '.join(names)}"

    def broken(self) -> dict[str, str]:
        """Apps whose actions could not be built this session."""
        self._build()
        return dict(self._broken)

    def app_for_url(self, url: str) -> str:
        """The app that owns this web address, if we control it directly.

        Opening open.spotify.com in a browser is not playing a song, it is
        showing a web page about one - and that is what "it only thinks of
        things on the browsers" looked like from the outside. If a real app
        owns the domain, the browser is the wrong answer.
        """
        self._build()
        text = (url or "").strip().lower()
        if not text:
            return ""
        for domain, app in _OWNED_DOMAINS.items():
            if domain not in text:
                continue
            if any(a.app == app for a in self._actions.values()):
                return app
        return ""

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


def _flat(name: str) -> str:
    return str(name or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def _discover_providers() -> list[Any]:
    """Every capability provider that works on this machine. UC-01 ships the
    protocol; the providers themselves arrive one milestone at a time."""
    from . import providers
    return providers.available()


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
