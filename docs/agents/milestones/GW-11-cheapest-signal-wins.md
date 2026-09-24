# GW-11 — the cheapest signal that can decide a step, decides it

**Status: done.** 9 new tests, 806 passing. Race reproduced against the
previous watcher before fixing it.

## The ladder

A step can be finished by, cheapest first: a click inside its target
(free, instant), its own signal read through UI Automation (free,
milliseconds), or the vision model (a screenshot upload and two to four
seconds). GW-09 and GW-10 added rungs; this makes sure the rungs are
climbed in order.

## The race

The vision model was asked about a screen change once it had held still for
0.6 s. The step's own UIA signal was only re-read every 0.9 s. So when the
last UIA read happened just before the change, vision was asked first —
about a change a free read would have confirmed three tenths of a second
later.

Now a settled change runs the step's own signal **first**, and asks vision
only if that cannot tell.

```
old watcher   vision asked 1 time;  finished by 'vision: looks done'
new watcher   vision asked 0 times; finished by 'expectation: control_appears (Downloads)'
```

### A test that passed for the wrong reason

The first draft of the race test used a fake clock starting near zero. The
watcher's "last asked vision at" also starts at zero, so the minimum gap
between vision calls blocked the first one — and the old code "passed",
finishing on the UIA read. Real `time.monotonic()` is large, so in real
life that gap never blocks the first call. The fake clock now starts at a
realistic origin, the old code fails the test, the new one passes, and the
reason is written into the test.

## Knowing which rung finished it

The summary used to count every local finish as "expectation", which hid
the thing this milestone is about. `_finish_kind` names the signal —
`click`, `control_appears`, `typed_text`, `target_gone`, `vision` — and the
summary adds the share vision decided:

```
guide ended (finished): … 4 finished by themselves (click 2, typed_text 1,
    vision 1) (vision decided 20%), 1 manual moves, …
```

The plan's target — vision decides under 20 % of steps — is now a number
the log reports rather than a hope.

## Proof

`tests/agents/test_e2e_guide_cheapest.py` (9): the race (local wins, zero
vision calls); vision is still asked when nothing cheaper can tell; six
`done_by` shapes map to the right rung; the summary reports the vision share.
