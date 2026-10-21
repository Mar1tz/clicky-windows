// The paid-plan rules (functions/_shared/billing.ts), without a network:
// node --test supabase/tests/billing.test.ts
// tests/agents/test_e2e_billing.py runs this file too.

import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import {
  apiBase, creditGrant, hasLivePlan, LIVE_PRICES, paddleEnv, planOf, planUpdate, priceMap, SANDBOX_PRICES,
  packsFrom, verifySignature,
} from "../functions/_shared/billing.ts";

const SECRET = "pdl_ntfset_test_secret";
const sign = (ts: number, body: string, secret = SECRET) =>
  `ts=${ts};h1=${createHmac("sha256", secret).update(`${ts}:${body}`).digest("hex")}`;

test("a webhook signed by Paddle is accepted, anything else is not", async () => {
  const body = '{"event_type":"subscription.created"}';
  const now = 1_790_000_000;
  assert.equal(await verifySignature(SECRET, sign(now, body), body, now), true);
  assert.equal(await verifySignature(SECRET, sign(now, body), body + " ", now), false, "a changed body");
  assert.equal(await verifySignature(SECRET, sign(now, body, "someone else"), body, now), false);
  assert.equal(await verifySignature(SECRET, null, body, now), false, "no signature");
  assert.equal(await verifySignature("", sign(now, body), body, now), false, "no secret set: refuse");
  assert.equal(await verifySignature(SECRET, "ts=abc;h1=00", body, now), false);
});

test("a replayed webhook is refused once it is old", async () => {
  const body = "{}";
  const then = 1_790_000_000;
  assert.equal(await verifySignature(SECRET, sign(then, body), body, then + 299), true);
  assert.equal(await verifySignature(SECRET, sign(then, body), body, then + 301), false);
});

test("Paddle may send several signatures while a secret is rotated", async () => {
  const body = "{}";
  const now = 1_790_000_000;
  const old = sign(now, body, "old secret").split(";")[1];
  assert.equal(await verifySignature(SECRET, `${sign(now, body)};${old}`, body, now), true);
});

const PRICES = { pro: "pri_pro", max: "pri_max" };

function subscription(over: Record<string, unknown> = {}, data: Record<string, unknown> = {}) {
  return {
    event_id: "evt_1", event_type: "subscription.created", occurred_at: "2026-10-06T10:00:00Z",
    data: {
      id: "sub_1", status: "active", customer_id: "ctm_1",
      custom_data: { user_id: "u-1" },
      items: [{ price: { id: "pri_pro", custom_data: { plan: "pro" } } }],
      next_billed_at: "2026-11-06T10:00:00Z",
      current_billing_period: { starts_at: "2026-10-06T10:00:00Z", ends_at: "2026-11-06T10:00:00Z" },
      scheduled_change: null,
      ...data,
    },
    ...over,
  };
}

test("a new subscription is the plan its price says, for the user who checked out", () => {
  assert.deepEqual(planUpdate(subscription(), PRICES), {
    user: "u-1", customer: "ctm_1", subscription: "sub_1", plan: "pro", status: "active",
    renewsAt: "2026-11-06T10:00:00Z", cancelAt: null, periodStart: "2026-10-06T10:00:00Z",
  });
});

test("the plan comes from the price's custom data, else its id", () => {
  assert.equal(planOf([{ price: { id: "pri_other", custom_data: { plan: "max" } } }], PRICES), "max");
  assert.equal(planOf([{ price: { id: "pri_max" } }], PRICES), "max");
  assert.equal(planOf([{ price: { id: "pri_unknown", custom_data: { plan: "gold" } } }], PRICES), null,
    "not a plan we sell");
});

test("cancelling keeps the plan until the period ends, then it is cancelled", () => {
  const scheduled = planUpdate(subscription({ event_type: "subscription.updated" }, {
    scheduled_change: { action: "cancel", effective_at: "2026-11-06T10:00:00Z" },
  }), PRICES)!;
  assert.equal(scheduled.status, "active", "still paid for");
  assert.equal(scheduled.cancelAt, "2026-11-06T10:00:00Z");
  const ended = planUpdate(subscription({ event_type: "subscription.canceled" }, {
    status: "canceled", canceled_at: "2026-11-06T10:00:01Z",
  }), PRICES)!;
  assert.equal(ended.status, "canceled");
  assert.equal(ended.renewsAt, null);
  assert.equal(ended.periodStart, null, "ended: back to the calendar month");
  assert.equal(ended.cancelAt, "2026-11-06T10:00:01Z");
});

