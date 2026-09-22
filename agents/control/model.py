"""What an app can do, and the ladder of ways to make it happen.

The whole design is one idea: **the mouse is the last resort.** "Spotify,
next song" should be a media key the system routes to whatever is playing
- no focus, no cursor, no screenshot. "VS Code, open this project and ask
the chat to explain it" should be `code -r <path>` and `code chat "..."`.
Only when an app offers nothing do we fall back to driving its window,
and only then does the user's screen get touched.

So every action carries a `Tier`: the lower the number, the less it
disturbs the person and the more reliably it works. The registry tries
the tiers in order and reports which one actually did the job, so the
timeline can say "via the VS Code CLI" instead of "clicked something".

Microsoft's UFO2 (arXiv 2504.14603) measured the same thing: giving an
agent an app's native API alongside its GUI raised success rates and cut
the number of steps by more than half. This is that hybrid layer, with
Windows deep links and the system media session added, because those
cover most of what a person asks for on a desktop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable, Optional, Protocol


class Tier(IntEnum):
    """How an action reaches the app. Lower is better: quieter, faster,
    more reliable, and it works while the window is hidden."""
    APP_ACTION = 1      # Windows 11 App Actions / MCP - the app declares it (future)
    API = 2            # the app's own API or CLI: `code chat`, Spotify Web API
    DEEP_LINK = 3      # a registered URI: spotify:playlist:…, vscode://file/…, ms-settings:
    SYSTEM = 4         # the OS does it: media keys, volume, window management, clipboard
    UI_BACKGROUND = 5  # UI Automation invoke on a control - no cursor, no focus stolen
    UI_FOREGROUND = 6  # bring the window up and really click/type (the user's screen moves)
    VISION = 7         # find it on screen and click a pixel - the last resort

    @property
    def label(self) -> str:
        return {1: "app action", 2: "app API", 3: "deep link", 4: "system",
                5: "background UI", 6: "on-screen input", 7: "vision"}[int(self)]

    @property
    def disturbs_the_user(self) -> bool:
        return self >= Tier.UI_FOREGROUND


class Risk(IntEnum):
    """What a person would say if it happened by mistake."""
    PASSIVE = 0        # only looks: what is playing, which windows are open
    REVERSIBLE = 1     # next song, volume, focus a window - undone in a second
    CHANGES = 2        # opens a project, starts a playlist, sends a keystroke into an app
    SENSITIVE = 3      # sends a message, posts, pays, deletes - someone else sees it

    @property
    def needs_confirmation(self) -> bool:
        return self >= Risk.REVERSIBLE


@dataclass(frozen=True)
class Param:
    """One argument an action takes.

    `choices` is enforced, not decoration - see agents/control/args.py.

    A parameter with `choices` usually decides WHAT the action does:
    volume's `direction`, media's `what`. Those must be `required`, with no
    default in the function signature, because a default turns a dropped
    argument into a different action - "reduce the volume" came back as a
    volume increase exactly this way. Set `modifier=True` for the rarer
    kind that only changes HOW something is done (an editor chat's `mode`),
    where a default is safe. test_control_arguments.py enforces the rule.
    """
    name: str
    description: str = ""
    required: bool = False
    type: str = "string"
    choices: tuple[str, ...] = ()
    modifier: bool = False


@dataclass
class Action:
    """One thing an app can do, and how to do it.

    `run` returns a short sentence for the user; raising `Unavailable`
    means "this route is not open on this machine right now", which makes
    the registry try the next tier rather than fail the task.
    """
    app: str
    name: str                                   # "next", "open_project", "chat"
    description: str
    run: Callable[..., Awaitable[str]]
    tier: Tier = Tier.API
    risk: Risk = Risk.CHANGES
    params: tuple[Param, ...] = ()
    needs_app_running: bool = False
    examples: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.app}.{self.name}"

    def describe(self) -> dict[str, Any]:
        return {"app": self.app, "action": self.name, "description": self.description,
                "tier": self.tier.label, "risk": self.risk.name.lower(),
                "params": [{"name": p.name, "required": p.required, "description": p.description,
                            "choices": list(p.choices)} for p in self.params],
                "examples": list(self.examples)}


class Unavailable(RuntimeError):
    """This route cannot run here (app not installed, CLI missing, no URI
    handler, control not found). The registry moves down a tier."""


class Refused(RuntimeError):
    """The action is understood but must not run (no such project, the
    query matched nothing). Trying a lower tier would not help."""


@dataclass
class Outcome:
    ok: bool
    message: str
    tier: Optional[Tier] = None
    action: str = ""
    app: str = ""
    attempts: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


class AppAdapter(Protocol):
    """An app's own way of being asked. One file per app."""

    app: str                      # "spotify"
    display_name: str             # "Spotify"
    aliases: tuple[str, ...]      # ("music", "spotify desktop")

    def available(self) -> bool: ...
    def actions(self) -> list[Action]: ...
