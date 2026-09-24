# GW-08 — step one arrives faster

**Status: done.** 34 new tests, 949 passing. From the end of the question,
live (medians of 9 runs each): the arrow on step one **7.0 s → 4.2 s**, step
one heard **9.2 s → 5.3 s**, and "walk you through it" said once, not twice.

## Where the seconds went

Every piece was measured live before anything was changed:

| piece | before | streamed |
|---|---|---|
| plan → step one known | 3.6–4.3 s (the whole plan) | **1.8–1.9 s** (step one's JSON closes) |
| step one's speech → first sound | 3.3–4.1 s (the whole clip) | **0.85–0.91 s** |

Step one was heard about 8 s after `show_me_how`: the whole plan first,
then the whole of step one's speech. The 0.8 s start delay sat between them.

Measuring the whole path turned up four more causes that no single
measurement would have shown.

1. **The model acknowledged twice, every time (6/6).** `config.py` told it
   to *say "sure, let me walk you through it" first, then call*
   `show_me_how`. The tool's result then told it to *say one short
   sentence*. So it did both: "Sure, let me walk you through the steps to
   do that. … I'll guide you through each step of the calculation." That
   is two voices saying the same thing, and step one waiting behind both.
2. **Step one's speech was synthesised twice.** It was prefetched when the
   guide started, but the step came up before the 3.3–4.1 s synthesis had
   finished (it usually did), so it was synthesised again on the spot.
3. **Every line paid for opening the sound device.** A new MME output
   stream asks for its first audio 0.24 s after it is opened, and 0.97 s
   for the first one after launch. Measured on this machine's headphones,
   stream latency 0.2 s.
4. **A dead pooled connection sent the plan to the slow model.** The field
   log has `planner failed: LocalProtocolError … RECV_PING in state
   CLOSED` (21 September, 19:43). That is an HTTP/2 connection the server
   had closed, then reused. The walkthrough then fell back to the pro
   planner, which takes 10–20 s. The same error came up once in this
   milestone's live runs.

## What was built

**The plan streams** (`gemini_planner._stream_plan`, `gemini.stream(schema=)`).
Step one is handed over the moment its JSON object closes. Complete step
objects are read by counting braces outside strings. Step one gets the same
splitting and target snapping as the whole plan would give it. The returned
Guide is still the whole plan. A plan that says `feasible: false` starts
nothing. A stream that breaks after step one keeps every step that closed.
One that breaks before step one fails as before, so the caller falls back
as it always has.

**The walkthrough starts on step one and takes the rest as it arrives.**
`GuideRunner(writing=True)` treats running out of steps as a wait rather
than the end, until `plan_written()`. A stop or pause during that wait still
ends it. A "next" said during it is dropped, because otherwise it would skip
the step that is about to arrive. The hub appends only the new steps, so the
step on screen keeps its watcher, and it sends the real count to the screen.
Once step one has started, there is no fallback to a different plan: it
would not follow on from the step already being done.

**Silent things do not wait for the voice.** The arrow goes on step one as
soon as step one exists. Step one acts on the current screen, so its target
is there now; later steps are still resolved when they come up. Step one's
speech starts downloading at the same moment. Only the voice waits for the
acknowledgement to end.

**Speech streams, is downloaded once, and goes out through one open device**
(`audio/tts/gemini_tts.py`):

* `speak()` plays from the first piece of audio.
* A `SpeechBuffer` can be played before it has finished downloading, so a
  prepared line is never asked for twice.
* One output stream stays open between lines. It is warmed the moment a
  walkthrough is asked for, and closed after 20 s of quiet so that a device
  that went away is reopened, not held dead.
* A lock stops two lines from playing over each other.
* A line cut off by `stop_audio()` leaves nothing queued for the next one.
* Nothing is aborted at the end of a line, so the device's last 0.2 s is not
  clipped.

On the real device, with silent audio: first audio taken **0.55 s** after it
was fed on a cold stream, **0.02 s** on a warmed one.

**Call first, then speak once.** The rule and the tool description now say
*call `show_me_how` straight away, then say one short sentence.* In a live
A/B the model acknowledged twice in 0 of 6 runs, against 6 of 6 before, and
called the tool at a median 1.8 s after the question, against 2.7 s. Two
attempts to fix this by rewording the tool result instead both failed. One
still produced second sentences. The other ("reply with silence") drove the
model into bursts of 3 and 7 `guide_step` calls, and produced transcripts
like `<no speech>{pause}` that would have shown in the caption.

**A dead connection costs a retry, not the slow planner.** Every request
through `ai/gemini.py` is retried once on a fresh connection after a
protocol or network error. A stream is retried only if nothing has arrived
yet, because replaying one would say its start twice. Timeouts are not
retried, since that would double a wait that was already long.

**Honest counts while the plan is being written.** Step one is said as
"Step 1: …", not "Step 1 of 1". The pill shows "Guide · 1". The model is
told "step 1 (the rest of the plan is still being written)" rather than
"0 left". A "next" on step one gets the reply "moving on to step 2 — it is
still being planned", not "that was the last step".

**Measured from now on.** The log records, per walkthrough: step one ready,
pointed at, up and heard, each counted from when it was asked for. The
summary line ends with `step one: arrow 2.3s, up 3.1s, heard 3.4s`.

## The end-to-end measurement

The harness used a real Live session with the real system prompt and tools,
the real `show_me_how` result, and the real planner and TTS. Nothing was
played: the model's audio went through a model of the player's queue, so
"the acknowledgement has ended" means when it would stop sounding. Planning
prep is counted as a fixed 0.4 s; measured here it is 0.1–0.4 s, screenshot
and control walk in parallel. Times are from the end of the question:

| median of 9 | before | after |
|---|---|---|
| `show_me_how` called | 1.7 s | **0.7 s** |
| arrow on step one | 7.0 s | **4.2 s** |
| step one starts being said | 7.0 s | **4.5 s** |
| step one heard | 9.2 s | **5.3 s** |

Neither "heard" row includes opening the sound device. The old path paid
0.24–0.97 s for that on every line; the new path, warmed, pays 0.02 s. The
real gain in "heard" is therefore larger than the table shows.

**Against the plan's own target** of step one's arrow under 2.5 s after
`show_me_how`: the median is 2.6 s over 15 runs (2.3 s and 2.8 s in two
batches), down from 4.7 s. What is left is the planner's own time to step
one. That was 1.8–1.9 s measured in isolation, and slower and more variable
alongside a live session: 2.0–7.2 s across these runs.

## Proof

`tests/agents/test_e2e_guide_first_step.py` (34):

* **Planner:** only complete steps are read, with braces and escaped quotes
  inside strings handled. Step one is handed over before half the plan has
  arrived, snapped like the full plan. An infeasible plan starts nothing. A
  broken stream keeps the steps that closed; one that breaks before step one
  is an ordinary failure.
* **Runner:** running out of steps while the plan is being written is a
  wait. A plan that really was one step finishes. Stop works during the
  wait, and a "next" said during it does not skip the arriving step.
* **Hub:**
  * starts on step one;
  * takes the rest as it arrives;
  * the step on screen stays the same object;
  * the real count reaches the screen;
  * step one's speech and arrow are submitted at step one;
  * the arrow is timed;
  * a prepared line is played, not asked for again;
  * there is no fallback after step one;
  * "Step 1:" and "still being written";
  * "next" is not called the end.
* **Speech:**
  * starts with the first piece;
  * a stop ends the download and leaves nothing queued;
  * one download;
  * one stream kept open between lines, closed after quiet and reopened;
  * lines never interleave;
  * warming does not touch the stop flag;
  * a failed stream is silence, not a crash.
* **Requests:**
  * the schema is sent;
  * streamed audio comes back as PCM with its rate;
  * a dead connection is retried once, and twice in a row is an error;
  * timeouts are not retried;
  * a stream that cannot start is retried;
  * one that breaks after starting is not replayed.
* **Screen:** the island and the panel show "Step 1" alone while the plan is
  being written.

## Not in scope

* Starting the plan speculatively from the transcript, before the model has
  even called `show_me_how`, would save at most the 0.7 s the model now takes.
* Every other target is still resolved when its step comes up. Resolving
  them ahead of time is GW-17.
