// HeyTora's server function: the ONLY place the business Gemini key lives.
//
// The desktop app signs in to the user's HeyTora account (Supabase Auth) and
// sends its Gemini requests here instead of to Google. This function checks
// who they are, spends their plan (public.heytora_consume, migration 0003),
// and then either
//
//   POST /voice-pass    mints a short-lived voice pass (a Gemini Live
//                       "ephemeral token": one session, minutes long) so the
//                       app talks - or dictates - straight to Google, as fast
//                       as with a key, and nothing to steal;
//   POST /agent-ticket  issues an agent ticket - one agent message on the
//                       plan, good for 30 minutes of agent work;
//   GET  /me            the plan, its limits and this month's usage;
//   *    /v1beta/...    forwards a Gemini REST call (generateContent, the
//                       streamed kind, embeddings, the model list) with the
//                       key added here.
//
// Secrets (supabase secrets set ...):
//   GEMINI_API_KEY          the business key
//   HEYTORA_TICKET_SECRET   any long random string; signs agent tickets
// Optional:
//   HEYTORA_LIVE_MODELS     comma list of models a voice pass may open
//
// Deploy:  supabase functions deploy heytora
// (JWT verification stays ON: every request must carry a signed-in user.)

import { createClient } from "npm:@supabase/supabase-js@2";

const GOOGLE = "https://generativelanguage.googleapis.com";
const KEY = Deno.env.get("GEMINI_API_KEY") ?? "";
const TICKET_SECRET = Deno.env.get("HEYTORA_TICKET_SECRET") ?? "";
const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const LIVE_MODELS = new Set(
  (Deno.env.get("HEYTORA_LIVE_MODELS") ?? "gemini-3.8-live,gemini-3.5-transcribe-live")
    .split(",").map((m) => m.trim()).filter(Boolean),
);
const TICKET_MINUTES = 30;

// Only these Gemini REST paths may be forwarded: model calls and the model
// list. No files, no tuning, no caches - nothing that stores anything on the
// business account.
const REST_PATH =
  /^v1(beta|alpha)?\/models(\/[A-Za-z0-9._-]+(:(generateContent|streamGenerateContent|batchEmbedContents|embedContent|countTokens))?)?$/;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// Errors look like Google's ({"error": {...}}), so the app's existing error
// handling reads them; "status" says which of HeyTora's own they are.
function refuse(status: number, code: string, message: string, heytora: unknown = undefined) {
  return json(status, { error: { code: status, status: code, message, heytora } });
}

const WORDS: Record<string, string> = {
  talk: "talk messages", dictation: "dictations", agent: "agent messages", calls: "requests",
};

function limitResponse(info: Record<string, unknown>): Response {
  if (info.reason === "rate") {
    return refuse(429, "HEYTORA_LIMIT", "That's a lot at once - give it a minute and try again.", info);
  }
  const kind = WORDS[String(info.kind)] ?? "allowance";
  const plan = String(info.plan ?? "").replace(/^./, (c) => c.toUpperCase());
  const message = info.reason === "not_in_plan"
    ? `Your ${plan} plan doesn't include ${kind}.`
    : `You've used all ${info.limit} ${kind} on the ${plan} plan this month. ` +
      "Upgrade on the HeyTora website, or switch to your own Gemini key in Settings.";
  return refuse(402, "HEYTORA_LIMIT", message, info);
}

// -- agent tickets: HMAC-signed, so no table is needed to check one ----------------------------------------

async function hmac(text: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(TICKET_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(text)));
  return btoa(String.fromCharCode(...sig)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function makeTicket(uid: string): Promise<{ ticket: string; expires: number }> {
  const expires = Math.floor(Date.now() / 1000) + TICKET_MINUTES * 60;
  const id = crypto.randomUUID();
  return { ticket: `${id}.${expires}.${await hmac(`${uid}.${id}.${expires}`)}`, expires };
}

async function ticketOk(uid: string, ticket: string | null): Promise<boolean> {
  if (!ticket) return false;
  const [id, expires, sig] = ticket.split(".");
  if (!id || !expires || !sig || Number(expires) < Date.now() / 1000) return false;
  return sig === await hmac(`${uid}.${id}.${expires}`);
}

// -- voice passes (Gemini Live ephemeral tokens) ------------------------------------------------------------

// Google's docs give two shapes for creating one; whichever answers is kept.
let passShape = 0;
const PASS_SHAPES: Array<{ version: string; path: string; wrap: boolean }> = [
  { version: "v1beta", path: "auth_tokens", wrap: false },
  { version: "v1alpha", path: "auth_tokens", wrap: false },
  { version: "v1alpha", path: "auth_tokens", wrap: true },
  { version: "v1beta", path: "authTokens:create", wrap: true },
];

async function mintPass(): Promise<{ token: string; url: string; expires: string }> {
  const now = Date.now();
  const fields = {
    uses: 1,                                                    // one session
    expireTime: new Date(now + 30 * 60_000).toISOString(),      // the session may run 30 min
    newSessionExpireTime: new Date(now + 2 * 60_000).toISOString(), // must open within 2 min
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
          expires: fields.expireTime,
          url: `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.${shape.version}` +
            ".GenerativeService.BidiGenerateContentConstrained",
        };
      }
    }
    lastError = `${r.status} ${text.slice(0, 300)}`;
    if (r.status !== 400 && r.status !== 404) break;            // only a wrong shape is worth another try
  }
  throw new Error(`Google refused the voice pass: ${lastError}`);
}

