# GW-09 — "wait for it" is its own kind of step

**Status: done.** 7 new tests, 777 passing. The real planner checked live.

## The symptom

CapCut, 22 September. Step three was "wait for the captions to generate",
and the watcher treated it like a click — every time the progress bar moved
and settled, it asked the vision model whether *the user* had done it:

```
23:32:17  not done yet (caption processing is still in progress at 45%)
23:32:31  step done — vision: processing has finished
```

## The mechanism, and the fix that falls out of it

The watcher only asks the vision model about a screen that has *settled* —
changed from the baseline and then held still. A moving progress bar holds
still for a fraction of a second between updates, which was enough (0.6 s)
to count as settled, so it was asked about over and over.

A progress bar only truly goes still when the work is done. So a waiting
step is watched with a **3-second settle window and an 8-second minimum
gap**: the vision model is asked once, at the end.

Reproduced on a fake clock — twenty seconds of a progress bar moving every
second, then stillness:

```
ordinary step   asked the vision model 10 times, at 2.75, 4.75, … 20.75
WAITING step    asked the vision model  1 time,  at 22.0   (3 s after it stopped)
```

## What was built

* **The planner marks them.** A `kind` field (`action` | `wait`) and a rule:
  processing, exporting, downloading, installing, rendering, generating,
  uploading are their own step, with no target and a `done_when` saying
  what appears at the end. Live, it marks all three it was given —
  generating captions, exporting a video, installing an extension.
* **The watcher waits properly** (`waiting=True`): long settle, long gap.
  The next step's target appearing — already every step's free finish line
  — is usually what ends it without the vision model at all.
* **The runner does not mistake waiting for being stuck.** No "here, try
  it this way" help, and the nudge comes five times later — "say next when
  you've done it" is the wrong thing to say about a render.
* **Tora says she will speak up, and does.** *"Step 3 of 3: Wait for CapCut
  to finish. This takes a moment — I'll tell you when it's done."* — and
  the step after it begins *"That's finished."*

## Noticed, not done

The live plans also produced *"Ensure MP4 is selected as the format, then
click the blue Export button"* — a compound GW-06's splitter does not catch,
because "ensure" is not a pointing verb and ", then" is not "and then".
Left for a later pass rather than widening GW-06 inside GW-09.

## Proof

`tests/agents/test_e2e_guide_wait.py` (7): waiting steps are parsed and
lose their target; the prompt defines them; old plans default to action;
the spoken lines; the ordinary-vs-waiting comparison above; and the runner
neither helps nor nudges during a wait.
