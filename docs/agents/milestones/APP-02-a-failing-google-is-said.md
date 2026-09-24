# APP-02 — a busy or out-of-credit Google is said out loud

**Status: done.** 30 new tests, 1002 passing.

## The problem

* **22 September:** 77 requests failed with *"Your prepayment credits are
  depleted"* over eleven hours. The live session, the older speech path
  and the TTS voice all failed. Tora went quiet and could not say why,
  because her voice runs on the same account. At most a toast showed the
  raw websocket error.
* **24 September, 17:07:** a walkthrough failed on two 503s (*"This model is
  currently experiencing high demand"*), fast model and then pro model. It
  was reported as *"I couldn't work out the steps for that from this
  screen"*. You restarted Tora a minute later.

## What was built

**Every failure is named** (`gemini.problem_of`). There are four kinds, in
terms a person can act on:

* **billing:** credits depleted, quota exceeded, 402;
* **key:** key not valid, permission denied, 401/403;
* **busy:** 503, 429 rate limit, "high demand";
* **network:** getaddrinfo, a handshake timeout.

A 429 that says *quota* counts as billing, not load.

**A busy server is asked again.** Retries happen twice, 1 s and then 2 s
apart, for plain requests and for streams. Streams are only retried
before anything has arrived. Running out of credit is never retried,
because asking again cannot top up an account.

**Account problems reach the app from wherever they happen** (a request, a
stream or the live session's connect) through one report point. They are
**shown**:

* **in the caption, on every failed request:** *"Tora can't reach Google:
  your Gemini credits have run out. Top them up at ai.studio/projects and
  she'll work again."*;
* **as a toast plus a tray warning, at most once every ten minutes:**
  *"⚠ Gemini credits have run out — top up…"*, which opens AI Studio. For a
  refused key it is *"⚠ Google refused the API key — fix it…"*, which opens
  API keys.

When a request gets through again, the warning comes down by itself.

**Walkthroughs give the real reason.** They say *"Google's planner is
overloaded right now — ask me again in a moment"* instead of blaming the
screen. No credit, a refused key or no network skip the pro-model fallback,
which would fail the same way after 10–20 s more. A busy fast model still
gets one careful try, since the two are served separately.

## Proof

`tests/agents/test_e2e_account_problems.py` (30):

* ten real error texts are named correctly;
* a busy server is asked again and recovers, or fails after two retries;
* credit problems are not retried;
* a busy stream is asked again;
* a problem and its end are reported, the end only once;
* a refused live session reports it;
* 77 reports produce one notice, which is shown again after ten minutes
  and taken down when Google answers;
* the caption shows why for each known kind;
* the planner says "overloaded";
* the careful model is tried only when it could help;
* the tray warns while the problem lasts.