test("a failed payment and a pause are said as the database knows them", () => {
  assert.equal(planUpdate(subscription({}, { status: "past_due" }), PRICES)!.status, "past_due");
  assert.equal(planUpdate(subscription({}, { status: "paused" }), PRICES)!.status, "canceled");
});

test("only subscription events change a plan", () => {
  assert.equal(planUpdate({ event_type: "transaction.completed", data: { id: "txn_1" } }, PRICES), null);
  assert.equal(planUpdate(subscription({}, { status: "something new" }), PRICES), null);
});

test("each Paddle account sells its own prices, unless told otherwise", () => {
  assert.deepEqual(priceMap(undefined, "sandbox"), SANDBOX_PRICES);
  assert.deepEqual(priceMap(undefined, "production"), LIVE_PRICES);
  for (const id of Object.values(LIVE_PRICES)) {
    assert.ok(!Object.values(SANDBOX_PRICES).includes(id), "never a sandbox price live");
  }
  assert.deepEqual(priceMap("pro=pri_a, max = pri_b", "production"), { pro: "pri_a", max: "pri_b" });
  assert.equal(paddleEnv("Production"), "production");
  assert.equal(paddleEnv(undefined), "sandbox");
  assert.equal(apiBase("sandbox"), "https://sandbox-api.paddle.com");
});

test("a plan to change in place is a live paid subscription", () => {
  assert.equal(hasLivePlan({ plan: "pro", plan_status: "active", paddle_subscription_id: "sub_1" }), true);
  assert.equal(hasLivePlan({ plan: "pro", plan_status: "canceled", paddle_subscription_id: "sub_1" }), false);
  assert.equal(hasLivePlan({ plan: "pro", plan_status: "active", paddle_subscription_id: null }), false);
  assert.equal(hasLivePlan({ plan: "free", plan_status: "active", paddle_subscription_id: "sub_1" }), false);
});

test("a paid pack adds the credits its price carries, to the account the server named", () => {
  const paid = (items: unknown[], user: unknown = "u1", type = "transaction.completed") => ({
    event_type: type, data: { id: "txn_1", custom_data: { user_id: user }, items },
  });
  const pack = { price: { id: "pri_pack", custom_data: { credits: "675" } }, quantity: 1 };   // Paddle: strings
  assert.deepEqual(creditGrant(paid([pack])), { user: "u1", credits: 675, transaction: "txn_1" });
  assert.equal(creditGrant(paid([{ ...pack, quantity: 2 }]))?.credits, 1350, "two packs");
  // A renewal is a transaction too - its price carries no credits.
  assert.equal(creditGrant(paid([{ price: { id: "pri_pro", custom_data: { plan: "pro" } } }])), null);
  assert.equal(creditGrant(paid([pack], null)), null, "whose? nobody's: nothing");
  assert.equal(creditGrant(paid([pack], "u1", "transaction.paid")), null, "only once it's completed");
  assert.equal(creditGrant(paid([{ price: { custom_data: { credits: -5 } } }])), null);
});

test("the packs on sale are the product's active one-off prices with credits, smallest first", () => {
  const price = (id: string, credits: unknown, cents: string, extra: Record<string, unknown> = {}) => ({
    id, status: "active", billing_cycle: null, custom_data: { credits },
    unit_price: { amount: cents, currency_code: "USD" }, ...extra,
  });
  assert.deepEqual(packsFrom([
    price("pri_50", "3500", "5000"), price("pri_10", "675", "1000"), price("pri_25", "1750", "2500"),
    price("pri_old", "100", "200", { status: "archived" }),
    price("pri_sub", "900", "2000", { billing_cycle: { interval: "month", frequency: 1 } }),
    price("pri_none", undefined, "1000"),
  ]), [
    { price: "pri_10", credits: 675, cents: 1000, currency: "USD", label: "$10" },
    { price: "pri_25", credits: 1750, cents: 2500, currency: "USD", label: "$25" },
    { price: "pri_50", credits: 3500, cents: 5000, currency: "USD", label: "$50" },
  ]);
  assert.deepEqual(packsFrom(null), []);
  assert.equal(packsFrom([price("pri_x", "10", "999")])[0].label, "$9.99");
});
