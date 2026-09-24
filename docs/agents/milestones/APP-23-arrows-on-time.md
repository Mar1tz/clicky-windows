# APP-23 — a walkthrough's arrow arrives with its step

**Status: done.** Reported on 24 Sep: "it did steps but the showing on the
screen was so slow … only showed much later". 14 new tests, 1145 passing.

## What the log showed

This is from "Add captions in CapCut", 22:39 on 24 Sep. Each step's words
appeared within about 3 s, but its arrow came much later:

| step | words shown | target found | late by |
|---|---|---|---|
| 1 Captions | 22:39:10 | 22:39:39 | 28.9 s |
| 2 Auto captions | 22:39:24 | 22:39:46 | 22.1 s |
| 3 Generate | 22:39:35 | 22:39:52 | 16.4 s |

By the time step one's arrow landed, the user was on step three. The same
thing happened in the 22:37 CapCut session. In VS Code and the browser, the
arrow's lookup succeeded in under a second.

## Why

CapCut's accessibility tree names one control, so every target went
through the whole chain:

1. **The UIA walk ran well past its 1.2 s cap.** The deadline was checked
   only every 32 nodes, and CapCut, busy rendering, answered each of the
   four calls per node slowly.
2. **OCR read the wrong monitor.** It read `mss.monitors[1]`. On this PC,
   with three screens, that is the right-hand one; the primary is in the
   middle. So OCR could not find text on the screen CapCut was on. It also
   never added the monitor's origin, so a hit on any screen but the one at
   (0, 0) would have put the arrow on the wrong screen.
3. **Four OCRs ran at once.** Step one was looked up twice, and the
   lookups overlapped. OCR is CPU-bound: one takes 2.2 s on this 8-core
   PC, four at once take 8.3–8.8 s each.
4. **The vision model waited.** It was asked only after UIA and OCR had
   both failed, even though it found each target in about 3.6 s.
5. **Late arrows were drawn anyway,** for steps that were already over.

## What changed

* **UIA** checks its deadline at every node. `ai/hybrid_pointer.py`
* **OCR** reads the monitor the app is on, as `ScreenShot` already does,
  and adds that monitor's origin to a hit. `ai/hybrid_pointer.py`
* **One OCR at a time.** A caller who asks while a read of the same
  monitor is under way takes that read. `ai/hybrid_pointer.py`
* **Vision races the local tiers.** It is asked as well once UIA and OCR
  have had 1 s, and at once in an app known to have no accessibility tree.
  That is judged by how many controls the planner or the screen summary
  found (CapCut: 1). The first answer wins. A quick local hit still never
  calls the model, and if the model sees nothing, OCR still gets its say.
  `CompanionManager._resolve_point`
* **Each step's target is looked up once,** however many callers ask while
  it runs. `_step_target`
* **A new step stops the last step's lookup,** and so does the end of the
  guide. `_drop_step_jobs`
* **An arrow that comes back after its step ended is not drawn.** The log
  says so.

Two older bugs were found along the way:

* **"@element" anchors never resolved.** `_resolve_anchor` passed
  `skip_vision=`, which `find_target` no longer accepts. The error was
  swallowed, so every anchor came back as nothing. This part of GW-19 is
  now fixed.
* **The pointer's docstring** promised "~5 ms" and "~300 ms". It now gives
  the measured times.

## What it should feel like

In CapCut, the arrow should come about 3–4 s after the step's words, the
time vision takes, or sooner when OCR reads the label first. Before, it came
16–29 s after, and pointed at the wrong step.

In apps with an accessibility tree it is unchanged: under a second, with no
model call.

**Cost:** the same number of vision calls or fewer in CapCut, since step
one's duplicate lookup is gone. There may be a few more where UIA misses
and OCR would have hit after the 1 s mark.

This is not yet GW-17, where an arrow lands with the step's first word by
resolving targets before the step is spoken. That is next in the
walkthrough plan.

## Proof

`tests/agents/test_e2e_guide_arrows_on_time.py` (14):

* OCR reads the monitor the app is on. With no window to go by, it reads
  the primary, not `monitors[1]`.
* An OCR hit is in screen pixels.
* OCR runs one at a time and shares a read, and a given image is read as
  given.
* The UIA walk keeps its deadline in a slow app.
* A slow local miss does not hold up the model.
* In an app with no controls, the model is asked at once.
* A quick local hit never asks the model.
* When the model sees nothing, OCR is still waited for.
* A step's arrow is not drawn after the step has ended.
* Step one is looked up once.
* A new step stops the last step's lookup.
* Anchors resolve again.

Each behaviour was removed in turn: six mutations, each caught by the test
written for it. The tests use no screen, network or paid call.