// -- the function -------------------------------------------------------------------------------------------

Deno.serve(async (req: Request): Promise<Response> => {
  if (!KEY || !TICKET_SECRET) return refuse(500, "HEYTORA_SETUP", "The server is missing its secrets.");

  const url = new URL(req.url);
  const path = url.pathname.replace(/^.*?\/heytora\/?/, "");
  const auth = req.headers.get("Authorization") ?? "";
  const jwt = auth.replace(/^Bearer\s+/i, "");

  // Who is asking - with their own token, so the database's rules apply.
  const db = createClient(SUPABASE_URL, ANON_KEY, { global: { headers: { Authorization: auth } } });
  const { data: who } = await db.auth.getUser(jwt);
  const user = who?.user;
  if (!user) return refuse(401, "HEYTORA_SIGNIN", "Sign in to HeyTora to keep talking.");

  const consume = async (kind: string, meta: Record<string, unknown> = {}) => {
    const { data, error } = await db.rpc("heytora_consume", { p_kind: kind, p_quantity: 1, p_meta: meta });
    if (error) throw new Error(error.message);
    return data as Record<string, unknown>;
  };

  try {
    if (path === "me" && req.method === "GET") {
      const { data, error } = await db.rpc("get_usage_summary");
      if (error) throw new Error(error.message);
      return json(200, data);
    }

    if (path === "voice-pass" && req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      const kind = body?.kind === "dictation" ? "dictation" : "talk";
      const model = String(body?.model ?? "");
      if (!LIVE_MODELS.has(model)) return refuse(400, "HEYTORA_MODEL", `Not a voice model: ${model}`);
      const usage = await consume(kind, { model });
      if (!usage.ok) return limitResponse(usage);
      const pass = await mintPass();
      return json(200, { token: pass.token, url: pass.url, expires_at: pass.expires, usage });
    }

    if (path === "agent-ticket" && req.method === "POST") {
      const usage = await consume("agent");
      if (!usage.ok) return limitResponse(usage);
      const { ticket, expires } = await makeTicket(user.id);
      return json(200, { ticket, expires_at: expires, usage });
    }

    // Everything else: forward a Gemini REST call with the business key.
    if (!REST_PATH.test(path)) return refuse(404, "HEYTORA_PATH", `Not something HeyTora forwards: ${path}`);
    const kind = req.headers.get("x-heytora-kind") ?? "talk";
    const ticket = req.headers.get("x-heytora-ticket");
    if (kind === "agent" && !(await ticketOk(user.id, ticket))) {
      return refuse(402, "HEYTORA_LIMIT", "Your agent ticket has expired - ask again.",
        { reason: "ticket", kind: "agent" });
    }
    const usage = await consume("calls", { kind, path: path.slice(0, 120), ticket: ticket?.split(".")[0] });
    if (!usage.ok) return limitResponse(usage);

    const query = new URLSearchParams(url.search);
    query.delete("key");                                  // never a key from the client
    const qs = query.toString();
    const upstream = await fetch(`${GOOGLE}/${path}${qs ? `?${qs}` : ""}`, {
      method: req.method,
      headers: {
        "content-type": req.headers.get("content-type") ?? "application/json",
        "x-goog-api-key": KEY,
      },
      body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer(),
    });
    // Streamed straight through, so a streamed answer starts at once.
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch (e) {
    console.error("heytora:", e);
    return refuse(502, "HEYTORA_SERVER", "HeyTora's server had a problem - try again.");
  }
});
