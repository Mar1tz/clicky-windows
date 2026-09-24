# GW-14 — every command has one meaning

**Status: done.** 44 new tests, 894 passing. Live check: 17 of 17
utterances picked the defined command.

## The problem

The walkthrough's words meant different things in different places:

* The runner's nudge told people to say **"'back' to hear it again"**. Back
  is the step *before*; "repeat" is this one again. The one place people
  learn the words taught the wrong one.
* The tool description called next "skip ahead" and back "repeat the
  previous step".
* **"Skip" and "pause" were not commands at all.** A model that called
  `guide_step("skip")` got an error.
* **"Where is it?"** only worked when the model misread it as "next" (the
  GW-13 check then pointed instead of moving).
* **"I'm stuck"** had no route. The runner's own stuck-help speaks locally,
  so wiring that up as-is would have put two voices on top of each other.
* **"Resume"** with a walkthrough already running returned an error, which
  invites the model to try something else.

## The table

One behaviour per command, whoever calls it. It lives above
`CompanionManager._GUIDE_COMMANDS`, and the tool's `enum` is tested to
equal it.

| command | people say | what happens |
|---|---|---|
| next | next, done, I did it, carry on | move on one step |
| skip | skip, skip this one | the same move — counted as a skip, not as the step done |
| back | go back, previous | the step before, said again; on step one, step one again (and the reply says so) |
| repeat | say that again, pardon | this step, said again |
| stop | stop, cancel, never mind | end it; the step it was on is kept for resume |
| pause | pause, take a break | the same as stop — ends "paused by the user" — and the reply says "resume" picks it up |
| resume | resume, continue where we left off | a stopped walkthrough restarts at its step; one already running is left alone |
| where | where is it, I can't find it | the arrow goes back on this step's target; nothing moves |
| help | I'm stuck, it's not working | the arrow goes back on the target, and the **model** explains it one different way, with the planner's alternative route if there is one; nothing moves |
| do_it | do it for me | until GW-20: the model says plainly Tora can't do steps yet, and the arrow goes on the target; nothing moves |

*where*, *help* and *do_it* never move the guide, and they are not
second-guessed against the transcript: a misread costs one pointing.

## What was built

* **`guide_step`** (`companion_manager.py`) takes all ten commands. `skip`
  becomes the next move; `pause` is passed to the runner as its own way
  of stopping. The action name is normalised, so "do it" works as well as
  "do_it".
* **One voice for help.** `_guide_stay` only points. The live model does
  the talking, from a note that carries the instruction they are stuck on
  and the planner's other route. `GuideRunner.mark_helped()` then stops the
  runner offering the same help again after a still minute. It lapses at
  the next step.
* **The GW-13 check does the thing asked for.** If the model calls "next"
  but the person said "I'm stuck" or "do it for me", it now helps or
  answers in place instead of refusing and asking for a second call.
* **The phrase reader** (`guide_intent.py`) knows *pause*, *help* and
  *do_it*. Help comes before repeat, so "it's not working again" is help.
  "Help me find…" is where. A negated command no longer ends the reading:
  "I don't know how, can you do it for me" goes on to be help. "Don't
  pause" and "don't do it for me" are nothing.
* **The nudge teaches the right words:** "Say 'next' when you've done it,
  'repeat' to hear it again, or 'I'm stuck' and I'll help."
* **A stopped walkthrough is named in the context** ("A walkthrough toward
  'export the video' was stopped at step 2 of 3"). "Continue" said an hour
  later about something else is then less likely to restart it.
* **The summary line says which commands were used**
  ("commands: help 1, skip 2"). A step that draws "where is it" is a
  badly planned step, which is what GW-21 will learn from.

## The live check, and the wording it changed

The check sends each utterance to a fresh Gemini Live session with the
real system prompt, the real tool declarations and the real per-turn
walkthrough context (built by `_screen_context` itself), and records the
first tool call. It sends text, not audio, plays no sound, and costs a few
cents a run. GW-23 is where a repeatable version of it belongs.

The first run found a regression that unit tests could not have seen. The
current-step line had been reworded to `(the arrow goes on "Export")`, and
the model read it as an instruction:

| current-step wording | "okay" → point_at | "where is it?" |
|---|---|---|
| old: "if the user asks where to click, call point_at with 'Export'" | 1 of 5 | point_at 3/3 |
| first draft: "(the arrow goes on 'Export')" | **4 of 5** | point_at 3/3 |
| **shipped: "Tora's arrow is already on 'Export'"** | **0 of 5** | where 1/3, point_at 2/3 (same arrow either way) |

With the shipped wording:

```
ok   'next'                              -> next
ok   'okay I did it'                     -> next
ok   'skip this one'                     -> skip
ok   'go back'                           -> back
ok   'can you say that again?'           -> repeat
ok   'stop the walkthrough'              -> stop
ok   'pause for a minute'                -> pause
ok   'where is it?'                      -> where
ok   "I can't find the export button"    -> where
ok   "I'm stuck"                         -> help
ok   "it's not working"                  -> help
ok   'can you just do it for me?'        -> do_it
ok   'what does this button do?'         -> (answered)
ok   'okay'                              -> (nothing)
ok   'hmm, is this the right one?'       -> (pointed at it)
ok   'resume the walkthrough'            -> resume   (walkthrough stopped)
ok   "okay let's continue where we left off" -> resume
17/17 as defined
```

## Proof

`tests/agents/test_e2e_guide_commands.py` (44):

* each row of the table, against a real `GuideRunner`;
* stop and pause through the real loop, each ending with its own reason
  while keeping its step for resume;
* help causes no local speech, and the runner's own help is suppressed
  after it (with the control case, where it is not suppressed);
* the tool's enum equals the hub's list;
* the per-turn context names every command;
* the nudge never says "back" for "again";
* the summary counts commands;
* 16 new utterances for the phrase reader.

## Not in scope

* The pre-live (Whisper) path still has its own `is_next` / `is_repeat`
  shortcuts, and its "repeat" replays the last answer. It is only used
  when the live session is off.
* "Do it for me" really doing it is GW-20.
