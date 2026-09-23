# UC-02 — the launcher provider

**Status: done.** 23 tests, 675 passing. Verified against the real
machine: Calculator was opened by name, the window confirmed, and closed.

## The problem

Tora offered **9 apps**. The machine has **223**.

## What was built

`agents/control/providers/launcher.py`. `Get-StartApps` is the list
Windows itself shows when you press Start — a display name and an
AppUserModelID for each. That ID launches anything through the
`AppsFolder` shell path, the same door the Start menu uses: classic Win32,
MSIX packages, Steam games. No install path to find, no shortcut to parse,
no file written per app.

**Result: 225 apps addressable, up from 9.** `open notepad`,
`open calculator`, `open whatsapp` — none of which anyone wrote code for.

## The three things that needed care

**It costs 1.4 seconds.** Far too slow for the catalogue rebuild that runs
every minute, so the list is scanned once into
`%LOCALAPPDATA%\Tora\installed_apps.json` and refreshed when a day old.
Measured: **1.6 s cold, 0.1 s cached.** A corrupt cache rescans; no
PowerShell means no apps rather than a crash.

**Names collide.** "Antigravity" is listed twice — a packaged build and an
Electron one. Offering a person two identical choices is worse than
quietly taking the one Windows lists first.

**Matching had to get stricter.** Over eight adapters a loose substring
rule is harmless. Over 223 apps it lets one letter pick whatever sorts
first. Discovered apps now match by: exact name, then **whole word**, then
prefix, then substring — and nothing under three characters matches at
all. A whole word beats a prefix deliberately, so *"open word"* is
Microsoft Word and not WordPad. An unrecognised app returns nothing, which
is a better failure than the wrong app.

## Opening is verified

`Risk.CHANGES` — a window appearing is the user's screen changing, so it
asks. And it is checked (DC-24): the window titles are sampled before,
and the action succeeds only if one appears. An app that was already
running reports *"Spotify was already open"* rather than claiming to have
opened it, and an app that never appears **fails** instead of saying
"opened Notepad" to an empty desktop.

## One thing that got better by accident

A broken adapter no longer leaves an app with nothing: the launcher can
still open it. But `suggestions()` now names the broken adapter *and*
lists what generic actions remain, because otherwise the next person
debugs the symptom instead of the cause.

## Surface

| File | Change |
|---|---|
| `agents/control/providers/launcher.py` | new |
| `agents/control/providers/__init__.py` | registers it — one line |
| `agents/control/registry.py` | stricter discovered-app matching; broken adapters named even when generic actions remain |
| `tests/agents/conftest.py` | `TORA_APPS_CACHE` per test — a test must never write the user's app list |

## Out of scope

`open` is the only verb. Everything an app can do *once it is open* is
UC-04 (the UI Automation provider). Deep links (298 schemes) are their own
provider, and shell verbs are UC-07.
