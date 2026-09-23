# UC-04 — every control in every window, on any PC

**Status: done.** 23 tests, 699 passing.

## The requirement

> "I need this for all PCs to automatically be able to control and do
> anything for them."

Eight hand-written adapters cover eight apps. On anyone else's computer —
different software, a different language — those are mostly absent, and
Tora can do nothing but open things. UC-02 made 223 apps openable; this is
what makes them *usable*.

## What was built

`agents/control/providers/windows_ui.py`. Every Win32, WinForms, WPF and
WinUI app publishes its controls through UI Automation, each one declaring
what can be done to it. An action is therefore **discovered, not written**:

    Details        (RadioButton, Select)  ->  explorer.select_details
    Word wrap      (CheckBox, Toggle)     ->  notepad.toggle_word_wrap
    Address bar    (Edit, Value)          ->  brave.set_address_and_search_bar

Measured on this machine, with no configuration at all:

| app | actions | time |
|---|---:|---:|
| Brave | 60 (capped) | 0.44 s |
| Notepad | 43 | 0.22 s |
| File Explorer | 4–127 (depends which window) | 0.36 s |

## Why this generalises to other people's machines

**Apps are identified by executable, not by window title.** "Inbox - Mail"
and "Drafts - Mail" are one app, because both are `olk.exe`. Titles are
localised and change every minute; executable names do not. This is the
single decision that makes the provider behave the same on a German or
Japanese PC. The tail of the title ("Mail", "File Explorer") is kept as an
*alias*, so a person can still say what they see.

Nothing about it is specific to this machine: there is no app list, no
path, no per-app knowledge anywhere in the file.

## Why it is safe enough to ship

An adapter author judges each action's risk by hand. **Nobody judged
these.** So:

* the floor is `CHANGES`, which always confirms — no synthesised action
  ever runs silently;
* a name that sounds final is promoted to `SENSITIVE`, which cannot be
  approved by voice or by a standing grant. Whole-word matching, so *Send*
  is caught and *Sender* is not;
* reading is `PASSIVE` and free;
* a disabled control is never offered;
* at most 60 actions per app — four hundred list rows is a data set, not a
  menu.

Getting this wrong would mean an agent that can press *Delete account*
because it happened to be a button with a name.

## Cost

Reading one window is 0.2–0.4 s, so it happens **only when the user names
that app**, and the answer is cached (UC-01). Nothing is enumerated per
turn; the per-turn payload is unchanged.

## Out of scope

Web page content. Chromium exposes its own chrome and almost nothing of
the page — measured at 26 nodes and 3 addressable controls for a full
Gmail window, stable across three passes. That is UC-10.

## A hazard found while running the suite

`test_open_app_really_opens_notepad` ended in an unconditional
`taskkill /IM notepad.exe /F`. On a machine where someone already had
Notepad open — most machines — it killed their window and any unsaved
work, and failed besides, because `open_app` correctly reported
`launched: False` for a window it had only focused. It now skips when
Notepad is already running and closes only what it started.
