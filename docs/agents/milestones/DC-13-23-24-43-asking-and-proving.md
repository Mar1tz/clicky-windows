# DC-13, 23, 24, 43 — asking without going silent, and proving it happened

The two things wave 1 deliberately left broken.

## The complaint

> "It's not being able to do this anymore … now it just gets **stuck**. I
> see it pop up with the approve message. But I click approve, it doesn't
> go through with it."

Two separate faults sit behind that sentence, and wave 1 fixed neither.

## DC-13 · Asking used to make Tora go silent

`_tool_desktop_action` awaited the approval dialog. The live model is
**blocked until a tool returns** — it cannot say a word while one is
outstanding. So the whole time the box was on screen, Tora was mute. The
field log has it exactly:

```
00:16:52  tool call: desktop_action
00:17:06  tool desktop_action -> ok      ← fourteen seconds of nothing
00:17:14  turn: realtime                 ← the user asks again
```

Fourteen seconds of silence reads as "it's stuck", so people ask again,
which raises a second dialog behind the first.

**Now:** the tool returns immediately with `awaiting_approval` and an
instruction to say one short sentence — *"I need a yes for that, the box
is on screen"* — and then stop. The dialog is waited on in the background
by `_after_approval`, which runs the action when the answer comes and
**volunteers the outcome**: "you approved it and it has run: …", or "you
approved it but it didn't work: …", or "you didn't approve it".

Volunteering needed a way to make the model speak unprompted.
`send_context` deliberately does not end the turn, which is right for
screen context attached to a question in flight and useless here.
`RealtimeConversation.say_now` does end it — and waits for a gap first,
because arriving late is better than landing on top of a half-spoken
sentence.

An action already waved through with "always allow" still runs inline,
with no dialog and no round trip: waving something through must make it
faster, not slower.

## DC-23 · The volume is a number now, not N key taps

Key taps cannot say where the volume started, cannot honour "set it to
twenty percent", and cannot be checked afterwards — a tap "succeeds"
whether or not anything was listening.

`agents/control/audio.py` talks to Core Audio's `IAudioEndpointVolume`
through **comtypes**, which the pointer's UI Automation path already
requires (CLAUDE.md forbids new dependencies; pycaw would be the usual
route and is a thin wrapper over these same three interfaces). It reads
and writes the master scalar and the mute state on the default output
device. `set_level` returns the **read-back** value, not the requested
one, because some endpoints quantise and a check that trusts its own
input checks nothing.

`windows.volume` gained `to_percent`, so "set the volume to twenty
percent" is one call. Key taps remain the fallback for a machine with no
audio endpoint — a bare VM, RDP without audio — and on that machine the
action honestly reports itself as unverified.

## DC-24 · `ok` now means it happened

`Outcome.ok` used to mean "the coroutine did not raise". An `Action` may
now carry a `verify` coroutine, called with the same bound arguments
after `run`:

* returns a sentence → verified, and **that sentence becomes the message**,
  so the confirmation describes the world rather than the attempt (DC-43):
  "volume is at 34 percent", not "turned the volume down";
* returns `None` → cannot tell. Still `ok`, logged `(unverified)`, and
  `data["verified"]` is False. Honest uncertainty is not failure;
* raises `Unverified` → it ran and the world did not change, so the
  action is reported as **failed** and Tora says so.

A `verify` that itself breaks never fails a good action.

## Surface

| File | Change |
|---|---|
| `agents/control/audio.py` | new — Core Audio read/write via comtypes |
| `agents/control/model.py` | `Action.verify`, `Unverified` |
| `agents/control/registry.py` | calls `verify`; logs `(unverified)` |
| `agents/control/apps/windows_os.py` | volume by value, `to_percent`, verified |
| `agents/control/apps/media.py` | Spotify volume by value, verified |
| `ai/gemini_realtime.py` | `say_now` — speak unprompted, after a gap |
| `companion_manager.py` | `_after_approval`, `_speak_aside`, no blocking |

## Out of scope, deliberately

* **DC-14** voice approval — "yes" still cannot resolve a dialog; it needs
  a click. That is the next one worth doing.
* **DC-15/16/19** the dialog's wording, persistent grants, and Z-order
  above the overlay.
* **Media transport is still unverified.** Proving "next track" worked
  means comparing now-playing before and after, and `verify` only sees the
  arguments. Either it gains a before-state or the action returns one.
  Today it logs `(unverified)` and says so, which is at least honest.

## Proof

`test_control_arguments.py` (39) and `test_control_desktop.py` (15):

* `test_voice_fast_path_asks_without_going_silent` — the call returns
  `awaiting_approval`, does not claim success, and the action runs only
  after the answer
* `test_an_action_already_allowed_runs_straight_away` — a grant means no
  dialog and no deferral
* `test_an_action_that_did_not_take_is_reported_as_failed`
* `test_a_confirmed_action_reports_what_was_observed`
* `test_not_being_able_to_check_is_not_a_failure`
* `test_the_volume_is_read_back_from_the_device` — against the real
  device, restoring the level afterwards
* `test_the_volume_can_be_set_to_a_number`
* `test_say_now_waits_for_a_gap_then_speaks` / `…gives_up_rather_than_talking_over_her`

628 passing.
