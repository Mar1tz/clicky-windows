# Payments: paid plans through Paddle

**Written for:** whoever switches payments on (the owners), and whoever
changes this code later.

## How it works

- **Paddle is the merchant of record.** It sells the subscription, takes the
  card, charges and files VAT/sales tax, and sends receipts. Our privacy
  policy, terms and refunds pages already say so. Card details never reach
  HeyTora.
- **Accounts are made in the app.** Typing an email in the sign-in screen
  sends a code. A new email gets its account that way: `create_user: true` in
  `account/service.send_code`. The website has no accounts.
- **Buying a plan:**
  1. Settings → Plans & billing → **Choose Pro**.
  2. The app calls `POST /checkout` on the `heytora` function.
  3. The function creates a Paddle transaction tagged with the signed-in
     user's id (`custom_data.user_id`). Who pays is always the sign-in's,
     never something the app sends.
  4. The app opens Paddle's checkout URL. Paddle sends the browser to the
     website's `/pay` page, which loads Paddle.js and shows the checkout.
- **The plan changes only when Paddle says so.**
  1. Paddle calls the `paddle` function, a webhook with every
     `subscription.*` event.
  2. The function checks Paddle's signature.
  3. It calls `heytora_billing_event` (migration 0008). That sets the
     profile's plan, status, renewal date and cancel date. It ignores repeats
     and older events that arrive late.
  4. The app watches for the new plan for 20 minutes after opening the
     checkout. Limits change at once, because the server already enforces
     `plans.limits`.
- **A failed renewal** ("past due") drops the account to Free at once. The
  app says the payment didn't go through and offers Manage billing. When
  Paddle collects, the plan comes back by itself.
- **Already paying:** choosing another plan changes the same subscription
  straight away (`PATCH /subscriptions`). Paddle prorates it. Choosing again
  a plan that was cancelled but hasn't ended undoes the cancellation.
- **Manage billing** (`POST /billing`) opens Paddle's customer portal:
  change card, see invoices, cancel. A cancelled plan stays until its period
  ends. Then it's `canceled`, and the database treats the account as Free.
- **Delete account:** cancels the Paddle subscription immediately, then
  deletes the account.
- **Usage limits stay in Supabase.** Paddle only decides which plan applies.

| Piece | Where |
|---|---|
| Rules (signature, event → plan, prices) | `supabase/functions/_shared/billing.ts`, tested in `supabase/tests/billing.test.ts` |
| Webhook | `supabase/functions/paddle/index.ts` |
| Checkout, plan change, portal, cancel on delete | `supabase/functions/heytora/index.ts` |
| Database | `supabase/migrations/0008_heytora_billing.sql` |
| App | `account/service.py` (`checkout`, `billing_portal`), `ui/app_bridge.py`, `ui/settings_tabs.py` (`PlansTab`) |
| Website | `heytora-web/src/app/pay/page.tsx`, `src/components/site/pay-checkout.tsx`, `pay` in `src/lib/site.ts` |
| App and server tests | `tests/agents/test_e2e_billing.py`, `tests/agents/test_e2e_heytora_account.py` |

## Switching it on (sandbox first)

The sandbox prices are already in the code
(`billing.ts: SANDBOX_PRICES`): Pro `pri_01m4971kmb5pw7cw739j3rktr1`, Max
`pri_01m496kctf4c4jxgzfzncrktny`. Each price has custom data `plan` = `pro`
or `max`.

1. **Supabase → Authentication → Sign In / Providers → Email:** turn on
   *Allow new users to sign up*. Otherwise new emails are refused.
2. **Apply the migration:** `supabase db push`, or run `0008` in the SQL editor.
3. **Paddle sandbox → Developer tools → Authentication:**
   - make an **API key**: products, prices, customers, transactions and
     subscriptions (read and write);
   - make a **client-side token**.
4. **Paddle sandbox → Checkout → Checkout settings:** set the **default
   payment link** to `https://<website>/pay`, e.g.
   `https://heytora.co.za/pay`. Paddle builds checkout URLs from it. For
   live, Paddle must approve the domain.
5. **Deploy the webhook:** `supabase functions deploy paddle --no-verify-jwt`.
6. **Paddle sandbox → Developer tools → Notifications → New destination:**
   - URL `https://uizwfpxzzkokyrkvjzkr.supabase.co/functions/v1/paddle`
   - type: webhook
   - events: `subscription.created`, `subscription.updated`,
     `subscription.activated`, `subscription.canceled`,
     `subscription.past_due`, `subscription.paused`, `subscription.resumed`,
     `subscription.trialing`
   - copy its **secret key**.
7. **Supabase → Edge Functions → Secrets:**
   - `PADDLE_API_KEY`: the API key from step 3
   - `PADDLE_WEBHOOK_SECRET`: the secret from step 6
   - `PADDLE_ENV`: `sandbox`
8. **Redeploy the main function:** `supabase functions deploy heytora --use-api`.
9. **Website:** put the client-side token in `pay.paddle.clientToken`
   (`src/lib/site.ts`; it is public by design), then deploy the site.

**Test:**
1. Sign in to the app with a new email, then choose Pro.
2. Pay with Paddle's test card: `4242 4242 4242 4242`, any future date, any
   CVC.
3. Within a few seconds the app should show Pro.
4. Then try: Manage billing → cancel. The app should show "Pro until …".
5. Choose Max: it changes at once.
6. Delete account: the subscription is cancelled in Paddle.

The Supabase function logs and Paddle's notification log show every event
and how it was applied. The `billing_events` table keeps each one, with a
note when it wasn't applied.

## Going live

Repeat steps 3–9 in the **live** Paddle account. Then set:

- `PADDLE_ENV` = `production`
- `PADDLE_PRICES` = `pro=pri_<live pro id>,max=pri_<live max id>`.
  Live has no default prices: the code never sells sandbox prices live.
- the website's `pay.paddle.environment` = `"production"`, with the `live_`
  client token.

Paddle reviews the business before the live account can take payments. It
needs the registered company details on /terms (the `NEEDS` note in the
website's `site.ts`), the pricing page and the refund policy.
