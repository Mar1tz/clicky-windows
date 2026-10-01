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
| Sign-in | the app (first launch, or Settings) | email + 6-digit code (Supabase Auth OTP), only for accounts that already exist |
| `heytora` server function | `supabase/functions/heytora/index.ts` | holds `GEMINI_API_KEY`; checks the user, spends their plan, then mints **voice passes**, issues **agent tickets**, and forwards the other Gemini calls |
| Plans & counting | `supabase/migrations/0003_heytora_plans.sql` | the three plans' allowances and `heytora_consume()`, the only thing that spends one |

**Live voice is still direct.** For talk and dictation the server mints a
voice pass — a Gemini Live *ephemeral token*: one session, opened within
2 minutes, at most 30 minutes long — and the app connects straight to Google
with it. Same speed as a key; nothing worth stealing.

**Everything else goes through the server**: reading the screen, planning
walkthroughs, the step voice, embeddings, the agents. The server adds the key.
Only model calls are forwarded (no files, tuning or caches).

## What each plan allows (per calendar month, UTC)

| | Free | Pro ($20) | Max ($100) |
| --- | --- | --- | --- |
| Talk | 25 conversations | unlimited | unlimited |
| Dictation | 300 | unlimited | unlimited |
| Agent messages | 25 | 150 | 1,000 |
| Other requests (hidden backstop) | 1,500 | 30,000 | 150,000 |

- **Talk** — one voice pass. On Free the app opens the voice session when the
  user asks and closes it after 20 s quiet, so one question = one talk
  message (a quick follow-up inside 20 s rides on it). Pro and Max keep the
  session open as before.
- **Agent message** — one agent ticket: up to 30 minutes of agent work.
- **Backstop** — never on the price list; stops any plan being used to drain
  the key. Every plan also has a per-minute brake.
- A lapsed or cancelled subscription (`plan_status` not `active`/`trialing`)
  counts as Free. Change the numbers in the `plans` table at any time — no new
  download needed.

When an allowance runs out, the user is told which one and what to do
(upgrade on the website, or use their own key) — in the caption, the tray and
Settings → "Your HeyTora account" (which shows the plan and this month's use).

## Switching it on — once

1. **Database.** In the Supabase SQL editor (or `supabase db push`), run
   `supabase/migrations/0001_auth_usage.sql` (if not already) and then
   `0003_heytora_plans.sql`. If your website already has its own `profiles`
   table, it needs `plan` (text, one of `free`/`pro`/`max`) and
   `plan_status` (`active`, `trialing`, `past_due`, `canceled`) — or tell
   Claude where it keeps the plan and the two functions are adapted.
2. **Email codes.** Supabase → Authentication → Email: keep Email enabled, and
   make the "Magic Link" email template show the code: `{{ .Token }}`.
3. **Secrets** (never in the app, never in git):
   ```
   supabase secrets set GEMINI_API_KEY=<the business key> HEYTORA_TICKET_SECRET=<a long random string>
   ```
4. **Deploy the function**: `supabase functions deploy heytora` (JWT
   verification stays on).
5. **Tell the app where it is** — `account/settings.py`: the project URL, the
   anon/publishable key (public by design), and the website URL. Then build a
   new installer (`build.bat installer`).

Until step 5, a build offers only "use my own Gemini key".

## Your own key

Settings → "Use my own Gemini key instead", or the first-launch "Connect"
page. The key is checked with Google, saved in the app's `.env`, and used
straight to Google — HeyTora's limits do not apply.
