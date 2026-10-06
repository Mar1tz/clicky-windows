// HeyTora's server function: the ONLY place the business Gemini key lives,
// and the only judge of what a plan allows. The app is easy to modify, so
// nothing it says about itself is trusted here:
//
//   POST /ticket       {kind: "talk"|"agent", new?}  a WINDOW ticket. A talk
//                      message is a talk window (Free 10 min, Pro/Max 30), an
//                      agent message an agent window (30 min). The open one
//                      is handed back at no charge; `new` starts the next.
//   POST /voice-pass   {kind: "talk"|"dictation"|"wake", model, new?}  a Gemini Live
//                      ephemeral token - one session, locked to that model,
//                      ending with its talk window (or after the plan's
//                      dictation minutes; a wake-word check: one minute).
//                      Talk passes come with the window's ticket. With
//                      {standby: true} (Pro/Max keeping the voice open for
//                      the next question) the pass lives in an uncounted
//                      "voice" window and comes with no ticket (0007).
//                      The app then talks straight to Google.
//   POST /agent-ticket the agent window's ticket (older apps).
//   GET  /me           the plan, its allowances and this month's use.
//   DELETE /account    {confirm: email}  close the account and delete its
//                      data (refused while a paid plan is active).
//   POST /omniroute/v1/chat/completions   the AGENTS' model call: OmniRoute
//                      (Claude), the only models the agents use (the owner,
//                      5 Oct 2026). Needs the agent message's ticket; only
//                      the Claude models; counted and costed like the rest.
//   GET  /omniroute/v1/models             those models.
//   *    /v1beta/...   a Gemini REST call, forwarded with the key added:
//                      the app's model families only; answers (generate)
//                      need a window ticket; every call is counted, and its
//                      real cost - Google's token counts - is recorded
//                      against the plan's hidden monthly ceiling.
//
// The rules are in migration 0004 (heytora_window / heytora_pass /
// heytora_consume) and rules.ts. Secrets: GEMINI_API_KEY,
// HEYTORA_TICKET_SECRET. Optional: HEYTORA_TALK_MODELS,
// HEYTORA_DICTATION_MODELS, HEYTORA_PRICES.
//
// Deploy:  supabase functions deploy heytora --use-api   (JWT verification on)

import { createClient } from "npm:@supabase/supabase-js@2";
import {
  checkGenerate, costMicro, estimateTokens, family, makeTicket, MAX_BODY_BYTES, MAX_OUTPUT_TOKENS,
  omnirouteModels, prices, readTicket, UsageReader,
} from "./rules.ts";

const GOOGLE = "https://generativelanguage.googleapis.com";
const KEY = Deno.env.get("GEMINI_API_KEY") ?? "";
const TICKET_SECRET = Deno.env.get("HEYTORA_TICKET_SECRET") ?? "";
const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const list = (name: string, fallback: string) =>
  new Set((Deno.env.get(name) ?? fallback).split(",").map((m) => m.trim()).filter(Boolean));
const VOICE_MODELS: Record<string, Set<string>> = {
  talk: list("HEYTORA_TALK_MODELS", "gemini-3.8-live"),
  dictation: list("HEYTORA_DICTATION_MODELS", "gemini-3.5-transcribe-live"),
};
VOICE_MODELS.wake = VOICE_MODELS.dictation; // "is that 'Tora'?" - the dictation model, briefly
const PRICES = prices(Deno.env.get("HEYTORA_PRICES"));
// The agents' gateway (secrets: OMNIROUTE_API_KEY, OMNIROUTE_BASE_URL - the
// OpenAI-compatible root, e.g. https://example.com/v1).
const OMNIROUTE_KEY = Deno.env.get("OMNIROUTE_API_KEY") ?? "";
const OMNIROUTE_URL = (Deno.env.get("OMNIROUTE_BASE_URL") ?? "").replace(/\/+$/, "");
const OMNIROUTE_MODELS = omnirouteModels(Deno.env.get("HEYTORA_OMNIROUTE_MODELS"));

// Only these Gemini REST paths: model calls and the model list. No files,
// no tuning, no caches - nothing that stores anything on the business account.
const REST_PATH =
  /^v1(beta|alpha)?\/models(\/([A-Za-z0-9._-]+)(:(generateContent|streamGenerateContent|batchEmbedContents|embedContent|countTokens))?)?$/;

