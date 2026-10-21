# HeyTora accounts — your Gemini key stays on your server

Anyone who downloads HeyTora can use it on **your** Gemini key, within the
plan they chose on the website — without the key ever being in the app.
Or they use their **own** key, straight to Google.

## Why the key is not in the app

A key inside a program you give to the public can be read by anyone: from
the installer, or from the app's own network traffic. Encrypting it only
slows that down. So the key lives in one place — a Supabase server function
you control — and the app signs in instead.

## How it fits together

| Part | Where | What it does |
| --- | --- | --- |
| Account + plan | your website, same Supabase project | creates the account; sets `profiles.plan` (`free` / `pro` / `max`) and `profiles.plan_status` when someone picks or pays for a plan |
| Sign-in | the app (first launch, or Settings) | email + one-time code (Supabase Auth OTP; this project sends 8 digits), only for accounts that already exist |
| `heytora` server function | `supabase/functions/heytora/index.ts` (+ `rules.ts`) | holds `GEMINI_API_KEY`; checks the user, opens **windows** on their plan, mints **voice passes**, and forwards model calls - counting what each really cost |
| Plans & counting | `supabase/migrations/0003_heytora_plans.sql`, `0004_heytora_metering.sql` | the plans' allowances and ceilings, and the only functions that spend them |

**Live voice is still direct.** For talk and dictation the server mints a
voice pass — a Gemini Live *ephemeral token*: one session, locked to its
model, ended by Google at the moment the pass ends — and the app connects
straight to Google with it. Same speed as a key; nothing worth stealing.

**Everything else goes through the server**: reading the screen, planning
walkthroughs, the step voice, embeddings, the agents. The server adds the key.
Only the app's model families are forwarded (flash, flash-lite, pro, TTS,
embeddings) — no files, tuning, caches, search grounding, image or video
models — and an answer is capped at 16k tokens.

## What each plan allows

The app is easy to modify, so **the server decides** (migration 0004). What a
user buys is counted as a *window* the server opens and closes:

| | Free | Pro ($20) | Max ($100) |
| --- | --- | --- | --- |
| Talk messages / month | 25 | unlimited | unlimited |
| Dictations / month | 300 | unlimited | unlimited |
| Agent messages / month | 25 | 150 | 1,000 |
| *A talk message lasts at most* | *10 min* | *30 min* | *30 min* |
| *Hidden: daily fair use (talk / dictation / agent)* | *25 / 100 / 10* | *300 / 500 / 50* | *600 / 1,000 / 200* |
| *Hidden: monthly cost ceiling (what Google charges you, voice included)* | *$2* | *$9* | *$47* |
| *Hidden: requests / month* | *1,500* | *30,000* | *150,000* |

- **Talk message** — every time the user talks, on every plan, counted the
  moment Tora hears them (a press with nothing said counts nothing). It is a
  talk window: the voice pass and every call made for that question run on
  its **ticket**, and all of it stops when the window ends — Google itself
  closes the voice session then. At most 3 voice passes per window on Free
  (6 on Pro/Max), so one talk message can't become many parallel sessions.
- **The voice waiting for a question** (Pro/Max open it at start-up and after
  a drop) is a *standby* window, kind `voice` (migration 0007): never a talk
  message, a hidden daily ceiling, and no ticket — it can carry the voice,
  never an answer. Free has none: its voice opens on the question.
- **Agent message** — an agent window: up to 30 minutes of agent work.
- **Wake word** — with "say Tora" on, speech near the PC is checked for the
  name by the dictation model. Each check is its own kind, `wake` (migration
  0006): never a dictation, one minute per pass, a hidden daily ceiling
  (Free 500, Pro 2,000, Max 3,000; when it is reached the app stops checking
  for 15 minutes), and its own column in the business's report.
- **Every answer needs a ticket.** A model call without a valid window
  ticket is refused, and the server records the ticket's kind — never the
  app's own label — so agent work can't pass itself off as talk.
- **Cost ceiling** — every forwarded call's real token counts (from Google's
  own `usageMetadata`) are priced and recorded in `usage_costs`; once a
  month's total reaches the plan's `budget_usd`, forwarded calls stop until
  the 1st. Prices are on the high side of Google's list (`rules.ts`); set the
  `HEYTORA_PRICES` secret to change them.
- **The voice is in the ceiling too** (migration 0011, 21 Oct 2026). It goes
  straight to Google, so each question, dictation and wake-word check is
  **booked** as it opens at `voice_estimates`' price (a question $0.01, from
  `tools/voice_eval.py`). After every answer the app sends Google's
  `usageMetadata` (`POST /voice-usage`); the server prices it
  (`rules.ts liveCostMicro`) and adds whatever is above the booking — never
  less. The ceiling is checked before **any** use opens. Ceilings are 50% of
  what a plan leaves after Paddle's fee (the owners, 21 Oct): Pro $9, Max $47.
  Edit `voice_estimates` to change a booking; no release needed.
- **Per-minute brake** on every plan, and a lapsed or cancelled subscription
  (`plan_status` not `active`/`trialing`) counts as Free.
- All of these numbers are in `public.plans.limits` — change them any time,
  no new download needed. The app and website only ever see talk, dictation
  and agent (`get_usage_summary`).

## The agents' model: OmniRoute (Claude) only

