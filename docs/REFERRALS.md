# Referrals: give 14 days of Pro, get a free month

**Written for:** the two owners, who decide, and whoever builds it.

**Status (7 Oct 2026): Phase 1 built.**
- **Database:** migration `0010_heytora_referrals.sql`.
- **Server:** the routes in the `heytora` function, and the free month on the
  next bill in the `paddle` webhook.
- **App:** `ui/referral_window.py`, `account/device.py`, the claim at sign-in,
  and the trial in Plans & billing.
- **Website:** `/r/[code]`, the note in `/login`, `/signed-in` forgetting the
  code, plus the terms and privacy text.
- **Tests:** `tests/agents/test_e2e_referrals.py` and
  `supabase/tests/referrals.test.ts`.

## The offer

> **Give a friend 14 days of Pro. When they've asked Tora 15 questions, you
> get a free month.**

This is Wispr Flow's model ("give a month of Pro, get a month"), sized to our
costs:

| | Gets | When |
|---|---|---|
| **The friend** | **14 days of Pro**, free | As soon as they sign up through the link or code |
| **The referrer** | **1 free month** of their plan | When the friend has **asked Tora 15 questions** |

The referrer's free month only counts **against a payment**. If they subscribe,
their next bill is free; Paddle applies it automatically. If they're on
Free, the month is **banked** ("1 free month waiting - subscribe in the app to
use it") and used on their first renewal. Banked months **never expire**.

That is the point of the design: a reward only has value to someone who pays.
It turns free users into paying ones, and fake referrals gain nothing without
a real card.

## What it costs

Each plan has a hidden monthly ceiling on its Gemini cost (migration 0004):
Free $2, Pro $12, Max $60.

- **A friend's 14 days** run on a new **"Pro trial"** plan: Pro's allowances in
  full, with a **$6** ceiling. That's Pro's own $12-a-month fair use for 14
  days, so it is Pro, not a cut-down version. That matters, because it's
  advertised as Pro. It's the worst case per friend; a typical one costs $1–3.
- **Measuring real costs:** every call's cost is already recorded per user
  (`usage_costs`, 0004). `select * from heytora_admin_usage();` shows each
  user's monthly cost. The ceilings are estimates: set them from a few weeks of
  real use, and check the prices in `functions/heytora/rules.ts` against
  Google's price list before launch.
- **A referrer's free month** is one bill not charged ($20 for Pro). Its real
  cost is that month's usage, up to the plan's ceiling, and only for someone
  who is already paying us.
- **It pays for itself** if roughly 1 in 5 referred friends goes on to pay.

## The rules

0. **One personal code per referrer, for any number of friends.** It never
   changes unless the referrer presses **refresh** (Wispr has the same icon).
   That gives a new code and retires the old one, for a link that ended up
   somewhere unwanted. Friends who already used the old code keep everything.
1. **Codes and links are for new accounts only.** A code can be applied within
   an account's **first 7 days**, **once**. It can't be your own, or from
   someone you referred. So a referrer can never use their friend's code: their
   account is too old and already exists.
2. **One trial per PC.** The app sends a one-way device ID: Windows' machine ID,
   hashed with a secret, so it can't be traced back to the PC.
   - A PC that has already had a trial doesn't get another.
   - A friend on the **same PC as the referrer** never counts.
3. **15 questions** means 15 talk messages. Those are counted on the server
   already (`usage_events`), so the app can't fake them. They must be asked
   within the 14 days.
4. **Limits:** at most **12 earned months a year** per referrer. More than **3 in
   a calendar month** is flagged for the owners to look at, not refused.
5. **No throwaway emails.** Known disposable-email domains can't apply a code.
6. **Withdrawn if abused.** A reward not yet used on a bill is withdrawn if the
   friend's account is deleted or flagged.
7. **Fair use applies**, as it does to Pro: the referral terms say so.

**Self-referral with a second account.** Someone with two PCs who really uses
the second account gets, at most, one month off their own subscription, while
paying for every other month, and only up to the caps. It can't be prevented
completely, by us or anyone. The design keeps it rare and cheap.

## How a referral is tracked

The hard part: the path is link → website → installer → app → sign-in, with no
account on the website, and the installer carries nothing across.

1. The link is `heytora.co.za/r/DYLAN7`. The page there remembers the code
   in the browser, says "Dylan gave you 14 days of HeyTora Pro", and offers the
   download.
2. The friend installs, then clicks **Sign in via browser**. `/login` opens **in
   the same browser** and finds the code. It tells the server "this sign-in
   (its one-time PKCE challenge) came from DYLAN7". The server keeps that for
   an hour.
3. After sign-in, the app claims the referral (`POST /referrals/claim`),
   signed in as the new account, with the PKCE **verifier**: the challenge's
   secret half, which never left the app. The server checks it matches the
   challenge in the note, so the claim provably comes from the app that
   started that sign-in. It then applies the rules above and starts the 14
   days.
4. The website's "You're signed in" page forgets the code, so a second account
   made later in the same browser doesn't take it again.
5. **Fallback:** the **Apply referral** tab takes a code by hand, within the
   first 7 days. That covers a link opened on a phone, a different browser,
   and so on.

## What people see (Wispr's layout)

The sidebar's **Get a free month** opens a window with three tabs:

- **Refer**
  - A gift card: "HeyTora Pro · 14 days free · Gifted by Dylan".
  - **How it works:** *Share your link* → *They get 14 days of Pro* → *You get a
    free month when they've asked Tora 15 questions*.
  - **Your invite link**, with **Copy**, and a small **refresh** icon (new code;
    the old one stops working for new sign-ups).
  - Small print: "Free months apply to your next payment. Subscribe in the app
    to use them."
- **Past referrals (n)**
  - Each friend: *Joined · 9 of 15 questions* → *Earned you a month*.
  - Months waiting / used.
  - Empty: "You haven't referred anyone yet".
- **Apply referral**
  - A code box with **Apply**, for new accounts only. It says why when it
    can't be used.

**The friend**, after sign-in, sees: "Dylan gave you 14 days of Pro. Ask Tora
15 questions and Dylan gets a free month too." Their Plans & billing shows
"Pro trial until 21 Oct".

## How it's built

- **Database:**
  - `referral_codes` (user → code);
  - `referrals` (referrer, friend, status: joined / qualified / rewarded /
    withdrawn, questions so far, and when);
  - `reward_months` (earned, used on which Paddle transaction);
  - the `pro_trial` plan row;
  - on `profiles`: `trial_until` and the device ID hash;
  - a check that turns a referral "qualified" at 15 talk messages.
- **Server (the `heytora` function):**
  - `GET /referrals` (your code, your referrals, months waiting);
  - `POST /referrals/apply {code}`;
  - `POST /referrals/claim {challenge}`;
  - `POST /referrals/intent` (from `/login`, no sign-in, rate-limited).
- **Paying referrers:** at each renewal, if months are waiting, the server
  applies a one-off 100% Paddle discount to that subscription's next bill and
  marks the month used.
- **App:**
  - the three-tab window (from the sidebar card, replacing "Coming soon");
  - the device ID, sent with the claim;
  - the friend's welcome line;
  - "Pro trial until …" in Plans & billing.
- **Website:**
  - `/r/[code]`: shows the referrer's **first name only** (from a public
    lookup), keeps the code in a 30-day first-party cookie, and offers the
    download. An unknown code still shows the page, without the gift line.
  - `/login`: posts the note (`POST /referrals/intent {challenge, code}`) just
    before starting Google or the email link. The note is public,
    rate-limited, real codes only, and kept for 1 hour.
  - `/signed-in`: forgets the code.
- **Terms and privacy:** a short referral section on /terms, and one line in the
  privacy policy about the device ID.

## Phases

| Phase | What |
|---|---|
| **1** | Everything above, with invites by link and code |
| **2** | Email invites (after the email sender is set up), notifications ("Sam joined", "You earned a month"), and tiers if wanted (e.g. 3 referrals → a month of double allowance) |
| **Later, separately** | An affiliate programme with cash commission for partners, through a Paddle-compatible tool. Not part of referrals |
