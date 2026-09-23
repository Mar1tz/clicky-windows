"""UC-01: an app nobody wrote a file about can still be asked to do things.

Eight hand-written adapters cover eight apps. This machine has 223
launchable ones. A capability provider knows a *mechanism* rather than an
app, so it can answer for all of them — see provider.py and
docs/agents/UNIVERSAL_CONTROL.md.

The contract these tests hold:

  * a provider's actions are reachable by name, for an app with no adapter;
  * an adapter always wins a collision — it was written by someone who
    knew that app;
  * `actions()` is only ever called for an app someone NAMED, and its
    answer is cached, because walking a UI tree for 223 apps would be
    slower than the conversation and dearer than the answer;
  * a provider that breaks takes nothing else down with it.
"""
from __future__ import annotations

import asyncio

import pytest

from agents.control.model import Action, Param, Risk, Tier
from agents.control.provider import AppProfile, CapabilityProvider
from agents.control.registry import AppControl


class CountingProvider:
    """Knows two apps. Records every time it is asked to do real work."""
    name = "fake"

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.ran: list[str] = []

    def apps(self) -> list[AppProfile]:
        return [
            AppProfile("notepad", "Notepad", ("text editor",), source=self.name,
                       launch="notepad.exe"),
            AppProfile("spotify", "Spotify", source=self.name),
        ]

    def actions(self, app: str) -> list[Action]:
        self.asked.append(app)
        if app != "notepad":
            return []

        async def launch() -> str:
            self.ran.append("open")
            return "opened Notepad"

        async def write(text: str) -> str:
            self.ran.append(f"write:{text}")
            return f"typed {text!r}"

        return [
            Action("notepad", "open", "Open Notepad.", launch, Tier.SYSTEM, Risk.CHANGES),
            Action("notepad", "write", "Type something.", write, Tier.UI_BACKGROUND,
                   Risk.CHANGES, (Param("text", "What to type", required=True),)),
        ]


class RealAdapter:
    """A hand-written adapter for spotify, to prove it outranks the provider."""
    app = "spotify"
    display_name = "Spotify"
    aliases = ("music",)

    def available(self) -> bool:
        return True

    def actions(self):
        async def next_track() -> str:
            return "skipped (the hand-written way)"

        return [Action("spotify", "next", "Next track.", next_track,
                       Tier.SYSTEM, Risk.REVERSIBLE)]


@pytest.fixture
def control():
    provider = CountingProvider()
    return provider, AppControl(adapters=[RealAdapter()], providers=[provider])


def run(coro):
    return asyncio.run(coro)


# ── the point of the whole thing ─────────────────────────────────────────────

def test_an_app_with_no_adapter_can_still_be_asked(control):
    provider, c = control
    out = run(c.run("notepad", "open"))
    assert out.ok and provider.ran == ["open"]
    assert out.message == "opened Notepad"


def test_a_provided_action_gets_the_same_argument_contract(control):
    """Everything wave 1 fixed applies to synthesised actions too."""
    provider, c = control
    out = run(c.run("notepad", "write", {"txt": "hello"}))
    assert not out.ok and "text" in out.message, "an unknown name is still refused"
    assert provider.ran == []

    out = run(c.run("notepad", "write", {"text": "hello"}))
    assert out.ok and provider.ran == ["write:hello"]


def test_a_discovered_app_is_addressable_by_its_human_name(control):
    _provider, c = control
    assert c.resolve_app("Notepad") == "notepad"
    assert c.resolve_app("text editor") == "notepad"
    assert c.resolve_app("notepad") == "notepad"


def test_a_discovered_app_lists_what_it_can_do(control):
    _provider, c = control
    names = {a.name for a in c.actions(app="notepad")}
    assert names == {"open", "write"}


# ── an adapter is worth more than anything synthesised ───────────────────────

def test_an_adapter_beats_a_provider_for_the_same_app(control):
    _provider, c = control
    out = run(c.run("spotify", "next"))
    assert "hand-written" in out.message


def test_the_adapter_app_list_leads_and_discovered_apps_follow(control):
    _provider, c = control
    listed = c.apps()
    assert listed[0]["app"] == "spotify" and listed[0]["actions"] == ["next"]
    discovered = [a for a in listed if a["app"] == "notepad"][0]
    assert discovered["actions"] == [], "asking what it can do costs time; do it on demand"
    assert discovered["via"] == "fake"


# ── laziness, because 223 apps is not a per-turn budget ──────────────────────

def test_nothing_is_asked_about_until_it_is_named(control):
    provider, c = control
    c.apps()
    c.resolve_app("notepad")
    assert provider.asked == [], "listing apps must not walk anyone's UI tree"

    c.actions(app="notepad")
    assert provider.asked == ["notepad"]


def test_an_answer_is_remembered(control):
    provider, c = control
    for _ in range(4):
        c.actions(app="notepad")
    assert provider.asked == ["notepad"], "half a second, once, not four times"


# ── one bad provider must not sink the rest ──────────────────────────────────

class BrokenProvider:
    name = "broken"

    def apps(self):
        raise RuntimeError("no registry access")

    def actions(self, app):
        raise RuntimeError("still broken")


def test_a_provider_that_breaks_is_reported_not_fatal():
    provider = CountingProvider()
    c = AppControl(adapters=[RealAdapter()], providers=[BrokenProvider(), provider])
    assert c.resolve_app("Notepad") == "notepad", "the working provider still answers"
    assert any("broken" in k for k in c.broken())


def test_a_provider_that_breaks_mid_question_loses_only_its_own_answer():
    provider = CountingProvider()
    c = AppControl(adapters=[], providers=[provider, BrokenProvider()])
    assert {a.name for a in c.actions(app="notepad")} == {"open", "write"}


def test_the_protocol_is_what_it_claims():
    assert isinstance(CountingProvider(), CapabilityProvider)


def test_a_profile_knows_every_name_it_answers_to():
    profile = AppProfile("notepad", "Notepad", ("text editor", "notepad"))
    assert profile.names == ("notepad", "Notepad", "text editor")
    assert AppProfile("vlc").display_name == "vlc"
