# GW-13 — a question is not a "next"

**Status: done.** 39 new tests, 850 passing.

## The problem

Mid-walkthrough, almost everything a person says is not a command: "what
does this button do?", "okay", "hang on", "is this the right one?". The
live model decides when to call `guide_step`, from audio, and when it reads
one of those as "next" the guide moves on without them — the quieter cousin
of the 23 September runaway.

The model's interpretation cannot be unit-tested. The tool it calls can
check that interpretation against the **transcript of what was actually
said**, and that check can.

## What was built

**`tutor_features/guide_intent.py`** — a small deterministic reader:
*next, back, repeat, stop, where*, *ack*, or nothing.

* Generous with real commands — "done", "I did it", "keep going", and even
  "necks" (a real mis-transcription of "next").
* Strict about everything else. A question is nothing.
* **"Okay" and "got it" are acknowledgements, not commands.** They usually
  mean "understood", and the guide already moves on by itself the moment
  the step is done; advancing on "okay" skipped steps people had only just
  heard.
* **Negation** takes a move or a stop off the table: "I am not done yet",
  "wait", "don't stop", "hang on" are nothing. It does not eat a repeat —
  "I didn't hear that, say it again" is still a repeat.
* "Where is the next button" is *where*, not *next*.

**`guide_step` checks the model against it** (`_check_guide_move`):

| person said | model called | result |
|---|---|---|
| a question | next | refused — "answer them; the walkthrough stays where it is" |
| okay / got it | next | refused — "it moves on by itself" |
| where is it | next | **points at the target again** instead, tells the model to say where |
| go back | next | refused — "call it with 'back' instead" |
| anything | stop | **always honoured** — a refused stop is the worse mistake |
| (no transcript yet) | anything | allowed — the transcript can lag the tool call |

**The per-turn guide context** now lists which words are commands and says
outright that everything else is a question to answer while the guide keeps
running.

## Proof

`tests/agents/test_e2e_guide_intent.py` (39): thirty utterances and what
each means; negation not eating a repeat; "nextcloud" not being "next";
and each row of the table above against a real `GuideRunner`.
