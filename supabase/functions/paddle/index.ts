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
// Deploy:  supabase functions deploy paddle --no-verify-jwt
//          (Paddle has no Supabase sign-in; the signature is the check)
// Then, in Paddle > Developer tools > Notifications, a destination at
//          https://<project>.supabase.co/functions/v1/paddle
// with the subscription.* events.

import { createClient } from "npm:@supabase/supabase-js@2";
import { paddleEnv, planUpdate, priceMap, verifySignature } from "../_shared/billing.ts";

const SECRET = Deno.env.get("PADDLE_WEBHOOK_SECRET") ?? "";
const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const PRICES = priceMap(Deno.env.get("PADDLE_PRICES"), paddleEnv(Deno.env.get("PADDLE_ENV")));

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
  return reply(200, { ok: true, ...(data as Record<string, unknown>) });
});
