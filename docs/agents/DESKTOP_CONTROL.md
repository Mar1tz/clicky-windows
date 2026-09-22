# Desktop control — asking apps instead of clicking at them

*Status: Phase 1 built (M27). The goal: any app on this PC, driven by
voice or by an agent, in the background, with the user confirming
anything that acts.*

## The idea

A computer-using agent that only knows how to click is slow, brittle and
rude: it takes the user's mouse, it needs the window in front, and it
guesses pixel positions from screenshots. Almost every request people
actually make — *"next song"*, *"open this project in VS Code"*, *"put
this in the chat"*, *"open Bluetooth settings"*, *"turn it down"* — has a
quieter, exact route that the app itself provides.

So every action declares which rung of this ladder it uses, and the agent
always starts at the top:

| tier | how | example | touches the screen? |
|------|-----|---------|---------------------|
| 1 **App Action** | Windows 11 App Actions / MCP — the app declares it | *(Phase 2)* | no |
| 2 **App API / CLI** | the app's own command line | `code chat -m agent "explain this"` | no |
| 3 **Deep link** | a registered URI handler | `spotify:playlist:37i9…`, `vscode://file/C:/repo`, `ms-settings:bluetooth` | no |
| 4 **System** | the OS does it | media keys, volume, window management, clipboard | no |
| 5 **Background UI** | UI Automation invokes the control where it stands | press *Save* in a window that is behind others | no |
| 6 **On-screen input** | the real mouse and keyboard | click a canvas app | **yes** |
| 7 **Vision** | find it in a screenshot, click the pixel | last resort | **yes** |

Microsoft's own research points the same way: **UFO2** (arXiv 2504.14603)
gives each app an agent with native APIs *beside* the GUI and measures
higher success with up to 58% fewer steps, and runs the GUI half inside
an isolated picture-in-picture desktop so the user is never interrupted.
Windows 11's App Actions and MCP support are the same bet from the OS
side. This layer is that architecture, built on what today's Windows
already exposes.

## What it looks like in practice

```
"Spotify, next song"        → media key routed to the media session      (tier 4, ~30 ms)
"what's playing?"           → the media app's window title               (tier 4, no ask)
"open my clicky windows
 project in VS Code and ask
 the chat to explain it"    → code -r <path> ; code chat -m agent "…"    (tier 2)
"same, in Cursor"           → cursor://anysphere.cursor-deeplink/prompt? (tier 3)
"open Bluetooth settings"   → ms-settings:bluetooth                      (tier 3)
"open github.com in Brave"  → brave.exe --new-window https://github.com  (tier 2)
"press Save in Notepad"     → UIA InvokePattern on the button            (tier 5)
```

None of those focus a window or move the pointer. The app may be
minimised. The user keeps typing.

## The parts

```
agents/control/
    model.py        Tier, Risk, Action, Param, Outcome, Unavailable/Refused
    discovery.py    what this machine has: Start Menu apps, every registered URI
                    scheme (254 on this PC), which CLIs exist
    system.py       media keys, volume, now-playing, window commands, clipboard
    uia.py          background UI Automation: list controls, invoke/toggle/select/
                    set_value/read, never Tora's own windows
    registry.py     AppControl: resolve "vs code"→vscode, find actions, run them,
                    report which tier did the work
    apps/
        windows_os.py  media, volume, windows, clipboard, folders, ms-settings, open app/url
        media.py       Spotify: next/previous/play_pause/stop, now_playing, play,
                       play_playlist (links play; a phrase opens search, honestly)
        editors.py     VS Code / Cursor / Windsurf / Antigravity: open_project (by NAME,
                       resolved from the editor's own recent list), open_file, chat,
                       recent_projects, install_extension, diff
        browsers.py    Chrome / Edge / Brave / Opera / Firefox / Vivaldi: open_url, search
agents/tools/control_tools.py
    list_app_actions   what this machine can be asked (SAFE, never interrupts)
    app_action         do it the app's way
    ui_control         list/press/toggle/read a window's controls in the background
    open_link          any registered URI
```

## Confirmation — per action, not per tool

The user asked for "always confirm anything like a click or an action",
without the nagging that usually follows. So asking is decided per call:

* **PASSIVE** (what's playing, list controls, recent projects) — never asks.
* **REVERSIBLE** (next track, volume, focus a window) — asks once; the
  dialog offers *Allow spotify.next for this task* (agents) or *Always
  allow spotify.next* (voice), and that yes covers **only that action**.
* **CHANGES** (open a project, start a playlist, copy to clipboard) — asks.
* **SENSITIVE** (sends something to another person) — asks every time.

Mechanically: a tool may implement `asks_for_call(call)`, `grant_key(call)`
and `ask_reason_for(call)`; the registry uses them, `ApprovalRequest`
carries the grant key, and the manager's per-task grants and the policy's
`always_allow` both match on it. The dialog's reason line says which tier
will run and whether the screen will move ("in the background" vs "ON
YOUR SCREEN"). Esc still trips the kill switch for everything here.

## Two ways in

* **Voice, instantly**: `desktop_action(app, action, arguments)` runs one
  action with no task, no plan and no agent — the confirmation dialog
  appears, and the whole round trip is well under a second. Tora is told
  to use it for app requests and to call `action="list"` when unsure.
* **As a job**: the computer worker now leads with `list_app_actions` →
  `app_action` → `ui_control`, and only then the on-screen tools. "spotify
  next song", "open bluetooth settings" and "minimise the Chrome window"
  are recognised as tasks by the classifier and routed to it.

## Adding an app (a few lines)

```python
@dataclass
class Obsidian:
    app = "obsidian"; display_name = "Obsidian"; aliases = ("notes",)
    def available(self): return scan().has_scheme("obsidian")
    def actions(self):
        async def open_note(note: str):
            open_uri(f"obsidian://open?file={quote(note)}")
            return f"opened {note} in Obsidian"
        return [Action(self.app, "open_note", "Open a note.", open_note,
                       Tier.DEEP_LINK, Risk.CHANGES, (Param("note", "Note name", True),))]
```

Drop it in `agents/control/apps/`, add it to `_discover()`, done: it
appears in `list_app_actions`, gets confirmation and grants for free, and
the voice path can call it immediately.

## What is not built yet (Phase 2, in order)

1. **Picture-in-picture desktop** (UFO2's approach: an RDP loopback
   session) so even tier 6/7 actions run on a desktop of their own and
   never touch the user's screen. This is the single biggest upgrade.
2. **Windows App Actions + MCP** (tier 1): enumerate what apps declare
   and invoke it — the route Windows itself is moving to.
3. **Spotify Web API** as a connected account, so "play my Deep Focus
   playlist" really starts playing (Premium, OAuth) instead of opening a
   search.
4. **Richer media session** through WinRT (`GlobalSystemMediaTransportControlsSessionManager`)
   for exact track, position and per-app control.
5. **Learn by watching**: record a workflow once (pynput is already a
   dependency), keep it as a named action, replay it through the ladder.
6. **More adapters**: Explorer, Office, Outlook/Teams/Slack (their deep
   links are already discovered), OBS, Steam, Docker Desktop.
7. **Per-app knowledge substrate** (UFO2's other half): index an app's
   help pages and past successful runs so the agent knows the app's own
   words.
