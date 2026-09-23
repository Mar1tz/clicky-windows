"""Capability providers: how Tora learns what an app can do without being told.

An adapter (`agents/control/apps/*.py`) is a file someone wrote about one
app. There are eight of them. This machine offers 223 launchable apps, 298
registered URI schemes and, in a single File Explorer window, 127 controls
that already declare themselves actionable through UI Automation. An app
nobody wrote a file about is invisible — and "invisible" is
indistinguishable, from the outside, from "Tora cannot do that". That is
how a request to play a song became a browser tab last week.

A **provider** inverts it. Instead of describing one app, a provider knows
one *mechanism* — launching, deep links, shell verbs, the accessibility
tree — and can be asked about any app at all:

    provider.apps()            what apps do you know of?      (cheap)
    provider.actions("notepad")  what can THAT one do?        (on demand)

The split matters more than it looks. `apps()` runs whenever the catalogue
is rebuilt, so it has to be cheap and is cached; `actions()` may walk a UI
tree for half a second, so it runs **only when someone names that app** and
its answer is cached per app. Nothing enumerates 223 apps' actions, ever:
that would be slower than the conversation and, since the tool payload is
re-billed every turn (see M28), more expensive than the answer.

Adapters keep working exactly as they are. Where both a provider and an
adapter describe the same action, the adapter wins — it was written by
someone who knew that app, which is worth more than anything synthesised.

See docs/agents/UNIVERSAL_CONTROL.md for the measurements behind all this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .model import Action


@dataclass(frozen=True)
class AppProfile:
    """An app this machine has, discovered rather than declared.

    `app` is the id the model says ("notepad"); `display_name` is what a
    person calls it ("Notepad"); `aliases` are the other things they might
    say. `launch` is whatever the provider needs to start it later — a
    path, an AppUserModelID, a URI — and is opaque to everyone else.
    """
    app: str
    display_name: str = ""
    aliases: tuple[str, ...] = ()
    source: str = ""                 # which provider found it
    launch: str = ""                 # path / AUMID / URI, provider's business
    running: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.display_name:
            object.__setattr__(self, "display_name", self.app)

    @property
    def names(self) -> tuple[str, ...]:
        """Everything this app could reasonably be called."""
        return tuple(dict.fromkeys(
            [self.app, self.display_name, *self.aliases]))


@runtime_checkable
class CapabilityProvider(Protocol):
    """One mechanism, asked about any app.

    Implementations live in `agents/control/providers/`. Both methods must
    be total: an app a provider knows nothing about gets an empty list, not
    an exception — one provider having an opinion must never stop another
    from answering.
    """

    name: str

    def apps(self) -> list[AppProfile]:
        """Every app this mechanism can reach. Cheap; called on rebuild."""
        ...

    def actions(self, app: str) -> list[Action]:
        """What `app` can be asked, through this mechanism, right now.

        May be slow — a UI tree walk, a `--help` — because it only runs
        when someone has actually named this app.
        """
        ...
