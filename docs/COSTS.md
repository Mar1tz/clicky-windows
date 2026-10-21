# What Tora costs us, and how it's counted

**Written for:** the owners. Every number here says where it comes from.
Last measured 21 Oct 2026.

## The rule

Each account has **one pot a month** for everything Tora costs us — voice,
screen looks, agent jobs. The pot is **half of what the plan leaves us after
Paddle's fee**, so a paying user's AI can never cost more than half of what
they pay. When the pot is empty, nothing new starts until the account's month
starts again (a paid plan: its renewal day).

| Plan | Price | After Paddle (5% + $0.50)¹ | The pot (`budget_usd`) |
|---|---|---|---|
| Free | $0 | $0 | $0.50 (50 credits) |
| Pro trial (referrals, 14 days) | $0 | $0 | $6 |
| Pro | $20 | $18.50 | **$9** |
| Max | $100 | $94.50 | **$47** |

¹ Paddle's standard fee, not checked against our Paddle contract yet.

Change a pot: `update plans set limits = limits || '{"budget_usd": 9}' where key = 'pro';`
— no release, no deploy.

**Users see the pot as credits** (migration 0012): 1 credit = 1 cent of what
Tora costs us, so Free 50, Pro trial 600, Pro 900, Max 4,700 a month. Credits are
the only limit shown; the old counts (25 asks, 150 tasks) stay only as hidden
brakes, set above what the credits buy. A
question is about 2 credits, a screen look under 1, an agent job 25 – 150.

**Bought credits:** on Pro and Max, packs of **675 for $10, 1,750 for $25,
3,500 for $50** — each a one-off price of the "HeyTora credits" product in
Paddle with custom data `credits`; the app lists whatever Paddle has. On $10
we keep $9.00 after Paddle ($0.50 + 5%), and 675 credits cost us at most
$6.75 — **about 25% margin**; bigger packs give more per dollar because
Paddle's $0.50 matters less (the owners, 21 Oct: lower than the plans' 50%, because
the subscription carries the main margin; Lovable charges more for top-ups
than for plan credits). They never expire and are used after the month's.
A pot runs out only when the month's credits and the bought ones are both
used up.

## What each thing costs

| What | Model | How it's counted | Measured cost |
|---|---|---|---|
| **A spoken answer (live mode)** | `gemini-3.8-live` | **Booked** at $0.015 when the question opens; the app reports Google's real bill after each answer and anything above the booking is added | $0.0099 a fresh answer; **$0.0147** deep in a long chat (range $0.0046 – $0.0252); in the app on 21 Oct: $0.0077 – $0.016 |
| **A spoken answer (normal mode)** | transcribe → `gemini-3.8-flash`/`3.1-pro` → `gemini-3.1-flash-tts` | **Exact**: every step after the transcription goes through our server, which prices Google's own token counts | not measured separately |
| **A screen look** | `gemini-3.8-flash` | **Exact**, through our server | $0.0006 – $0.0009 |
| **Dictation** (speech to text) | `gemini-3.5-transcribe-live` | **Booked** at $0.005 each | not measured; a long one costs more |
| **Wake-word check** | `gemini-3.5-transcribe-live` | **Booked** at $0.0005 each | not measured |
| **An agent job** ("build me an app") | Claude Sonnet 4.6 via OmniRoute | **Exact**, through our server, at Claude's list price ($3 in / $15 out per million tokens) | ~$0.25 – $1.50 a job (the app stops a job at 300k tokens) |

"Exact" means our server sees the call and Google's (or Claude's) own token
count, priced from the list in `supabase/functions/heytora/rules.ts`. "Booked"
means the call goes straight from the app to Google, so the server charges an
estimate it can't be cheated out of; the estimates are rows of
`direct_cost_estimates` — edit a row to change one.

Where the measurements come from: `tools/voice_eval.py` (15 questions, 21 Oct,
$0.149 on the owner's key) and `--conversation` (a long chat, 21 Oct, $0.088);
the app's answer log (`%LOCALAPPDATA%\HeyTora\tora.log`, "answer timeline").

## What the pot buys (Pro, $9)

| If they only… | About |
|---|---|
| talk (voice + a look, ~$0.0185 a question) | 490 questions a month, ~16 a day |
| run agent jobs | 6 – 36 jobs |
| a mix: 10 questions a day ($5.55) + 1 agent job a week (~$3) | ~$8.55 a month — just fits |

The website promises more (Pro: "up to 300 asks a day", 150 agent tasks a
month). The pot is the real limit — see `ROADMAP_BILLING.md`.

## Who built what

- **Exact pricing of everything through the server** (looks, normal mode,
  agents), the pot, windows and tickets: Torben, 1 Oct (migration 0004) and
  5 Oct (agents through OmniRoute).
- **The voice in the pot** (bookings, the app's report, the pot on every
  kind): 21 Oct (migration 0011).

## Seeing it

```sql
-- this month, per account (all costs, voice included)
select * from heytora_admin_usage();

-- the last 30 days, by kind
select kind, count(*) uses, round(sum(cost_micro)/1e6, 2) usd
from usage_costs where created_at > now() - interval '30 days'
group by kind order by usd desc;

-- one account's questions, each with its total
select window_id as question, round(sum(cost_micro)/1e6, 4) as usd, min(created_at) as at
from usage_costs
where user_id = (select id from profiles where email = 'someone@example.com') and window_id is not null
group by window_id order by at desc limit 20;

-- the bookings
select * from direct_cost_estimates;
```
