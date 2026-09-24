# LG-01 to LG-04 — wave one: the arrow comes with the step and stays; "next" works; the step is heard

**Status: done.** Plan: `docs/agents/LIVE_GUIDE_PLAN.md`. 37 new tests,
1200 passing. Nine mutations, each caught by the test written for it.

These four are one commit because they share the same files: the runner,
the manager's step presentation, the overlay and the voice.

## LG-01 · The arrow never waits for a voice

**Was:**
* `GuideRunner._show` waited up to 12 s for Tora to be quiet before it
  showed a step, words and arrow included. A step the check had seen done
  also did not advance while the conversation was busy.
* On 24 Sep, 23:03, step one was seen done at :12 and step two came up at
  :36, behind a looping turn.

**Now:**
* **On screen at once.** When a step is due, `show_step`
  (`_display_step`) puts the words, the arrow, the progress pill and the
  step watcher up at once.
* **The voice waits on its own lane.** The line (`_speak_step_line`) is
  queued on a voice lane that waits for a quiet moment. A newer step
  replaces a line still waiting, and a line for a step already done is not
  said at all: it is logged "done before it could be said".
* **Done advances at once.** A step seen done moves on straight away,
  whatever the conversation is doing.
* **A latent bug, fixed.** The step watcher latches "done" on a click. If
  the user acted before the runner began polling, it saw "already true at
  step start" and never advanced. That was possible before (a click
  mid-sentence) and certain with the arrow first. The runner now trusts a
  check that arms itself (`self_arming`).
* **Lines never overlap.** The ending waits for the last line.

## LG-02 · The arrow gets there fast and stays

**Was:**
* A step's arrow flew at teacher pace: 1.6–2.8 s, ×1.7 in slow mode.
* It sat for 4 s, then flew back to the cursor.
* Every step's highlight box stayed on screen, piling up.

**Now:**
* **A fast, held arrow.** `CursorOverlay.guide_point` is a 0.35 s flight
  (0.7 s in slow mode), held on the target until `guide_release`.
* **New signals.** The manager emits `sig_guide_point` and
  `sig_guide_release`.
* **One step, one arrow.** Showing a new step takes the last one's arrow
  and box down. Showing the same step again (step one pointed early, or a
  repeat) keeps it.
* **Ending cleans up.** Stopping or finishing the guide takes the arrow
  down too.
* **Q&A is unchanged.** Pointing while Tora explains keeps teacher pace.

## LG-03 · "Next" always means next

**Was:**
* **"net" refused.** It was "next", badly transcribed, and was refused as
  "a question".
* **Other languages refused.** "nächste" was refused too: the check's word
  pattern was a-z only.
* **A 41-call loop.** The live model called `guide_step` 41 times in 20 s.
  The brake answered 35 times with "just answer", and the model ignored
  every one.

**Now:**
* **Any alphabet.** Words are read in any alphabet.
* **Other languages count.** "next" / back / stop / repeat / ack are
  recognised in German, Afrikaans/Dutch, Spanish, French, Portuguese,
  Italian and Swedish.
* **Near misses count.** "net", "neck" and "text" are next when they are
  the whole of a short utterance. "What text do I type?" is not.
* **A second ask wins, within limits.** The model's second ask for the same
  move on the same short, command-shaped words is taken: it heard the
  audio. A question, an "okay" or a "not yet" never is.
* **A hard cut.** After 10 rounds the realtime session is closed and the
  conversation resumes in a fresh one (`HARD_TOOL_ROUNDS`), instead of 35
  ignored errors: each round is a paid pass over the whole conversation.

## LG-04 · The step is always spoken

**Was:**
* **Silent for the rest of the day.** The Gemini TTS model allows 100
  requests a day on this key. From 22:41 every step was silent.
* **Called "out of credit".** The 429 said "check your plan and billing",
  so it was labelled billing.
* **The warning flickered.** It went up and down with every other model's
  success.

**Now:**
* **"quota" is its own kind.** `problem_of` gives "quota", checked first.
  It is not an account problem, so there is no warning to flicker.
  `retry_after` reads Google's "retry in 2h56m" and `retryDelay`.
* **The Windows voice takes over.** When a line is refused, it is spoken
  in the Windows voice (`audio/tts/windows_voice.py`, SAPI through
  comtypes).
  * It is rendered into memory as the same 24 kHz PCM, so it plays through
    the same stream, and Esc and barge-in still stop it.
  * About 0.1 s a line, free and local.
* **No wasted calls.** While the refusal lasts, lines go straight to
  Windows.
* **One notice.** "Google's daily limit for Tora's voice is used up on this
  key. She is using the Windows voice until about 02:10."
* **The test sandbox** scripts the Windows voice as silence. No test
  drives real SAPI.

## Proof

* `tests/agents/test_e2e_guide_live.py` (14): LG-01 and LG-02, scripted
  timings plus a real offscreen overlay.
* `tests/agents/test_e2e_guide_next_always.py` (32): LG-03, including the
  23:03 loop replayed through the real realtime pump.
* `tests/agents/test_e2e_guide_voice.py` (8): LG-04, with Google's exact
  24 Sep error.

Nine mutations, each caught by the test written for it: the busy gate
restored, self-arming removed, the step made to wait for silence, a
teacher-pace arrow, a non-holding arrow, "nächste" removed, no hard cut,
quota read as billing, and a silent prepared line.

## Not yet proven live

All of this is local code with scripted timings. How it feels is proven
by LG-09's scorecard and the LG-10 run. That run costs money and waits for
a yes.
