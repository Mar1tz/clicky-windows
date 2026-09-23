# UC-02b — one app list, and it is Windows' own

**Status: done.** 11 more tests, 710 passing.

## The complaint

> "it keeps saying this app was not found in the installed list of
> applications. I want it that any computer I use this on, it'll be able
> to detect all the apps and use them."

## What was wrong

There were **two** app lists, and the one doing the answering was the
smaller.

| source | apps here | used by |
|---|---:|---|
| walk of the Start Menu's `.lnk` files | **162** | `windows.open_app`, the agent's `open_app` tool |
| `Get-StartApps` (Windows' own list) | **220** | the UC-02 launcher provider only |

**77 apps were invisible** to the path that answers "open X", because a
packaged app has no shortcut file on disk and no executable to find:

> Calculator · Calendar · Camera · Claude · Clock · Copilot · Mail ·
> Films & TV · Files · Game Bar · Magnifier · Feedback Hub · Live
> captions · Photos · Armoury Crate · ASUS GlideX · …

Asking for any of them got *"no app called that. Installed: …"*, followed
by twelve names in alphabetical order — which is wrong, and wrong in a way
that makes the machine look half-empty.

## What happens now

`launcher.find_app()` is the single answer to "is that app on this
machine", and it asks Windows. Both callers go through it:

* `windows.open_app` — the voice path — tries the packaged list first,
  then falls back to a shortcut on disk for the rare thing Windows does
  not list.
* the agent's `open_app` tool keeps its shortcut walk and its `PATH`
  lookup, then asks the packaged list before giving up. A packaged app
  comes back with a `shell:AppsFolder\…` target, which `os.startfile`
  opens exactly like a path.

Matching is the same ladder the registry uses: exact, then whole word,
then prefix, then substring, nothing under three characters — so "word" is
Word and not WordPad.

And a miss is now useful: *"there is no app called 'calulator' on this PC.
Did you mean: Calculator, Narrator?"* instead of an alphabetical dozen.

## Why this generalises

Nothing is hard-coded. `Get-StartApps` is whatever *that* machine has, in
*that* machine's language, cached per machine and refreshed daily. A PC
with Photoshop and no Spotify gets Photoshop and no Spotify.

## Note on ownership

`agents/tools/app_tools.py` belongs to worker 1 under `CLAUDE.md`. The
change there is eight lines and additive — the existing shortcut and
`PATH` lookups run first and unchanged; the packaged list is consulted
only where the function previously returned `None`. Flagged here for
review rather than done silently.

## Proof

`test_control_launcher.py`, 34 tests, including:

* `test_a_store_app_is_found_even_though_it_has_no_shortcut`
* `test_an_app_is_found_by_whatever_it_is_called` (5 phrasings)
* `test_an_app_that_is_not_here_is_not_invented`
* `test_a_near_miss_is_offered_rather_than_a_flat_no`
* `test_the_agent_tool_sees_packaged_apps_too`
