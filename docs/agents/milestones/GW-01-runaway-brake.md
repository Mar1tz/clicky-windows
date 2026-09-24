# GW-01 — the runaway brake works again

**Status: done.** 4 new tests, 719 passing. Regression reproduced against
the previous code before fixing it.

## The symptom

"Update Visual Studio Code", 23 September 16:39. After working correctly
for fifteen seconds, the live model called `guide_step` **39 times in 24
seconds**, about once every 0.6 s, until it walked off the end of the guide.

`MAX_TOOL_ROUNDS = 6` exists to stop exactly that. In the entire log it had
fired **zero times**.

## The cause — a regression from Field fix 5

Field fix 5 refilled the tool budget in two places: when a new question
started, and on every `turn_complete`. The second one broke the brake.

On the Live API **every tool round ends with its own `turn_complete`**. So
the counter went 1 → 0 → 1 → 0 and could never reach 6.

The turn_complete reset was there to paper over a real gap: the "new
question" check fired only on the *first transcript fragment of a turn*,
and a turn that only calls tools never finishes — so its transcript was
never cleared, and the next question was never recognised as new.

The test meant to guard the brake scripted nine tool calls **with no
turn_completes between them**. That is not what the wire does, so it
passed while the brake was dead.

## The fix

A single flag, `_awaiting_user`, set whenever a turn ends and cleared when
the user speaks. The budget refills when the user speaks *across a turn
boundary* — that is what "a new request" means — or when a spoken answer
finishes the request. Nothing else refills it.

`say_now` (DC-13's volunteered turns) counts as its own request.

## Proof

Reproduced first: the previous code, loaded from `HEAD`, driven with the
real wire shape — `tool_call`, `turn_complete`, ×40 — **ran the tool 40
times against a cap of 6**.

`test_realtime_session.py`:

* `test_a_tool_loop_with_turn_completes_between_calls_is_still_capped` —
  the 16:39 session; capped at exactly 6
* `test_a_question_after_a_tool_only_turn_gets_a_fresh_budget` — Field fix
  5's actual goal, kept: eight separate requests, none refused
* `test_one_long_request_and_its_follow_up_are_budgeted_separately`
* `test_the_brake_says_so_in_the_log` — a brake that fires in silence is
  how this went unnoticed for a day
