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

## Status — live since 1 Oct 2026

Project `uizwfpxzzkokyrkvjzkr` (eu-west-1), shared with the website.

| Step | State |
| --- | --- |
| Database: `0001`, `0002`, `0003` | applied (0001 was already there) |
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

* Allowances: edit `public.plans.limits` — no new download needed.
* A plan change from the website: set `profiles.plan` / `plan_status` with the
  service role key, on the website's server only.
* Redeploy the function: `supabase functions deploy heytora --project-ref uizwfpxzzkokyrkvjzkr --use-api`.

## Your own key

Settings → "Use my own Gemini key instead", or the first-launch "Connect"
page. The key is checked with Google, saved in the app's `.env`, and used
straight to Google — HeyTora's limits do not apply.
