"""Spotify and anything else that plays - controlled without being looked at.

"Spotify, next song" is a media key. Windows routes it to whichever app
owns the media session, so it works while Spotify is minimised, on
another desktop, or behind a game, and the user's cursor never twitches.
That is the whole trick, and it costs nothing: no account, no OAuth, no
Premium, no network.

Playlists and searches go by link (`spotify:playlist:…`, `spotify:search:…`),
which Spotify's own handler opens. Anything a link cannot express - "play
the third song in my Discover Weekly" - is honestly reported as needing
the Spotify Web API (Phase 2, and Premium-only for playback control) or
the window itself.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

from .. import system
from ..discovery import open_uri, scan
from ..model import Action, Param, Refused, Risk, Tier, Unavailable

# open.spotify.com/playlist/37i9… or spotify:playlist:37i9… or a bare id
_ID = re.compile(r"(?:open\.spotify\.com/(?:intl-\w+/)?(track|album|playlist|artist|show|episode)/|"
                 r"spotify:(track|album|playlist|artist|show|episode):)([A-Za-z0-9]{22})", re.I)
_BARE = re.compile(r"^[A-Za-z0-9]{22}$")


def _uri_for(what: str, kind: str = "playlist") -> str:
    m = _ID.search(what or "")
    if m:
        return f"spotify:{(m.group(1) or m.group(2)).lower()}:{m.group(3)}"
    if _BARE.match((what or "").strip()):
        return f"spotify:{kind}:{what.strip()}"
    return ""


@dataclass
class Spotify:
    app: str = "spotify"
    display_name: str = "Spotify"
    aliases: tuple[str, ...] = ("music", "spotify desktop")

    def available(self) -> bool:
        m = scan()
        return m.has_scheme("spotify") or bool(m.app_named("Spotify"))

    def _running(self) -> bool:
        from agents.tools.app_tools import list_windows
        return any("spotify" in w["title"].lower() for w in list_windows()) or \
            system.now_playing().app.lower() == "spotify"

    def actions(self) -> list[Action]:
        def media(key: str, label: str, description: str) -> Action:
            async def run(times: int = 1) -> str:
                if not self._running():
                    raise Refused("Spotify is not running - say 'open Spotify' first")
                system.tap(key, int(times or 1))
                now = system.now_playing()
                return f"{label}" + (f"; now {now.describe().lower()}" if now.title else "")
            return Action(self.app, key.replace("media_", ""), description, run, Tier.SYSTEM,
                          Risk.REVERSIBLE, (Param("times", "How many times", type="integer"),),
                          needs_app_running=True, aliases=(key,))

        async def now_playing() -> str:
            return system.now_playing().describe()

        async def open_app() -> str:
            if scan().has_scheme("spotify"):
                open_uri("spotify:")
                return "opened Spotify"
            from agents.tools.app_tools import find_app
            found = find_app("Spotify")
            if not found:
                raise Unavailable("Spotify is not installed here")
            import os
            os.startfile(found[1])
            return "opened Spotify"

        async def play_link(what: str) -> str:
            """A link plays straight away; a phrase can only open a search."""
            uri = _uri_for(what)
            if uri:
                open_uri(uri)
                kind = uri.split(":")[1]
                return f"told Spotify to play that {kind}"
            raise Unavailable("that is not a Spotify link or id")

        async def play(query: str) -> str:
            try:
                return await play_link(query)
            except Unavailable:
                pass
            if not scan().has_scheme("spotify"):
                raise Unavailable("Spotify's link handler is not registered here")
            open_uri("spotify:search:" + urllib.parse.quote(query))
            return (f"opened Spotify's search for {query!r}. Spotify will not start playing from a link - "
                    "press play, or connect a Spotify account (Premium) so I can start it for you.")

        async def play_playlist(playlist: str) -> str:
            uri = _uri_for(playlist, kind="playlist")
            if uri:
                open_uri(uri)
                return f"opened that playlist in Spotify"
            open_uri("spotify:search:" + urllib.parse.quote(playlist))
            return (f"opened Spotify's search for {playlist!r} - pick the playlist there, or give me its "
                    "link and I will open it directly.")

        async def volume(direction: str, steps: int = 2) -> str:
            # `direction` has NO default on purpose. It used to default to
            # "up", so an argument lost anywhere upstream turned "reduce the
            # volume" into a volume increase - the exact opposite of the
            # request. A direction with a default is a bug waiting for a
            # dropped argument; see agents/control/args.py.
            system.tap("volume_up" if direction == "up" else "volume_down",
                       max(1, min(int(steps or 2), 20)))
            return f"turned the volume {direction}"

        return [
            media("media_next", "skipped to the next track", "Next track (works while Spotify is hidden)."),
            media("media_previous", "went back a track", "Previous track."),
            media("media_play_pause", "play/pause", "Play or pause whatever is playing."),
            media("media_stop", "stopped playback", "Stop playback."),
            Action(self.app, "now_playing", "What is playing right now.", now_playing, Tier.SYSTEM,
                   Risk.PASSIVE, examples=("what's playing?",)),
            Action(self.app, "open", "Open (or focus) Spotify.", open_app, Tier.DEEP_LINK, Risk.CHANGES),
            Action(self.app, "play", "Play a Spotify link, or open a search for a phrase.", play,
                   Tier.DEEP_LINK, Risk.CHANGES, (Param("query", "Link, id, or what to search for", True),),
                   examples=("play spotify:track:… ", "play lo-fi beats on spotify")),
            Action(self.app, "play_playlist", "Open a playlist by link, or search for it by name.",
                   play_playlist, Tier.DEEP_LINK, Risk.CHANGES,
                   (Param("playlist", "Playlist link, id or name", True),),
                   examples=("spotify, open my Deep Focus playlist",)),
            Action(self.app, "volume", "Turn the system volume up or down.", volume, Tier.SYSTEM,
                   Risk.REVERSIBLE, (Param("direction", "up or down", required=True,
                                            choices=("up", "down")),
                                     Param("steps", "How many notches", type="integer"))),
        ]


def adapters() -> list[Spotify]:
    s = Spotify()
    return [s] if s.available() else []