// Recording what a call cost runs after the answer has gone back.
declare const EdgeRuntime: { waitUntil(p: Promise<unknown>): void } | undefined;
function later(p: Promise<unknown>): void {
  const done = p.catch((e) => console.error("heytora: metering", e));
  try {
    EdgeRuntime?.waitUntil(done);
  } catch {
    // not in the edge runtime: the promise runs on regardless
  }
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

// Errors look like Google's ({"error": {...}}), so the app's existing error
// handling reads them; "status" says which of HeyTora's own they are.
function refuse(status: number, code: string, message: string, heytora: unknown = undefined) {
  return json(status, { error: { code: status, status: code, message, heytora } });
}

const WORDS: Record<string, string> = {
  talk: "talk messages", dictation: "dictations", agent: "agent messages", calls: "requests",
  wake: "wake-word checks",
};
const own = "switch to your own Gemini key in Settings";

function limitResponse(info: Record<string, unknown>): Response {
  const kind = WORDS[String(info.kind)] ?? "allowance";
  const plan = String(info.plan ?? "free").replace(/^./, (c) => c.toUpperCase());
  const paid = plan !== "Free";
  switch (info.reason) {
    case "rate":
      return refuse(429, "HEYTORA_LIMIT", "That's a lot at once - give it a minute and try again.", info);
    case "day":
      return refuse(402, "HEYTORA_LIMIT",
        `That's today's fair-use limit for ${kind} on the ${plan} plan - it resets at midnight (UTC). ` +
          `Or ${own}.`, info);
    case "budget":
      return refuse(402, "HEYTORA_LIMIT", paid
        ? `You've reached this month's fair-use limit on the ${plan} plan. It resets on the 1st - or ${own}.`
        : `You've used this month's free allowance. Upgrade on the HeyTora website, or ${own}.`, info);
    case "passes":
    case "window":
      return refuse(402, "HEYTORA_LIMIT", "That conversation has ended - ask again.", info);
    case "not_in_plan":
      return refuse(402, "HEYTORA_LIMIT", `Your ${plan} plan doesn't include ${kind}.`, info);
    default:
      return refuse(402, "HEYTORA_LIMIT",
        `You've used all ${info.limit} ${kind} on the ${plan} plan this month. ` +
          `Upgrade on the HeyTora website, or ${own}.`, info);
  }
}

// -- voice passes (Gemini Live ephemeral tokens) ------------------------------------------------------------

// Google's docs give more than one shape for creating one; the one that
// answers is kept. The model is locked in with fieldMask "model" - without
// the mask Google ignores the lock (checked 1 Oct 2026).
let passShape = 0;
const PASS_SHAPES: Array<{ version: string; path: string; wrap: boolean }> = [
  { version: "v1beta", path: "auth_tokens", wrap: false },
  { version: "v1alpha", path: "auth_tokens", wrap: false },
  { version: "v1alpha", path: "auth_tokens", wrap: true },
];

async function mintPass(model: string, endsAt: number): Promise<{ token: string; url: string }> {
  const now = Date.now();
  const ends = Math.max(now + 60_000, Math.min(endsAt * 1000, now + 120 * 60_000));
  const fields = {
    uses: 1, // one session
    expireTime: new Date(ends).toISOString(), // Google closes the session then
    newSessionExpireTime: new Date(Math.min(now + 2 * 60_000, ends)).toISOString(),
    bidiGenerateContentSetup: { model: `models/${model}` },
    fieldMask: "model",
  };
  let lastError = "";
  for (let i = 0; i < PASS_SHAPES.length; i++) {
    const shape = PASS_SHAPES[(passShape + i) % PASS_SHAPES.length];
    const r = await fetch(`${GOOGLE}/${shape.version}/${shape.path}`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-goog-api-key": KEY },
      body: JSON.stringify(shape.wrap ? { authToken: fields } : fields),
    });
    const text = await r.text();
    if (r.ok) {
      const token = JSON.parse(text)?.name;
      if (token) {
        passShape = (passShape + i) % PASS_SHAPES.length;
        return {
          token,
          url: `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.${shape.version}` +
            ".GenerativeService.BidiGenerateContentConstrained",
        };
      }
    }
    lastError = `${r.status} ${text.slice(0, 300)}`;
    if (r.status !== 400 && r.status !== 404) break; // only a wrong shape is worth another try
  }
  throw new Error(`Google refused the voice pass: ${lastError}`);
}

// -- the function -------------------------------------------------------------------------------------------

