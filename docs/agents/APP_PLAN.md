# Making the whole of Tora work

**Written for:** the user deciding what gets fixed first, and the session
fixing it. One milestone at a time; the next starts when the last is
proven. Each lands as one commit with a doc in `milestones/APP-NN-*.md`.

**Evidence:** `%LOCALAPPDATA%\Tora\tora.log` (21–24 September, 67 starts,
149 warnings, 17 errors), `agents.db` (15 agent tasks), a pyflakes scan of
the whole codebase, and a read of every page of the app window and the
tray and `main.py` wiring behind it. Every number below is from those.

---

## What the review found

### Hurting today

| # | What happens to you | Evidence | Cause |
|---|---|---|---|
| 1 | You approve a desktop action and nothing happens | 4 times on 24 Sep, 12 in total: `volume` with `level`, `open_app` with `app_name`, `window` with `action`, `volume` without a direction | The model names arguments its own way, and they are checked only **after** you have approved |
| 2 | "I couldn't work out the steps from this screen" when the screen was fine | 24 Sep 17:07: fast planner 503, pro planner 503, both "high demand" — you restarted Tora a minute later | No retry on a busy server, and the message blames the screen |
| 3 | Tora goes quiet for hours and never says why | 22 Sep: **77** errors "your prepayment credits are depleted" — Live, TTS, everything | A billing error is treated like any other failure |
| 4 | Settings forget themselves | Quiz mode, slow mode, privacy guard, OCR, web search, journal, code mode, "answer in my language", auto-advance | Only the wake word, voice and route are saved; the rest start from defaults every launch |
| 5 | The Tora app window does nothing | All nine pages: "every number is a placeholder and every control is inert on purpose" (the M29 brief) | Built design-first, as asked; never wired |

### Real, less visible

| # | What | Evidence |
|---|---|---|
| 6 | A later walkthrough step can point at where a same-named control **used to be** | The pointer's first tier is the control snapshot from when you last spoke; it has no age or window check |
| 7 | "Tora" sometimes is not heard | 26 wake-word transcriptions timed out after 12 s, the latest 24 Sep 10:43 |
| 8 | Agent tasks fail their own checks after doing the work | 6 of 15 on 22 Sep: verification steps like `Get-Item …\index.html` failing, or 12 iterations used up; the florist site "failed" then succeeded on a second ask |
| 9 | Replies run together in the panel | Every reply is appended to the last with no gap; nothing ever clears it |

### Checked and fine

* Start-up: all three launches on 24 Sep came up clean; the live session
  opened in 1–3 s.
* Pyflakes: no name that is undefined at run time (the eight it reports
  are type hints in quotes).
* OCR: `rapidocr-onnxruntime` is installed now; the "OCR disabled" warning
  stopped on 22 Sep.
* 949 tests pass.

---

## Milestones

### Part 1 — stop it failing you (1–4, 6)

**APP-01 · A desktop action you approve, happens**
The model's argument names are accepted when they can only mean one thing
(`level`→`to_percent`, `app_name`→`name`, `action`→`command`, "set it to
30%" needs no direction), and anything that still does not fit is sent back
to the model **before** you are asked — so you are never asked to approve
something that cannot run.
*Proof:* the five refusals from the log, replayed, all run; a genuinely
wrong call never reaches the approval dialog.

**APP-02 · A busy or broke Google is said out loud**
A 503/"high demand" is retried (twice, with a short pause) before anything
falls back; an out-of-credit or bad-key error is recognised and said once,
plainly, with what to do — spoken and in a notification — instead of
silence. The walkthrough stops blaming the screen.
*Proof:* scripted 503s recover; a scripted "prepayment credits" error
produces one clear message, not 77 quiet failures.

**APP-03 · Settings stick**
Every tutor and privacy switch is saved when changed and applied at start,
the same way the voice and the wake word already are.
*Proof:* change, restart (in a test), still changed.

**APP-04 · Pointing never uses an old screen**
The in-memory control snapshot is used only when it is from the same
window and recent; otherwise the live search runs. (Part of GW-17.)
*Proof:* a stale snapshot with a same-named control is ignored.

### Part 2 — the Tora app, live (5)

Built on one bridge (`ui/app_bridge.py`): it reads the real state (manager,
config, journal, agents, extensions) and every control calls the **same**
handler the tray menu calls, so the two can never disagree.

**APP-10 · The bridge and the shell** — the window opens with live status
(listening / thinking / speaking, connected or not) and refreshes a page
each time it is shown.
**APP-11 · Settings** — workspace, what runs without asking, plan first,
microphone, always listening, hotkey, panel.
**APP-12 · Tutor** — every switch live and saved (with APP-03).
**APP-13 · Voice & models** — the voice, the conversation route, the real
model list and refresh.
**APP-14 · Setup** — real key status, set/test a key, live diagnostics
(mic, session, agents, screens, OCR), the wizard, the report, Quit.
**APP-15 · Journal** — real entries and walkthroughs (GW-16), due for
review, attach, recording and workflow capture.
**APP-16 · Home** — what is really happening: state, agents, today's
questions, walkthroughs, and what to try.
**APP-17 · Agents** — live tasks, plan, activity, approvals, stop/pause.
**APP-18 · Extensions** — the installed packs and their real switches.
**APP-19 · Accounting** — the pack's own data, or its dashboard, in the page.

### Part 3 — the rest (7–9, then the walkthrough plan)

**APP-20 · The wake word is always heard** — why 1 in ~N transcriptions
hangs for 12 s, and a shorter, retried path.
**APP-21 · Agent tasks judge themselves fairly** — the six failures
replayed; checks that test the outcome, not an incidental command.
(Mostly worker-owned code: specified here, assigned through TASKS.md.)
**APP-22 · The panel reads like a conversation** — one reply per block.
Then **GW-17 to GW-24** from `WALKTHROUGH_PLAN.md`.

---

## Cost

Everything above is built and proven with local tests and the logs; none of
it needs paid Gemini calls. Where a live check would add real confidence,
its estimated cost is stated and it waits for a yes.
