# HeyTora's server, faster (19 Oct 2026)

From the speed review (AC-02, AC-03, FX-06, AC-07). The changes are in the
repo and tested; **nothing is deployed** — the owner deploys.

## What changes

| | Before | After |
| --- | --- | --- |
| Sign-in on a voice pass, ticket, plan read or look (AC-02) | a call to the auth service every time | the token's signature checked in the function; the auth service only when it cannot be checked here. Account, checkout, billing and referral routes still ask it |
| A talk voice pass (AC-03) | two database calls in series (`heytora_window`, then `heytora_pass`) | one: `heytora_talk_pass` (migration 0011). Until 0011 is applied the function uses the two calls, so either can go first |
| Google refusing for billing (FX-06) | nobody told until users noticed (22 Sep, 6–9 Oct) | a `BILLING` line in the function's log, and a message to a webhook at most every 30 minutes |
| Where a call's time went (AC-07) | `Server-Timing` on looks only | on every route; the app logs it beside any HeyTora call over 1 s |

| Tora's web search (19 Oct) | refused (`Tool not forwarded: google_search`), so the app scrapes free engines: 2–7 s, once 22 s | Google Search through Gemini is forwarded: a short, dated answer in ~2 s. Each search is counted against the plan's ceiling at $0.014 (`SEARCH_MICRO`) — Google bills $14 per 1,000 past the first 5,000 a month |

Not done: AC-04 (start the Google request while usage is being recorded).
A modified app at its limit would make the server pay Google for calls it
then refuses, for about 50 ms of gain.

## Deploy

1. **The migration.** `supabase db push`, or paste
   `supabase/migrations/0011_heytora_talk_pass.sql` into the SQL editor.
2. **Secrets (both optional).**
   - `supabase secrets set HEYTORA_JWT_SECRET=...` — Dashboard → Project
     Settings → JWT Keys → the legacy JWT secret. Without it the sign-in is
     checked by the auth service, as before.
   - `supabase secrets set HEYTORA_ALERT_WEBHOOK=...` — a Slack or Discord
     incoming-webhook URL. Without it the billing alert is a log line only.
3. **The function.** `supabase functions deploy heytora --use-api`.
4. **Check.** Ask Tora something. In `%LOCALAPPDATA%\HeyTora\tora.log`, a
   slow call now reads, for example:
   `HeyTora POST voice-pass took 1.3s (server: auth_local;dur=2, window_pass;dur=41, google;dur=310, total;dur=360)`.
   `auth_local` means the secret is working; `auth` means the auth service
   was asked.

## The slow voice passes at :00 and :30

About twice an hour — mostly on the hour and the half hour — a wake-word
voice pass takes about 10 s and the wake check times out (19 of ~50
timeouts on 9–19 Oct were at :00 or :30). Once this is deployed, the next
one's log line says where the time went:

- `auth` or `auth_local` large: the auth service or the token check;
- `consume` or `window_pass` large: the database (something scheduled — a
  backup, a cron job, a vacuum — holding it up);
- `google` large: Google minting the token;
- `total` small but the call slow: the trip there — a cold start of the
  function, or the network.

### Measured from the PC, 19 Oct, 12:51–13:01

Every ~14 s: an unsigned HEAD to the Supabase gateway (no function), a HEAD
to Google, and the plan read (`GET /me`: the function, the sign-in check and
one database call).

| | gateway | Google | the function |
| --- | --- | --- | --- |
| 12:51–12:59 | ~300 ms | ~400 ms | 0.8–1.5 s, twice ~4 s |
| 12:59:57 | 330 ms | 388 ms | 2.2 s |
| **13:00:12** | **349 ms** | **384 ms** | **19.5 s** |
| 13:00:45–13:01:01 | ~350 ms | ~400 ms | 3.0–4.4 s |

So it is not the network or Google: the function's own work - the sign-in
check (Supabase Auth, `getUser`) or the database - stalls on the hour. With
AC-02 deployed and `HEYTORA_JWT_SECRET` set, the routes every question takes
no longer ask Auth at all; if the stall is the database, the `window_pass` /
`consume` / `usage` timings will show it. Meanwhile the local wake word
(HP-08) keeps most wake checks off the server.