Since 5 Oct 2026 the agents think with Claude Sonnet through OmniRoute and
nothing else (no Gemini fallback):

* On a PC with `TORA_OMNIROUTE_BASE_URL` / `TORA_OMNIROUTE_API_KEY` set (the
  owner's source copy), straight to OmniRoute.
* Everyone else, signed in: through the `heytora` function's
  `POST /omniroute/v1/chat/completions`. The OmniRoute key and address are the
  function's secrets (`OMNIROUTE_API_KEY`, `OMNIROUTE_BASE_URL`), never in the
  app. Each call needs the agent message's ticket (an agent window), only the
  Claude routes are allowed (`HEYTORA_OMNIROUTE_MODELS` overrides), answers
  are capped at 16k tokens, and each call's tokens are priced at Claude
  Sonnet's list price into `usage_costs` - the plan's monthly cost ceiling
  covers agents too.
* The owner pays OmniRoute for every user's agent work.

## What the user sees

- **Settings → "Your HeyTora account"**: the plan and when it starts again;
  talk, agent and dictation as "7 of 25 this month · 18 left" with a meter;
  "today's fair use reached" when that applies; *overall use this month* (the
  cost ceiling as a percentage, never dollars); "This month's use is up" when
  it is. The plan shown is the account's own (`account.service`): remembered
  from the last run, so it appears at once, and read again whenever the page
  comes on screen. A read that fails says so, with **Try again**, and keeps
  the last known use - never a silent "Reading your plan…".
- **The counts move with every question**: each window the server opens
  reports this month's count, and every screen hears it at once.
- **The tray's first line**: "HeyTora · Free plan - 18 talk messages left".
- **A heads-up at 80% and at 100%** of each allowance, today's fair use, and
  the month's overall use - each once a month, remembered across restarts.
- When an allowance runs out the server's own words say which and what to
  do - in the caption, the tray and Settings.
- Window ends are measured on the **server's clock**, so a PC whose clock is
  off keeps working; a ticket the server has ended is asked for again once.

The website reads the same `get_usage_summary()` with the user's sign-in:
`plan`, `plan_name`, `limits`, `usage`, `today`, `blocked`, `fair_use`,
`period_end`, `day_resets_at`, `now`.

## What the business sees

```sql
select * from heytora_admin_usage();              -- this month
select * from heytora_admin_usage('2026-09-01');  -- any month
```

One row per account: plan, talk / dictation / agent / requests, what Google
charged for their forwarded calls (`cost_usd`), last use. Service role only:
the Supabase SQL editor, or the website's server — never the app or a
signed-in user.

## Status — live since 1 Oct 2026

Project `uizwfpxzzkokyrkvjzkr` (eu-west-1), shared with the website.

| Step | State |
| --- | --- |
| Database: `0001` – `0007` | applied (0001 was already there; 0004 – 0007 on 2 Oct) |
| Secrets `GEMINI_API_KEY`, `HEYTORA_TICKET_SECRET` | set (Management API; never in git) |
| Function `heytora` | deployed, JWT verification on |
| Redirect URLs | `http://127.0.0.1:*/heytora/signed-in` — **the website must keep this entry** when it adds its own |
| `account/settings.py` | project URL + anon key (public by design) |
| Email sender | **still Supabase's built-in one** — see below |

Checked live with throwaway users (made with the admin API, no email sent,
deleted after): sign-in, refresh, sign-out; plan summary; talk and
dictation voice passes opening at Google; agent tickets; forwarded calls,
and the refusals (no sign-in, no ticket, files API, a user giving
themselves a plan, the 26th Free talk message).

### Signing in: the link, or the code

Supabase's built-in email sender sends a **link** only — a free project on it
may not change the email. So the app catches the link itself
(`account/loopback.py`): it asks for the email with a PKCE challenge and
`redirect_to=http://127.0.0.1:<port>/heytora/signed-in`, serves that one
page while it waits, and swaps the code the browser brings back for a
session. Clicking "Sign in" in the email on the same PC is enough. The
code box stays for when the email shows a code.

### Before real customers: an email sender

The built-in sender delivers only to the Supabase organisation's own team
addresses, two emails an hour. For customers, set up custom SMTP
(Authentication → Emails → SMTP; e.g. Resend, Postmark, SES — needs a
domain you own). Then the "Magic Link" template can show the code as well;
keep the link in it (the website and the app both use it):

```html
<h2>Your HeyTora sign-in code</h2>
<p>Type this code into HeyTora:</p>
<p style="font-size:28px;font-weight:700;letter-spacing:6px">{{ .Token }}</p>
<p>Or <a href="{{ .ConfirmationURL }}">sign in with this link</a>.</p>
```

### Changing things later

* Allowances and ceilings: edit `public.plans.limits` — no new download needed.
* What each user's calls cost this month: `select user_id, sum(cost_micro)/1e6 as usd from usage_costs
  where created_at >= date_trunc('month', now()) group by 1 order by 2 desc;`
* A plan change from the website: set `profiles.plan` / `plan_status` with the
  service role key, on the website's server only.
* Redeploy the function: `supabase functions deploy heytora --project-ref uizwfpxzzkokyrkvjzkr --use-api`.

## Your own key

Settings → "Use my own Gemini key instead", or the first-launch "Connect"
page. The key is checked with Google, saved in the app's `.env`, and used
straight to Google — HeyTora's limits do not apply.
