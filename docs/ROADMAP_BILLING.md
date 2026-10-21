# Roadmap: billing, usage and referrals

**Written for:** the owners, and the sessions that build it. Work top to
bottom; tick each box when it's done and checked.

Builds on `docs/PAYMENTS.md` (Paddle), `docs/agents/ACCOUNTS.md` (plans,
limits, the cost ceiling) and `docs/REFERRALS.md` (Pro trial, free months).

## Owners' decisions

- [x] **Margin target:** AI cost at most **50%** of what we receive per plan
      (the price after Paddle's fee): Pro ≈ $9, Max ≈ $47 a month for
      everything, voice included. (Owners, 21 Oct 2026; revisit after launch.)
- [ ] **Refunds:** when Paddle refunds a payment or a chargeback comes in —
      end the plan at once, or leave it to run to the end of the period?
- [ ] **Refund window:** should a new subscription's first 14 days run on a
      lower ceiling (like the Pro trial's $6)?
- [ ] **Free per PC:** one Free allowance per device, shared by every account
      on that PC?
- [ ] **Pro trial ceiling:** keep $6 for the friend's 14 days, or half of
      Pro's new ceiling?
- [x] **Extra usage instead of waiting** (owners, 21 Oct): visible credits
      (1 credit = 1 cent); Free 50 a month; packs 675/$10, 1,750/$25,
      3,500/$50 (~25% margin) from Paddle, Pro and Max only, never expire;
      plans sold in credits in the app and on the website. Built: migration
      0012, `POST /credits`, the webhook, Plans & billing. To do: the
      product's id in `billing.ts`, tick `transaction.completed`, apply
      0012, deploy both functions and the website, test a purchase; the same
      in the live Paddle account.
- [ ] **OmniRoute's routes:** what account sits behind `claude/`, `cc/` and
      `agy/`? Customers' agent jobs must run on the paid Claude API, not on a
      personal Claude Code or Antigravity subscription.

## Checklist

**Tidy up**
- [ ] Commit the plan-change fix (`supabase/functions/heytora/index.ts`,
      deployed 21 Oct).
- [ ] Commit the website's new sandbox client token (`heytora-web/src/lib/site.ts`).
- [ ] `PAYMENTS.md`: correct the price ids (live ones are listed as
      "sandbox"); record that sandbox is the **old** Paddle account and live
      the **new** one.

**Check what's there**
- [ ] Why two Free accounts went over the $2 ceiling (Cameron $5.80, Torben
      $3.09 in 30 days). Per month, and whether they were on a Pro trial:
      ```sql
      select p.email, p.plan, date_trunc('month', c.created_at) as month,
             round(sum(c.cost_micro)/1e6, 2) as usd
      from usage_costs c join profiles p on p.id = c.user_id
      where p.plan = 'free' group by 1, 2, 3 order by 1, 3;
      select * from referrals where friend in (select id from profiles where plan = 'free');
      ```
      If neither explains it, the ceiling has a bug — fix it first.
- [ ] Migrations `0008`–`0010` applied on the live project.
- [ ] Prices current: `rules.ts` `DEFAULT_PRICES` and `ai/gemini_live.py`
      `LIVE_PRICES` against Google's list; Claude against what OmniRoute bills;
      Paddle's fee against our Paddle account.
- [x] Measure voice with `tools/voice_eval.py` (21 Oct): $0.0099 a fresh
      answer (small talk $0.0046, two actions $0.0155); **$0.0147** an answer
      deep in a long session (`--conversation`, $0.0084 – $0.0252). Google
      bills per answer, not per minute: an idle session costs nothing.

**One cost ceiling for everything**
- [x] Migration `0011`: each question, dictation and wake-word check is
      booked as it opens (`direct_cost_estimates`: $0.015 / $0.005 /
      $0.0005); reports are rows of `usage_costs`, one ledger.
- [x] The ceiling is checked for **every** kind before it opens, not only
      forwarded calls.
- [x] `POST /voice-usage {window, usage}`: the server prices Google's
      `usageMetadata` itself and adds only what's above the booking
      (at most $0.25 an answer, $5 a question). Deployed 21 Oct.
- [x] App: `Account.report_voice` after every answer
      (`companion_manager._note_voice`), in the background.
- [x] Tests: `test_e2e_voice_cost.py`, `heytora_rules.test.ts`.
- [x] `budget_usd`: Pro $9, Max $47 (50%); Free stays $2.
- [ ] **Apply `0011`** in the Supabase SQL editor.
- [ ] Ship the app with `report_voice` (until then, only the bookings count).
- [ ] Check live: ask one question, then `heytora_admin_usage()` shows its
      voice in `cost_usd`.
- [ ] Dictation and the wake word: report their real cost too (bookings only
      for now; a 30-minute dictation is worth far more than $0.005).
- [ ] Website: Pro's "up to 300 asks a day" — the $9 ceiling is about 490
      questions a month (~16 a day) at $0.0185 a question (voice $0.015 +
      the look $0.0035). Change the wording, the price, or the ceiling.
- [ ] Daily check (see Alarms): an account with many standby windows and few
      questions is an app changed to talk without opening questions — the
      one way round the booking.
- [x] `ACCOUNTS.md` updated. `REFERRALS.md` follows the Pro trial decision.

**Confirm before a plan change**
- [ ] Server: `POST /checkout {plan}` for someone already paying returns a
      preview — `{confirm: {charge_now, credit, currency, next_amount,
      next_date}}` from Paddle's `PATCH /subscriptions/{id}/preview` — and
      `{plan, confirm: true}` makes the change.
- [ ] App: a dialog in `PlansTab` — "Switch to Max: $80.00 now, then
      $100/month from 6 Nov. [Switch] [Cancel]". A downgrade shows its credit.
- [ ] Tests for both; sandbox Pro → Max → Pro.

**Refunds** (as decided above)
- [ ] Webhook also takes `adjustment.created` / `adjustment.updated`: an
      approved refund or chargeback on a subscription does what the owners
      decided, recorded in `billing_events`.
- [ ] If decided: the refund window's lower ceiling.

**Referrals**
- [ ] A friend's Pro trial runs under the new ceiling (voice included) and
      ends on `trial_until` back to Free.
- [ ] Sandbox: a referrer with a month waiting renews → the next bill is
      $0 (the 100% discount, made by the webhook; the API key needs
      **discounts**).
- [ ] Sandbox: a referrer on Free with a month banked subscribes → the month
      goes on their first renewal, not lost.
- [ ] A friend reaching 15 talk messages within the 14 days turns the referral
      "qualified" and earns the month; after 14 days it doesn't.
- [ ] Withdrawn: a month not yet used is withdrawn when the friend's account
      is deleted or flagged.
- [ ] If the owners decide refunds end the plan: a refunded bill that used a
      free month gives the month back.

**Free-account abuse**
- [ ] If decided: Free allowance per device ID (already sent for referrals).
- [ ] Refuse disposable email domains at sign-up too, reusing
      `public.disposable_domains` (0010).

**Alarms**
- [ ] Daily check (`pg_cron`): accounts past 80% of their ceiling in their
      first week, and the day's total cost — emailed to the owners.
- [ ] Google Cloud budget alert on the Gemini key's project; OmniRoute's
      spend limit.
- [ ] Paddle emails when the webhook keeps failing.

**Customers can sign in**
- [ ] Custom SMTP sender for Supabase (ACCOUNTS.md, "Before real
      customers"), with the code in the email.
- [ ] Supabase → Email → *Allow new users to sign up* is on.

**Go live on the new Paddle account** (`PAYMENTS.md`, "Going live")
- [ ] Products Pro $20 / Max $100 monthly; each price's custom data
      `{"plan": "pro"}` / `{"plan": "max"}`.
- [ ] `LIVE_PRICES` in `billing.ts` and its test (`test_e2e_billing.py`).
- [ ] API key (incl. discounts); webhook destination with `subscription.*`
      and `adjustment.*`; its secret.
- [ ] Default payment link `https://heytora.co.za/pay`; domain approved.
- [ ] Secrets `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_ENV=production`.
- [ ] Website: the new `live_` client token, `PADDLE_ENVIRONMENT = "production"`.
- [ ] Clear sandbox `paddle_customer_id` / `paddle_subscription_id` from every
      test profile and set them to Free.
- [ ] Real test: buy Pro with a real card → app shows Pro → upgrade to Max
      through the dialog → refund in Paddle → the account does what the
      owners decided.

**Local development (a pass of its own)**
- [ ] A local Supabase for the owners to experiment on, as Stuurly has: the
      database, the functions and the website running on the PC, nothing
      real touched.
- [ ] Database tests that run the migrations on it (voice booking, credits
      bought / spent / refused), not only read them.
- [ ] One script to deploy when ready: migrations, both functions, secrets
      check, the website.

**After launch**
- [ ] Week 1 and week 4: `heytora_admin_usage()` — reset the ceilings and voice
      estimates from real customers.
