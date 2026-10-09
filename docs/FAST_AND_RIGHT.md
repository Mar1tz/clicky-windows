# Fast and right on every PC: the milestones

**Written for:** whoever works through these (the owner, Dylan or a Claude
session). Each milestone stands on its own: why, with the evidence; what to
do, and where; how to prove it. Do them in order unless one says otherwise.

**What was reported (9 Oct 2026):**
* On some people's PCs HeyTora is very slow to respond.
* "I asked it to do something, it did it, then I asked the next thing and
  it was still stuck and didn't realise."

**The goal:** every answer about as fast as the model allows, on a slow PC
too. Never an answer to a screen that has since changed, and never one to
yesterday's conversation.

---

## What the log shows (the owner's PC, 7–9 Oct, HeyTora 1.7.1–1.7.3)

Read from `tora.log` and `journal.db`, not guessed.

1. **"Still stuck" is a reused look.**
   * 13:36:54, "What's my next best move?": "Pawn d2 to d4." Right.
   * 13:37:10, "Okay, I've done that. Where's my next best move?": "Pawn d2
     to d4" again. The log says *reused: the screen had not changed, no
     call*. It had changed: they had moved.
   * 13:37:38 ("Okay, look again…") and 13:37:48 ("I've done that. Please
     do the next move."): the stale "Knight f3 to d4" both times, reused.
   * **The cause is UD-05's shortcut.** Its 16×9 sketch of the screen
     cannot see a piece move. And "look again" counted as "show me that
     again".
2. **Yesterday's conversation answered today's "Hello."**
   * At 13:32:44, "Hello." got "The screen shows your Gmail inbox rather
     than the CIPC Company Registration page." That was a request from
     8 Oct, 07:50.
   * The live session is resumed, or re-seeded with a recap, however long
     it has been idle: it reconnected every ten minutes all night.
3. **A look through HeyTora's server is slow.**
   * Fast looks took a p50 of **4.7 s** (3.3–11.4 s). The same look
     straight to Google takes about 2.2 s (SP-01).
   * At 13:32 one request took 11.3 s to the server and back.
   * **The server (Paris, eu-west-3) does three things, one after another,
     on every request:**
     * checks the sign-in with a network call (`auth.getUser`);
     * writes the usage (`heytora_consume`);
     * forwards to Google and reads the whole answer before passing it on.
   * The network itself is fine: about 0.3 s to the server and 0.4 s to
     Google from this PC.
4. **The voice gets dearer as a conversation runs.**
   * Its input grew from 10,274 to 33,644 tokens an answer, and the voice
     averaged **$0.022 an answer**, against $0.0085 on the bench (SP-02).
   * Every generation re-bills the whole kept context, up to the 16k
     compression trigger, and a look question has two generations.
5. **The question waits for the screen description.**
   * `_realtime_stream` builds the screen line (an accessibility walk)
     before it sends any audio.
   * That took 184–464 ms on this fast PC; a slow PC with a busy browser
     can take seconds. The voice is buffered meanwhile, so Google hears the
     question late.
6. **A look captures every monitor, then the window again.**
   * `capture_all_screens` encodes all three monitors (331 ms here), uses
     one, then `capture_region` grabs the window a second time.
7. **Opening anything needs a click first.**
   * All 7 of 7 "open YouTube / Gmail / chess.com / VS Code" requests were
     answered "I need your permission…", and each waited for a click.
8. **An API key is in `tora.log`.**
   * The HTTP library logs every request URL at INFO, and the own-key
     route puts the key in the URL: 256 lines, from September.
   * Nobody should be asked to send their log until this is fixed.
9. **No numbers come from other PCs.** SP-14 was declined, so a slow
   user's own `tora.log` has to say why: every step, timed.

---

## How to work a milestone

1. **Read the milestone,** and the code it names.
2. **Measure before and after** where it changes speed or cost:
   * `tools/look_eval.py` for the look;
   * `tools/voice_eval.py` for the voice;
   * the answer timeline in `tora.log` for the real thing.
3. **Keep `python -m pytest tests/agents -q` green.** Add tests beside the
   existing ones.
4. **Show anything visible on the owner's screen first,** then commit.
5. **Mark it done here,** with its numbers.

**Paid runs:** say the estimate first. Never send a real screen in a test.

---

## Wave A — never stuck, never stale

### FS-01 · "I've done that" means a new screen — S — free — **done (9 Oct)**

**Done:**
* **The sketch** (`look_region.sketch` / `changed_cells`) is 64×36 grey
  cells. On a test board, the pawn's move d2→d4 changes 8 cells; the old
  16×9 hash saw 0 bits of difference and called it the same screen.
* **Their words come first.** "done / I've done / did it / I clicked /
  now / next / moved / look again / check again / another" always look
  again. "show me that again / where was it / remind me / repeat that"
  re-show the last look while at most 40 cells have changed.
* **The same question, with nothing said,** reuses only with 2 cells
  changed or fewer.
* **A move in a game** (`needs_judgement`, the window title too) is never
  reused.
* **Tora's drawings are rubbed out before the screenshot,** not after it,
  with 0.1 s for the overlay to repaint. The model no longer sees its own
  stale arrow on the board.
* **Tested** (`test_e2e_never_stuck.py`): the 9 Oct sentences each look
  again; the moved board looks again; a game move is never reused; "show me
  that again" still re-shows; the clear comes before the capture.

**Why:** finding 1. Three stale answers in a row is exactly "still stuck
and didn't realise".

**Do** (`companion_manager._reusable_look`, `ai/look_region.py`):
* **A sharper sketch.** Compare a 64×36 grey thumbnail cell by cell:
  enough cells changed means a new screen. One chess square is about 6
  cells there, so a moved piece changes about 12.
* **Their words.** Never reuse after "I've done that / I did / done / now /
  next / moved / again / look again / check again". Those say the screen
  changed, or ask for a fresh look.
* **Only these words still re-show the last look:** "show me that again",
  "where was it", "remind me", "repeat that".
* **A move in a game is never reused.** The other side moves too.

*Prove:*
* tests with the SP-01 chess screen before and after a move: a different
  sketch;
* the 9 Oct sentences: each one looks again;
* "show me that again" on the same screen is still reused.

### FS-02 · Yesterday's conversation is over — S — free — **done (9 Oct)**

**Done:**
* **After 30 min with nothing said, the next question is a new
  conversation** (`IDLE_FRESH_S`). The kept history (`_app_memory`) and
  the last look are cleared.
* **The voice session forgets** (`RealtimeConversation.forget`): no
  resumption handle, and an open session counts as not ready, so it is
  replaced rather than continued. With the history cleared, no recap is
  sent.
* **A dropped session is reopened in the background only while they are
  talking** (the last 30 min) or a walkthrough is running. No more all-night
  reconnects, each a voice pass on a plan.
* **A question that finds no session opens one on the spot,** on every
  plan, as metered plans already did. Before, Pro and Max sent it down the
  cascade, which took about 12 s on 8 Oct. The log says "turn: realtime
  (opening the session)".
* **Tested** (`test_e2e_fresh_conversation.py`):
  * a forgotten session starts blank, with no handle and no recap;
  * a dropped session is reopened at 5 min idle, not at 31 min, and
    always for a walkthrough;
  * at 31 min the question clears the history and opens the voice;
  * at 4 min it keeps the conversation.

**Why:** finding 2, and part of finding 4.

**Do** (`ai/gemini_realtime.py`, `companion_manager._recent_exchanges`):
* **After 30 min idle, start fresh.** The next question opens a new
  session: no resumption handle, and no recap of anything older than
  30 min.
* **Don't keep an idle session alive overnight.** It is opened again when
  they next ask, which costs one handshake (about 0.5 s).

*Prove:* tests:
* a 31-minute gap gives a blank session with no recap;
* a 5-minute gap resumes;
* the recap holds only recent exchanges.

### FS-03 · Say what it did; ask only when it matters — S — **needs your decision**

**Why:** finding 7.
* The spoken answer was the request for permission. The site then opened
  in silence.
* Every request waited for a click.

**Do:**
* **After an approved action,** Tora says it in a few words: "YouTube's
  open."
* **The decision:** opening a website, opening an app and media keys could
  run without a yes. Typing, sending, deleting, paying and anything an
  agent does would still ask.

*Prove:* tests on which actions ask. The spoken line after an approval
names what happened.

---

## Wave B — fast on every PC

### FS-04 · The question goes out at once — S — free — **done (9 Oct)**

**Done:**
* **The audio streams from the first block.** The screen line is built
  alongside and sent the moment it is ready.
* **When the question ends,** the stream waits at most 1 s more for the
  screen line (`CONTEXT_WAIT_S`), then answers without it. With the names
  missing, she can still look, or point by name.
* **The log says when it was slow:** "screen line took N s - sent while
  they spoke", or "not ready … answered without it". It was already
  cached for 4 s per window.
* **Tested** (`test_e2e_question_goes_out_at_once.py`): the first words go
  out within 0.1 s while the line is still being built, and the line
  arrives before the question closes. A line 5 s late is not waited for.

**Why:** finding 5.

**Do** (`companion_manager._realtime_stream`):
* **Stream the audio from the first frame.** The screen line is sent when
  it is ready, as long as that is before the question ends.
* **Reuse the screen line** for the same window for a few seconds.
* **Time it in the log:** "screen line N ms".

*Prove:* a test that audio is pushed while the screen line is still being
built; the timeline shows it.

### FS-05 · Capture once — S — free — **done (9 Oct)**

**Done:**
* **A look asks for the monitors' numbers only**
  (`_capture_shots(pixels=False)`, `screen.capture.monitor_frames`), then
  grabs one picture: the window, or that monitor (`grab_frame`) when
  there is no window to crop.
* **A fresh cached capture is still used** when there is one.
  Number-only frames are never cached, so nothing else gets a frame with
  no picture.
* **Measured on the owner's PC** (3 monitors): getting the monitors went
  from **201 ms to 2 ms**; the window grab itself is 54 ms either way.
  The timeline's look detail now starts with "capture N ms".
* **Tested:** with no window to crop, one grab, of the monitor in front;
  frames carry numbers and no pictures.

**Why:** finding 6.

**Do** (`_tool_look_at_screen`):
* **Capture only what is looked at:** the window region, or the monitor
  (SP-10), once.
* **The other monitors** are captured only when a step needs them.

*Prove:* a test counting the captures; "capture N ms" in the timeline.

### FS-06 · Measure the server hop — S — free (needs a function deploy) — **built; deploy pending**

**Built:**
* **The server** (`supabase/functions/heytora/index.ts`) times its steps
  for a forwarded answer: `auth` (the sign-in check), `consume` (the usage
  write), `google` (the forward, read whole) and `total`. It sends them in
  a `Server-Timing` header. The change is additive: nothing else in the
  function moved.
* **The app** (`gemini.server_timing`, `generate_json`) keeps them as
  `server_*_ms` in the look's usage; a fast→deep look adds both. The look
  line in `tora.log` gets "server N ms (sign-in N, usage N, Google N)".
* **Tested:** the header is parsed; a forwarded look carries the split.
* **Waiting:**
  * `supabase functions deploy heytora` (the owner's yes, or Dylan's);
  * a type check with Deno, which is not on the owner's PC;
  * then one real look to read the split (about $0.001).

**Why:** finding 3. Before the hop is changed, it must say where its time
goes.

**Do:**
* **The server reports each step.** `supabase/functions/heytora` returns a
  `Server-Timing` header: auth, consume, google and total.
* **The app logs it** on the look's line: "look 4.7 s = server 0.9 +
  Google 3.6 + upload 0.2".

*Prove:* a scripted response with the header shows on the timeline. One
real look shows the split (about $0.001).

### FS-07 · A faster server hop — M — needs a function deploy

**Do,** guided by FS-06's numbers:
* **Check the sign-in in the function** (a JWT signature check) instead of
  calling Auth over the network.
* **Record the usage while Google answers,** not before. The answer is
  still held back if the plan says no.
* **Stream the answer through** where the app can use it.

*Prove:* FS-06's split before and after. A look through the server within
0.5 s of a look straight to Google.

### FS-08 · A lighter voice context — S — about $0.30 of runs

**Why:** finding 4. The context grew to 33k tokens an answer.

**Do:**
* **A smaller window.** Compression triggers at 8k and keeps 4k, instead
  of 16k and 8k.
* **A longer bench conversation.** `tools/voice_eval.py` gets a
  10-question conversation, so the cost of a long chat and its references
  ("that one") are both measured.

*Prove:* the conversation's cost per answer drops, and every question in
it still passes.

### FS-09 · A slow PC says why in its log — S — free — **done (9 Oct)**

**Done:**
* **Once a session, one line about the PC** (`pcinfo.describe`, from
  Windows' own calls, no new package). The owner's reads "pc: 8 cores, CPU
  61% busy, RAM 16 GB (3.3 GB free), 3 monitor(s): …, Windows 10.0.26200".
  It is logged off the start-up path, and added to `heytora.spec` because
  it is imported by name.
* **Each look's line now carries:**
  * the capture time (FS-05);
  * the server's split, once FS-06 is deployed;
  * the total and the first word, as before.
  A slow screen line is logged on its own (FS-04).
* **Not added:** a separate upload time. FS-06's total against the look's
  time gives it.
* **Tested:** the line's parts; one failing part does not lose the rest;
  the manager logs it on a thread; the build includes it.

**Why:** finding 9.

**Do:**
* **The answer timeline gets every step:** screen line, capture, encode,
  upload, server, model, first word.
* **Once a session, one line about the PC:** cores, memory, monitors and
  their scaling, and the free CPU at start.

*Prove:* a test reads the timeline. A deliberately slowed step shows up as
itself.

---

## Wave C — safe, and shipped

### FS-10 · No keys in the log — S — free — **done (9 Oct)**

**What was really wrong:**
* `logsafe.py` already had the fix: quiet the HTTP loggers, redact on
  every handler, scrub old logs. **It never ran.**
* `_setup_logging` scrubbed `tora.log` after opening it. Windows refuses
  to replace an open file, and the error fell into a silent `except:
  pass` that skipped everything after it:
  * the redacting filter;
  * the quiet HTTP loggers;
  * even the "HeyTora starting" line.
* So the 256 old key lines stayed, request URLs went on being logged at
  INFO, and a half-finished `tora.log.tmp` was left behind.

**Done:**
* **The order.** The logs are scrubbed before the file is opened, and the
  filter is installed whatever the scrub does.
* **A file another handle holds** (a second HeyTora run from source, or
  this one's handler) is rewritten in place when it cannot be replaced.
  Any leftover `.tmp` is removed.
* **The web search's logger** (`primp`, `ddgs`) is quieted too. It logged
  about 15 URLs a search, with the user's words in them.
* **Tested:**
  * a scrub with the log held open;
  * the real start-up order: the old key is gone, a new one logged after
    start is redacted, HTTP loggers are at WARNING, and "HeyTora starting"
    is written.
* **The owner's own `tora.log`** is cleaned the first time 1.7.4 starts.

**Why:** finding 8.

**Do:**
* **Stop logging request URLs.** The HTTP libraries (httpx, primp) log at
  WARNING, and a log filter blanks any `key=…` that still slips through.
* **Clean the old lines.** On start, existing `tora.log` lines with a key
  are rewritten without it.

*Prove:* a test logs a URL with a key and finds it blanked. The owner's
log has no key after one start.

### FS-11 · Ship 1.7.4, and check it on a slow PC — S

**Do:**
* **The usual release:** a dry run, the self-update with relaunch, then
  publish.
* **Then one session on a slow PC** (a friend's, or a 2-core VM), with its
  `tora.log` read.

*Prove:* the slow PC's timeline: the first word and the drawing near this
PC's.

---

## The order, at a glance

| # | milestone | wave | size | cost | needs |
|---|---|---|---|---|---|
| FS-01 | "I've done that" means a new screen — done | A | S | free | – |
| FS-02 | Yesterday's conversation is over — done | A | S | free | – |
| FS-03 | Say what it did; ask only when it matters | A | S | free | **your decision** |
| FS-04 | The question goes out at once — done | B | S | free | – |
| FS-05 | Capture once — done | B | S | free | – |
| FS-06 | Measure the server hop | B | S | ~$0.001 | function deploy |
| FS-07 | A faster server hop | B | M | – | FS-06, deploy |
| FS-08 | A lighter voice context | B | S | ~$0.30 | – |
| FS-09 | A slow PC says why in its log — done | B | S | free | – |
| FS-10 | No keys in the log — done | C | S | free | – |
| FS-11 | Ship 1.7.4, check on a slow PC | C | S | – | all above |

**The user's two complaints:**
* **"Still stuck":** FS-01 fixes it, and FS-02 stops the stale answers.
* **"Slow on some PCs":** FS-04, FS-05 and FS-07 are the biggest wins, and
  FS-09 shows the rest.
