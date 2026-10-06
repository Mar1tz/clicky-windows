// HeyTora's paid plans through Paddle - the rules, with no network, so they
// are tested on their own (supabase/tests/billing.test.ts, under Node).
//
// Paddle is the merchant of record: the payment, the tax and the receipts
// are Paddle's. HeyTora only turns what Paddle says about a subscription into
// the account's plan (migration 0008, heytora_billing_event) and asks Paddle
// for a checkout, a change of plan, the billing portal or a cancellation
// (supabase/functions/heytora). Which price is which plan comes from the
// price's own custom data ({"plan": "pro"}), else from PADDLE_PRICES.

export type Env = "sandbox" | "production";

// The prices, per Paddle account (created 6 Oct 2026). Price ids aren't
// secret. PADDLE_PRICES ("pro=pri_..,max=pri_..") overrides them - for a
// new price, until it is written in here.
// (Checked against Paddle's sandbox API, 6 Oct 2026.)
export const SANDBOX_PRICES: Record<string, string> = {
  pro: "pri_01m49bc41gge68ge4hf8mgbd6p",
  max: "pri_01m49bddnrepm7dn5xvrzdrbv0",
};
export const LIVE_PRICES: Record<string, string> = {
  pro: "pri_01m4971kmb5pw7cw739j3rktr1",
  max: "pri_01m496kctf4c4jxgzfzncrktny",
};
export const PLANS = ["pro", "max"] as const;
export const SIGNATURE_TOLERANCE_S = 300;

export function apiBase(env: Env): string {
  return env === "production" ? "https://api.paddle.com" : "https://sandbox-api.paddle.com";
}

export function paddleEnv(value: string | undefined): Env {
  return (value ?? "").trim().toLowerCase() === "production" ? "production" : "sandbox";
}

/** {plan: price id}, from "pro=pri_a,max=pri_b"; else the account's own. */
export function priceMap(value: string | undefined, env: Env): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of (value ?? "").split(",")) {
    const [plan, price] = part.split("=").map((s) => s.trim());
    if (plan && price) out[plan.toLowerCase()] = price;
  }
  if (Object.keys(out).length) return out;
  return { ...(env === "production" ? LIVE_PRICES : SANDBOX_PRICES) };
}

// -- the webhook's signature ----------------------------------------------------------------------------

function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function same(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Paddle-Signature is "ts=<seconds>;h1=<hex HMAC-SHA256 of `${ts}:${body}`>".
 *  The body must be the raw bytes Paddle sent, before any parsing. An old
 *  timestamp is refused too: a replayed webhook is not a new payment. */
export async function verifySignature(secret: string, header: string | null, rawBody: string,
                                      nowS: number, toleranceS = SIGNATURE_TOLERANCE_S): Promise<boolean> {
  if (!secret || !header) return false;
  let ts = "";
  const sigs: string[] = [];
  for (const part of header.split(";")) {
    const [k, v] = part.split("=", 2).map((s) => (s ?? "").trim());
    if (k === "ts") ts = v;
    else if (k === "h1" && v) sigs.push(v.toLowerCase());
  }
  if (!/^\d+$/.test(ts) || !sigs.length) return false;
  if (Math.abs(nowS - Number(ts)) > toleranceS) return false;
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${ts}:${rawBody}`)));
  return sigs.some((s) => same(s, mac));
}

// -- what an event means for the account ---------------------------------------------------------------

export type PlanUpdate = {
  user: string | null;          // our user id, from the checkout's custom_data
  customer: string | null;      // Paddle's ctm_...
  subscription: string;         // Paddle's sub_...
  plan: string | null;          // "pro" | "max", null when no item is one of ours
  status: "active" | "trialing" | "past_due" | "canceled";
  renewsAt: string | null;      // when it next charges
  cancelAt: string | null;      // when a cancelled plan ends (or ended)
  periodStart: string | null;   // when the current billing period began: the allowance counts from it
};

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj => (v && typeof v === "object" ? v as Obj : {});
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** The plan a subscription's items are, by the price's custom data or id. */
export function planOf(items: unknown, prices: Record<string, string>): string | null {
  const byId = Object.fromEntries(Object.entries(prices).map(([plan, id]) => [id, plan]));
  for (const item of Array.isArray(items) ? items : []) {
    const price = obj(obj(item).price);
    const named = str(obj(price.custom_data).plan)?.toLowerCase();
    if (named && (PLANS as readonly string[]).includes(named)) return named;
    const id = str(price.id) ?? str(obj(item).price_id);
    if (id && byId[id]) return byId[id];
  }
  return null;
}

const STATUS: Record<string, PlanUpdate["status"]> = {
  active: "active", trialing: "trialing", past_due: "past_due", canceled: "canceled", paused: "canceled",
};

/** A subscription.* event as the change it makes - null for anything else
 *  (transactions, customers: the subscription events carry the plan). */
export function planUpdate(event: Obj, prices: Record<string, string>): PlanUpdate | null {
  const type = str(event.event_type) ?? "";
  if (!type.startsWith("subscription.")) return null;
  const sub = obj(event.data);
  const id = str(sub.id);
  const status = STATUS[str(sub.status) ?? ""];
  if (!id || !status) return null;
  const change = obj(sub.scheduled_change);
  const cancelAt = str(change.action) === "cancel"
    ? str(change.effective_at)
    : status === "canceled" ? (str(sub.canceled_at) ?? str(event.occurred_at)) : null;
  return {
    user: str(obj(sub.custom_data).user_id),
    customer: str(sub.customer_id),
    subscription: id,
    plan: planOf(sub.items, prices),
    status,
    renewsAt: status === "canceled" ? null
      : str(sub.next_billed_at) ?? str(obj(sub.current_billing_period).ends_at),
    cancelAt,
    periodStart: status === "canceled" ? null : str(obj(sub.current_billing_period).starts_at),
  };
}

/** Is there a subscription to change, rather than a new one to buy? */
export function hasLivePlan(profile: Obj): boolean {
  return Boolean(str(profile.paddle_subscription_id))
    && ["active", "trialing", "past_due"].includes(str(profile.plan_status) ?? "")
    && str(profile.plan) !== "free";
}
