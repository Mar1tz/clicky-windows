# GW-02 — one "next" said is one step moved

**Status: done.** 6 new tests (one replaced), 725 passing.

## The symptom

39 `guide_step` calls in 24 seconds. The 1.5-second repeat guard from
Field fix 6 caught 22 and let 17 through — the model in a loop calls about
every 0.6 s, and calls further apart than 1.5 s passed. A timer is a guess
about how fast the model retries.

## The trigger nobody had spotted

GW-01 fixed the missing brake. Reading the tool's own result found what
started the loop.

`_tool_guide_step` reported `"step": runner.index + 1` **straight after**
calling `request()` — before the runner's loop had consumed the command.
So a "next" on step 1 of 2 came back as **"step 1 of 2"**. The model was
told, in plain data, that it had not moved. It said next again.

The field was added in Field fix 6 specifically to stop repeat calls. It
was the thing inviting them.

## Two guards, because there are two different failures

**Within one presentation — the runner.** After next / back / repeat is
accepted, every further move is refused until the step it moved to has
actually been *spoken*. A fact, not a timer. Handles a burst: forty moves
while one step is being presented land exactly once.

**Within one request — the manager.** The runner's window is only as long
as the speech, so a model looping every 0.6 s would still land a move
every few seconds. What the user means by one "next" is one step. The
manager counts the things the user says (`_request_serial`, bumped in
`_begin_capture`) and allows **one guide move per request**, however many
times the model calls.

The first version of the live-runner test put `sleep(0.001)` between
requests; on Windows that is ~15 ms, so the "burst" lasted 600 ms and the
runner correctly let one move through per spoken step. That is precisely
the case the per-request guard exists for, and the tests now say which
guard owns which.

`stop` is never refused by either.

## What the model is told now

```
{"where": "now on step 2 of 2",
 "note": "Done - the move has happened and Tora is reading the step aloud
          herself. Do NOT call guide_step again, and do not read the step
          out; stay quiet or answer briefly."}
```

A refused repeat is `ok: True` with "that was already done" — reporting it
as a failure would only invite a retry.

## Proof

`test_e2e_guide.py`:

* `test_a_repeated_next_does_not_walk_off_the_end_of_the_guide` — 40
  refused after the first
* `test_a_burst_during_one_step_moves_the_live_runner_exactly_once` — a real
  running `GuideRunner`
* `test_one_thing_said_is_one_move_however_often_the_model_calls` — 12
  calls in one request, one move; a new request may move again
* `test_the_model_is_told_where_the_guide_is_going_not_where_it_was`
* `test_the_tool_result_names_the_destination_and_says_stop_calling`
* `test_stop_always_works_even_after_a_move`
