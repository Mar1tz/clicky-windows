// Paddle's webhooks: the only way an account's paid plan changes.
//
// Paddle calls this whenever a subscription starts, renews, changes plan,
// fails a payment, or is cancelled. Each call is checked against Paddle's
// signature first (PADDLE_WEBHOOK_SECRET) - this URL is public, and anyone
// can post to it - then applied by heytora_billing_event (migration 0008),
// which ignores repeats and anything older than what it already applied.
//
// Paddle wants a 2xx within seconds, and retries anything else; so an event
// that is not ours to apply (a transaction, a customer) is still a 200.
//
// Secrets: PADDLE_WEBHOOK_SECRET; optional PADDLE_ENV ("production" for the
// live account, else sandbox) and PADDLE_PRICES ("pro=pri_..,max=pri_..").

//
// It also puts a referrer's earned free month on their next bill
// (migration 0010): a one-off 100% discount, HEYTORA-REFERRAL-MONTH, made in
// Paddle once and reused; one month at a time, the next at the next renewal.
// That needs PADDLE_API_KEY (set already for the heytora function).
//
// Deploy:  supabase functions deploy paddle --no-verify-jwt
//          (Paddle has no Supabase sign-in; the signature is the check)
// Then, in Paddle > Developer tools > Notifications, a destination at
//          https://<project>.supabase.co/functions/v1/paddle
// with the subscription.* events.

import { createClient } from "npm:@supabase/supabase-js@2";
import { apiBase, paddleEnv, planUpdate, priceMap, verifySignature } from "../_shared/billing.ts";

const SECRET = Deno.env.get("PADDLE_WEBHOOK_SECRET") ?? "";
const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const ENV = paddleEnv(Deno.env.get("PADDLE_ENV"));
const PRICES = priceMap(Deno.env.get("PADDLE_PRICES"), ENV);
const API_KEY = Deno.env.get("PADDLE_API_KEY") ?? "";
const REWARD_CODE = "HEYTORA-REFERRAL-MONTH";

async function paddleApi(method: string, path: string, body?: unknown): Promise<Record<string, unknown>> {
  const r = await fetch(`${apiBase(ENV)}${path}`, {
    method,
    headers: { authorization: `Bearer ${API_KEY}`, "content-type": "application/json", "Paddle-Version": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({})) as { data?: unknown; error?: { code?: string; detail?: string } };
  if (!r.ok) throw new Error(`paddle ${method} ${path}: ${r.status} ${data.error?.code ?? ""} ${data.error?.detail ?? ""}`);
  return (data.data ?? {}) as Record<string, unknown>;
}

// The referral month's discount: found by its code, or made once.
let rewardDiscount = "";
async function rewardDiscountId(): Promise<string> {
  if (rewardDiscount) return rewardDiscount;
  const found = await paddleApi("GET", `/discounts?code=${REWARD_CODE}`) as unknown;
  const list = Array.isArray(found) ? found as { id?: string; status?: string }[] : [];
  const live = list.find((d) => d.status === "active");
  if (live?.id) return (rewardDiscount = live.id);
  const made = await paddleApi("POST", "/discounts", {
    amount: "100", type: "percentage", description: "HeyTora referral: a free month",
    code: REWARD_CODE, enabled_for_checkout: false, recur: false,
  });
  return (rewardDiscount = String(made.id ?? ""));
}

/** A free month waiting for this user goes on this subscription's next bill
 *  - unless a discount is already lined up, or the plan isn't renewing. */
async function applyRewardMonth(admin: ReturnType<typeof createClient>, user: string, sub: Record<string, unknown>) {
  if (!API_KEY || sub.status !== "active" || sub.discount || sub.scheduled_change) return;
  const { data: month, error } = await admin.rpc("heytora_reward_next", { p_uid: user });
  if (error || !month) return;
  const discount = await rewardDiscountId();
  if (!discount) return;
  await paddleApi("PATCH", `/subscriptions/${sub.id}`, {
    discount: { id: discount, effective_from: "next_billing_period" },
  });
  await admin.rpc("heytora_reward_used", { p_id: month, p_on: String(sub.id) });
  console.log("paddle: a referral month goes on the next bill of", sub.id);
}

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method !== "POST") return reply(405, { error: "POST only" });
  if (!SECRET || !SERVICE_KEY) return reply(500, { error: "the server is missing its secrets" });

  const raw = await req.text();                  // the exact bytes: the signature is over them
  if (!await verifySignature(SECRET, req.headers.get("Paddle-Signature"), raw, Date.now() / 1000)) {
    console.warn("paddle: a webhook with a bad or missing signature was refused");
    return reply(401, { error: "bad signature" });
  }
  let event: Record<string, unknown>;
  try {
    event = JSON.parse(raw);
  } catch {
    return reply(400, { error: "not JSON" });
  }

  const update = planUpdate(event, PRICES);
  if (!update) return reply(200, { ok: true, applied: false, note: "not a subscription event" });

  const admin = createClient(SUPABASE_URL, SERVICE_KEY, { auth: { persistSession: false } });
  const { data, error } = await admin.rpc("heytora_billing_event", {
    p_event_id: String(event.event_id ?? ""),
    p_type: String(event.event_type ?? ""),
    p_occurred_at: String(event.occurred_at ?? new Date().toISOString()),
    p_user: update.user,
    p_customer: update.customer,
    p_subscription: update.subscription,
    p_plan: update.plan,
    p_status: update.status,
    p_renews_at: update.renewsAt,
    p_cancel_at: update.cancelAt,
    p_payload: event,
    p_period_start: update.periodStart,
  });
  if (error) {
    // A 500 makes Paddle retry - right for a database hiccup.
    console.error("paddle: could not apply", event.event_id, error.message);
    return reply(500, { error: "could not apply" });
  }
  console.log("paddle:", event.event_type, update.subscription, JSON.stringify(data));
  const applied = data as { applied?: boolean; user_id?: string } | null;
  if (applied?.applied && applied.user_id) {
    try {
      await applyRewardMonth(admin, applied.user_id, (event.data ?? {}) as Record<string, unknown>);
    } catch (e) {
      // The plan is applied; the month waits for the next event.
      console.error("paddle: referral month", e);
    }
  }
  return reply(200, { ok: true, ...(data as Record<string, unknown>) });
});
