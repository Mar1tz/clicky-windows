# GW-04 — Tora never hears herself say "next"

**Status: done — proven, no code change needed.** 10 new tests, 739 passing.

## The worry

Walkthrough steps say things like *"click Next"*, and the nudge after a
long wait says, word for word, *"Say 'next' when you've done it."* If either
reached the live model as the user's voice, the guide would advance itself:
the 23 September runaway, started from the other end.

## The finding

It cannot, by construction. Four independent parts, each now pinned by a
test so a later change cannot quietly reopen the path:

1. **The microphone feeds the model only during a capture** — the chunk
   sink is attached in `_begin_capture` and detached by `stop_recording`.
   Between questions, including while a step is being read out, nothing is
   streamed.
2. **`_begin_capture` silences every playback before it opens that path.**
   `stop_audio()` runs first, then the sink is attached, then the mic
   opens — in that order. A step still playing when the mic opened would be
   heard as the user; the test asserts the order, not just that all three
   happen.
3. **A step cannot start speaking while the user is being recorded.**
   `_guide_busy()` is true in LISTENING and THINKING, and the runner holds a
   step until it is false — tested against a real running `GuideRunner`,
   not just the predicate.
4. **While Tora is busy, the wake word is not listened for** (M28), so her
   own voice cannot open a capture in the first place. Tested for every
   non-idle state.

## Proof

`tests/agents/test_e2e_guide_echo.py`:

* `test_playback_is_silenced_before_the_microphone_opens`
* `test_the_microphone_stops_feeding_the_model_when_the_capture_ends`
* `test_a_step_waits_while_the_user_is_talking` (three states)
* `test_the_runner_really_waits_before_speaking_a_step`
* `test_the_wake_word_is_not_listened_for_while_tora_is_busy` (four states)

## Wave 1 closes here

GW-01 through GW-04: the runaway is capped, its trigger is gone, one "next"
is one step, every walkthrough writes its life down, and the echo path is
closed and pinned. Wave 2 (plans that point at real controls) can now be
measured against the GW-03 summary line.
