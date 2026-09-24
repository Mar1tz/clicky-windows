# GW-03 — a walkthrough's whole life is in the log

**Status: done.** 4 new tests, 729 passing.

## The symptom

Fourteen walkthroughs in `tora.log`. Every one recorded its start. **Not
one recorded its end** — nor a single nudge, stuck moment, or how any step
finished. The audit that found GW-01 had to guess at durations, and got
"six to ten minutes for three steps", which was meaningless: there was
nothing to measure *to*.

## What a walkthrough now writes

```
guide ready: 2 steps for 'update Visual Studio Code' (planned in 4.0s)
guide step 1/2 shown: Open the Help menu.
guide step 1 target 'Help' found via uia in 0.04s
guide step 1/2 finished by itself after 6.1s (click on target)
guide step 2/2 shown: Choose Check for Updates.
guide step 2 target 'Check for Updates' found via vision in 2.80s
guide step 2/2 ended: next after 19.4s
guide ended (finished): 'update Visual Studio Code' - 2 steps,
    1 finished by themselves (click on target 1), 1 manual moves,
    0 vision checks, targets: uia 1, vision 1, stuck 0, nudged 0,
    planned in 4.0s, 31s total
```

The last line is the one that matters: one summary per walkthrough, with
everything the later milestones are judged by — steps that finished by
themselves versus manual moves (GW-11), vision checks (GW-11), which tier
found each target (GW-18), stuck and nudged (GW-12), time to plan (GW-08).

## How

* `GuideRunner` takes an `on_event` hook and reports `step_shown`,
  `step_ended` (with the outcome and seconds since shown), `stuck`,
  `nudged`, and `ended` with a reason: *finished*, *stopped by the user*,
  *the user got there ahead of the guide*, *cancelled*, *crashed*. The
  runner is the only thing that knows whether a step finished by itself or
  the user said "next".
* `StepWatcher` counts its vision checks (`judged`).
* `_resolve_point` now says which tier found a target (`via`: snapshot,
  uia, ocr, vision) — 52 targets went to the vision model across fourteen
  walkthroughs and there was no way to tell which.
* `CompanionManager._guide_event` writes the lines and keeps the per-guide
  tallies.

A reporting hook that raises is swallowed: reporting is never allowed to
break the thing it reports on.

## Proof

`tests/agents/test_e2e_guide_log.py` — a real `GuideRunner` driving the
manager's own handler:

* `test_a_walkthrough_writes_its_whole_life_down` — a click, a manual next
  and a vision finish; the exact lines and the summary counts
* `test_stopping_part_way_is_recorded_with_its_reason`
* `test_the_tier_that_found_each_target_is_recorded`
* `test_a_reporting_hook_that_breaks_never_stops_the_walkthrough`