Deno.serve(async (req: Request): Promise<Response> => {
  if (!KEY || !TICKET_SECRET || !SERVICE_KEY) {
    return refuse(500, "HEYTORA_SETUP", "The server is missing its secrets.");
  }

  const url = new URL(req.url);
  const path = url.pathname.replace(/^.*?\/heytora\/?/, "");
  const auth = req.headers.get("Authorization") ?? "";
  const jwt = auth.replace(/^Bearer\s+/i, "");

  // Who is asking - with their own token, so the database's rules apply.
  const db = createClient(SUPABASE_URL, ANON_KEY, { global: { headers: { Authorization: auth } } });
  const { data: who } = await db.auth.getUser(jwt);
  const user = who?.user;
  if (!user) return refuse(401, "HEYTORA_SIGNIN", "Sign in to HeyTora to keep talking.");

  const rpc = async (fn: string, args: Record<string, unknown>) => {
    const { data, error } = await db.rpc(fn, args);
    if (error) throw new Error(`${fn}: ${error.message}`);
    return data as Record<string, unknown>;
  };
  // `now` rides along with every end time: the app measures what is left by
  // the server's clock, so a PC whose clock is off is never misled.
  const ticketFor = async (kind: string, w: Record<string, unknown>) => ({
    ticket: await makeTicket(TICKET_SECRET, user.id, {
      kind, window: String(w.window), expires: Number(w.expires_at),
    }),
    expires_at: Number(w.expires_at),
    now: Date.now() / 1000,
  });

  try {
    if (path === "me" && req.method === "GET") {
      return json(200, await rpc("get_usage_summary", {}));
    }

    if (path === "account" && req.method === "DELETE") {
      // Closing the account, from the app. The email typed to confirm is
      // checked here too, so nothing but a deliberate request deletes.
      const body = await req.json().catch(() => ({}));
      const typed = String(body?.confirm ?? "").trim().toLowerCase();
      if (!typed || typed !== String(user.email ?? "").trim().toLowerCase()) {
        return refuse(400, "HEYTORA_CONFIRM", "Type the email you sign in with to confirm.");
      }
      const admin = createClient(SUPABASE_URL, SERVICE_KEY, { auth: { persistSession: false } });
      // A paid plan is cancelled first, on the website - never deleted out
      // from under a subscription that would go on billing.
      const { data: profile } = await admin.from("profiles").select("plan, plan_status")
        .eq("id", user.id).maybeSingle();
      if (profile && profile.plan !== "free" && ["active", "trialing", "past_due"].includes(profile.plan_status)) {
        return refuse(409, "HEYTORA_PAID",
          "Cancel your plan on the HeyTora website first, then delete the account.",
          { reason: "paid", plan: profile.plan });
      }
      // Every table of theirs references auth.users on delete cascade
      // (0001, 0002, 0004): the profile, usage, costs and agent history go too.
      const { error } = await admin.auth.admin.deleteUser(user.id);
      if (error) throw new Error(`delete account: ${error.message}`);
      console.log("heytora: account deleted", user.id);
      return json(200, { deleted: true });
    }

    if ((path === "ticket" || path === "agent-ticket") && req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      const kind = path === "agent-ticket" ? "agent" : body?.kind === "agent" ? "agent" : "talk";
      const w = await rpc("heytora_window", { p_kind: kind, p_new: body?.new === true });
      if (!w.ok) return limitResponse(w);
      return json(200, { ...(await ticketFor(kind, w)), kind, usage: w });
    }

    if (path === "voice-pass" && req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      const kind = body?.kind === "dictation" || body?.kind === "wake" ? String(body.kind) : "talk";
      const model = String(body?.model ?? "");
      if (!VOICE_MODELS[kind].has(model)) {
        return refuse(400, "HEYTORA_MODEL", `Not a ${kind} model: ${model}`);
      }
      if (kind !== "talk") {
        // A dictation, or a wake-word check: one pass each, counted apart.
        const usage = await rpc("heytora_consume", { p_kind: kind, p_quantity: 1, p_meta: { model } });
        if (!usage.ok) return limitResponse(usage);
        const minutes = Number((usage.limits as Record<string, unknown>)?.[`${kind}_minutes`] ??
          (kind === "wake" ? 1 : 10));
        const endsAt = Date.now() / 1000 + Math.min(Math.max(minutes, 1), 120) * 60;
        const pass = await mintPass(model, endsAt);
        delete usage.limits;
        return json(200, { ...pass, expires_at: endsAt, now: Date.now() / 1000, usage });
      }
      // Talk: the pass lives inside a talk window - a new one unless the app
      // says it is the same question (a reconnect) - and ends with it. A
      // standby pass (the voice waiting for a question) lives in a "voice"
      // window instead: not a talk message, and no ticket for answers.
      const standby = body?.standby === true;
      const w = await rpc("heytora_window", standby
        ? { p_kind: "voice", p_new: body?.new === true }
        : { p_kind: "talk", p_new: body?.new !== false });
      if (!w.ok) return limitResponse(w);
      const p = await rpc("heytora_pass", { p_window: Number(w.window), p_model: model });
      if (!p.ok) return limitResponse({ ...p, plan: w.plan });
      const pass = await mintPass(model, Number(p.expires_at));
      if (standby) {
        return json(200, {
          ...pass, expires_at: Number(p.expires_at), now: Date.now() / 1000, standby: true, usage: w,
        });
      }
      const t = await ticketFor("talk", w);
      return json(200, {
        ...pass, expires_at: Number(p.expires_at), ticket: t.ticket, ticket_expires_at: t.expires_at,
        now: t.now, usage: w,
      });
    }

    if (path === "omniroute/v1/models" && req.method === "GET") {
      return json(200, { object: "list", data: [...OMNIROUTE_MODELS].map((id) => ({ id, object: "model" })) });
    }

    if (path === "omniroute/v1/chat/completions" && req.method === "POST") {
      if (!OMNIROUTE_KEY || !OMNIROUTE_URL) {
        return refuse(503, "HEYTORA_SETUP", "HeyTora's agents are not switched on yet.");
      }
      // An agent message the plan has paid for - its ticket, nothing else.
      const t = await readTicket(TICKET_SECRET, user.id, req.headers.get("x-heytora-ticket"), Date.now() / 1000);
      if (!t || t.kind !== "agent") {
        return refuse(402, "HEYTORA_TICKET", "That agent job's time is up - ask again.",
          { reason: "ticket", kind: "agent" });
      }
      const raw = await req.arrayBuffer();
      if (raw.byteLength > MAX_BODY_BYTES) {
        return refuse(413, "HEYTORA_REQUEST", "That request is too large to forward.");
      }
      let body: Record<string, unknown>;
      try {
        body = JSON.parse(new TextDecoder().decode(raw));
      } catch {
        return refuse(400, "HEYTORA_REQUEST", "Not a model request.");
      }
      const model = String(body?.model ?? "");
      if (!OMNIROUTE_MODELS.has(model)) {
        return refuse(403, "HEYTORA_MODEL", `HeyTora's agents don't use ${model}.`);
      }
      const asked = Number(body.max_tokens);
      body.max_tokens = Number.isFinite(asked) && asked > 0 ? Math.min(asked, MAX_OUTPUT_TOKENS) : MAX_OUTPUT_TOKENS;
      body.stream = true;                                 // and the usage, at the end
      body.stream_options = { include_usage: true };
      const usage = await rpc("heytora_consume", {
        p_kind: "calls", p_quantity: 1, p_meta: { kind: "agent", model, via: "omniroute", window: t.window },
      });
      if (!usage.ok) {
        return limitResponse(usage.reason === "limit" ? { ...usage, reason: "budget" } : usage);
      }
      const upstream = await fetch(`${OMNIROUTE_URL}/chat/completions`, {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${OMNIROUTE_KEY}` },
        body: JSON.stringify(body),
      });
      const type = upstream.headers.get("content-type") ?? "application/json";
      if (!upstream.ok || !upstream.body) {
        const text = await upstream.text();
        console.error("heytora: omniroute", upstream.status, text.slice(0, 300));
        return refuse(upstream.status >= 500 ? 502 : upstream.status, "HEYTORA_UPSTREAM",
          `The agents' model said no (${upstream.status}).`, { detail: text.slice(0, 300) });
      }
      const admin = createClient(SUPABASE_URL, SERVICE_KEY, { auth: { persistSession: false } });
      const [toApp, toMeter] = upstream.body.tee();
      later((async () => {
        const reader = new UsageReader();
        const decoder = new TextDecoder();
        for await (const chunk of toMeter) reader.push(decoder.decode(chunk, { stream: true }));
        const used = reader.used();
        if (!used) return;
        const { error } = await admin.rpc("heytora_record_cost", {
          p_user: user.id, p_kind: "agent", p_model: model, p_in: used.input, p_out: used.output,
          p_micro: costMicro(PRICES, "claude", used.input, used.output),
        });
        if (error) throw new Error(error.message);
      })());
      return new Response(toApp, { status: upstream.status, headers: { "content-type": type } });
    }

    // Everything else: forward a Gemini REST call with the business key.
    const route = path.match(REST_PATH);
    if (!route) return refuse(404, "HEYTORA_PATH", `Not something HeyTora forwards: ${path}`);
    const model = route[3] ?? "";
    const method = route[5] ?? "";
    const fam = model ? family(model) : "list";
    if (!fam) return refuse(403, "HEYTORA_MODEL", `HeyTora doesn't forward ${model}.`);

    // Answers need a window: a talk message or an agent message the plan
    // has paid for. Its kind - not the app's label - is what is recorded.
    let kind = "talk";
    let ticketId = "";
    const answers = method === "generateContent" || method === "streamGenerateContent";
    if (answers) {
      const t = await readTicket(TICKET_SECRET, user.id, req.headers.get("x-heytora-ticket"), Date.now() / 1000);
      if (!t) {
        return refuse(402, "HEYTORA_TICKET", "That conversation has ended - ask again.",
          { reason: "ticket", kind: req.headers.get("x-heytora-kind") ?? "talk" });
      }
      kind = t.kind;
      ticketId = t.window;
    }

    let payload: ArrayBuffer | string | undefined;
    if (req.method !== "GET" && req.method !== "HEAD") {
      const raw = await req.arrayBuffer();
      if (raw.byteLength > MAX_BODY_BYTES) {
        return refuse(413, "HEYTORA_REQUEST", "That request is too large to forward.");
      }
      payload = raw;
      if (answers) {
        let parsed: unknown;
        try {
          parsed = JSON.parse(new TextDecoder().decode(raw));
        } catch {
          return refuse(400, "HEYTORA_REQUEST", "Not a model request.");
        }
        const checked = checkGenerate(parsed);
        if (!checked.ok) return refuse(checked.status, checked.code, checked.message);
        payload = JSON.stringify(checked.body);
      }
    }

    const usage = await rpc("heytora_consume", {
      p_kind: "calls", p_quantity: 1, p_meta: { kind, model, method, window: ticketId || undefined },
    });
    // The request count and the cost ceiling are the server's own: either
    // one running out reads as the month's allowance being used.
    if (!usage.ok) {
      return limitResponse(usage.reason === "limit" ? { ...usage, reason: "budget" } : usage);
    }

    const query = new URLSearchParams(url.search);
    query.delete("key"); // never a key from the client
    const qs = query.toString();
    const upstream = await fetch(`${GOOGLE}/${path}${qs ? `?${qs}` : ""}`, {
      method: req.method,
      headers: { "content-type": req.headers.get("content-type") ?? "application/json", "x-goog-api-key": KEY },
      body: payload,
    });
    const type = upstream.headers.get("content-type") ?? "application/json";

    // What it cost: service role, after the answer is on its way.
    const admin = createClient(SUPABASE_URL, SERVICE_KEY, { auth: { persistSession: false } });
    const record = async (input: number, output: number) => {
      const micro = costMicro(PRICES, fam, input, output);
      if (!micro && !input && !output) return;
      const { error } = await admin.rpc("heytora_record_cost", {
        p_user: user.id, p_kind: kind, p_model: model, p_in: input, p_out: output, p_micro: micro,
      });
      if (error) throw new Error(error.message);
    };

    if (!model) {
      // The model list: only what HeyTora forwards, so the app offers nothing else.
      const data = await upstream.json().catch(() => ({}));
      if (Array.isArray(data?.models)) {
        data.models = data.models.filter((m: { name?: string }) => family(String(m?.name ?? "")));
      }
      return json(upstream.status, data);
    }
    if (!upstream.ok || !upstream.body) {
      return new Response(upstream.body, { status: upstream.status, headers: { "content-type": type } });
    }
    if (method === "embedContent" || method === "batchEmbedContents") {
      const bytes = typeof payload === "string" ? payload.length : (payload?.byteLength ?? 0);
      later(record(estimateTokens(bytes), 0));
      return new Response(upstream.body, { status: upstream.status, headers: { "content-type": type } });
    }
    if (method === "streamGenerateContent") {
      // Streamed straight through, so the answer starts at once; a copy is
      // read alongside for the token counts in its chunks.
      const [toApp, toMeter] = upstream.body.tee();
      later((async () => {
        const reader = new UsageReader();
        const decoder = new TextDecoder();
        for await (const chunk of toMeter) reader.push(decoder.decode(chunk, { stream: true }));
        const used = reader.used();
        if (used) await record(used.input, used.output);
      })());
      return new Response(toApp, { status: upstream.status, headers: { "content-type": type } });
    }
    // A plain answer (generateContent, countTokens): read whole, counted, passed on.
    const text = await upstream.text();
    if (answers) {
      const reader = new UsageReader();
      reader.push(text);
      const used = reader.used();
      if (used) later(record(used.input, used.output));
    }
    return new Response(text, { status: upstream.status, headers: { "content-type": type } });
  } catch (e) {
    console.error("heytora:", e);
    return refuse(502, "HEYTORA_SERVER", "HeyTora's server had a problem - try again.");
  }
});
