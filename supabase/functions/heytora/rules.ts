// HeyTora's server rules that need no network - kept apart from index.ts so
// they can be tested on their own (rules.test.ts, run by node --test):
//
//   * which models may be forwarded, and what they cost;
//   * what a forwarded request may contain;
//   * how a window ticket is made and checked;
//   * how much a call used, read from Google's own token counts.
//
// Plain TypeScript (no Deno APIs): the same file runs in Supabase's edge
// runtime and in Node.

export type Price = { input: number; output: number }; // US dollars per million tokens

// The model families the app uses, priced on the HIGH side of Google's list
// so the monthly cost ceilings err towards stopping early. The HEYTORA_PRICES
// secret (JSON, same shape) replaces any entry without a redeploy of the app.
export const DEFAULT_PRICES: Record<string, Price> = {
  "pro": { input: 2.5, output: 15 },
  "flash": { input: 0.5, output: 3 },
  "flash-lite": { input: 0.15, output: 0.6 },
  "tts": { input: 1, output: 20 },
  "embedding": { input: 0.15, output: 0 },
  // The agents' models through OmniRoute: Claude Sonnet, at Anthropic's list
  // price (what OmniRoute bills is no more than that, as far as we know).
  "claude": { input: 3, output: 15 },
};

// The only models the agents may reach through OmniRoute (the owner, 5 Oct
// 2026: "only use OmniRoute for the agents", Claude). HEYTORA_OMNIROUTE_MODELS
// (comma list) replaces it without a release.
export const OMNIROUTE_MODELS = ["claude/claude-sonnet-4-6", "cc/claude-sonnet-4-6", "agy/claude-sonnet-4-6"];

export function omnirouteModels(list: string | undefined): Set<string> {
  const names = (list ?? "").split(",").map((m) => m.trim()).filter(Boolean);
  return new Set(names.length ? names : OMNIROUTE_MODELS);
}

// Never forwarded, whatever their family: billed per image / second / search,
// or only reachable as Live (which goes by voice pass).
const NEVER = /(image|veo|imagen|live|native-audio|computer-use|deep-research|robotics|ultra|lyria|gemma)/;

/** The price family of a model, or null if HeyTora does not forward it. */
export function family(model: string): string | null {
  const m = model.replace(/^models\//, "").toLowerCase();
  if (!/^[a-z0-9._-]+$/.test(m)) return null;
  if (m.startsWith("gemini-embedding") || m.startsWith("text-embedding")) return "embedding";
  if (!m.startsWith("gemini-")) return null;
  if (m.includes("-tts")) return "tts";
  if (NEVER.test(m)) return null;
  if (m.includes("flash-lite")) return "flash-lite";
  if (m.includes("flash")) return "flash";
  if (m.includes("-pro")) return "pro";
  return null;
}

export function prices(json: string | undefined): Record<string, Price> {
  if (!json) return DEFAULT_PRICES;
  try {
    return { ...DEFAULT_PRICES, ...JSON.parse(json) };
  } catch {
    return DEFAULT_PRICES;
  }
}

/** Cost in millionths of a dollar: tokens x dollars-per-million. */
export function costMicro(table: Record<string, Price>, fam: string, input: number, output: number): number {
  const p = table[fam] ?? DEFAULT_PRICES.pro;
  return Math.ceil(Math.max(0, input) * p.input + Math.max(0, output) * p.output);
}

// -- what a request may contain ---------------------------------------------------------------------------

export const MAX_BODY_BYTES = 20 * 1024 * 1024; // a few screenshots; never a video
export const MAX_OUTPUT_TOKENS = 16384;

export type Checked =
  | { ok: true; body: Record<string, unknown> }
  | { ok: false; status: number; code: string; message: string };

/** A generateContent / streamGenerateContent body: refused, or made safe. */
export function checkGenerate(body: unknown): Checked {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return { ok: false, status: 400, code: "HEYTORA_REQUEST", message: "Not a model request." };
  }
  const b = { ...(body as Record<string, unknown>) };
  // Cached contents are stored (and billed) on the business account.
  if ("cachedContent" in b) {
    return { ok: false, status: 400, code: "HEYTORA_REQUEST", message: "Cached contents are not forwarded." };
  }
  // Only the app's own tools: function declarations. Search grounding, URL
  // context, code execution, maps... are billed on their own or reach out.
  for (const tool of (Array.isArray(b.tools) ? b.tools : [])) {
    const keys = Object.keys((tool ?? {}) as Record<string, unknown>);
    if (keys.some((k) => k !== "functionDeclarations")) {
      return { ok: false, status: 400, code: "HEYTORA_REQUEST", message: `Tool not forwarded: ${keys.join(", ")}` };
    }
  }
  // Files by address (uploads, videos on the web) are not forwarded either.
  for (const content of (Array.isArray(b.contents) ? b.contents : [])) {
    for (const part of (((content ?? {}) as Record<string, unknown>).parts as unknown[] ?? [])) {
      if (part && typeof part === "object" && "fileData" in (part as object)) {
        return { ok: false, status: 400, code: "HEYTORA_REQUEST", message: "Files by address are not forwarded." };
      }
    }
  }
  // An answer is never longer than this.
  const gen = { ...((b.generationConfig ?? {}) as Record<string, unknown>) };
  const asked = Number(gen.maxOutputTokens);
  gen.maxOutputTokens = Number.isFinite(asked) && asked > 0 ? Math.min(asked, MAX_OUTPUT_TOKENS) : MAX_OUTPUT_TOKENS;
  b.generationConfig = gen;
  return { ok: true, body: b };
}

// -- window tickets -----------------------------------------------------------------------------------------
//
// kind.window.expires.signature - the signature binds them to one user, so a
// ticket is worth nothing to anyone else and cannot be stretched.

async function hmac(secret: string, text: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(text)));
  let s = "";
  for (const byte of sig) s += String.fromCharCode(byte);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function sameText(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export type Ticket = { kind: string; window: string; expires: number };

export async function makeTicket(secret: string, uid: string, t: Ticket): Promise<string> {
  const expires = Math.floor(t.expires);
  return `${t.kind}.${t.window}.${expires}.${await hmac(secret, `${uid}.${t.kind}.${t.window}.${expires}`)}`;
}

export async function readTicket(
  secret: string, uid: string, ticket: string | null, nowSeconds: number,
): Promise<Ticket | null> {
  if (!ticket || !secret) return null;
  const parts = ticket.split(".");
  if (parts.length !== 4) return null;
  const [kind, window, expires, sig] = parts;
  if (!["talk", "agent"].includes(kind) || !/^\d+$/.test(window) || !/^\d+$/.test(expires)) return null;
  if (Number(expires) < nowSeconds) return null;
  const want = await hmac(secret, `${uid}.${kind}.${window}.${expires}`);
  return sameText(sig, want) ? { kind, window, expires: Number(expires) } : null;
}

// -- what a call used -----------------------------------------------------------------------------------------
//
// Google puts the running totals in usageMetadata - once in a plain answer,
// in every chunk of a streamed one - so the largest of each count is the
// call's total. Reading key by key works for both, line by line.

export type Used = { input: number; output: number };

const COUNTS: Array<[RegExp, keyof Used]> = [
  // OpenAI-style (OmniRoute): in the last chunk of a stream, or the body
  [/"prompt_tokens"\s*:\s*(\d+)/g, "input"],
  [/"completion_tokens"\s*:\s*(\d+)/g, "output"],
  // Gemini
  [/"promptTokenCount"\s*:\s*(\d+)/g, "input"],
  [/"toolUsePromptTokenCount"\s*:\s*(\d+)/g, "input"],
  [/"candidatesTokenCount"\s*:\s*(\d+)/g, "output"],
  [/"thoughtsTokenCount"\s*:\s*(\d+)/g, "output"],
];

export class UsageReader {
  private best: Record<string, number> = {};
  private tail = "";

  /** Feed text as it arrives (any chunking). */
  push(text: string): void {
    const lines = (this.tail + text).split("\n");
    this.tail = lines.pop() ?? "";
    // A single line can be huge (spoken audio); keep only what a count could still need.
    if (this.tail.length > 1_000_000) this.tail = this.tail.slice(-4096);
    for (const line of lines) this.scan(line);
  }

  used(): Used | null {
    if (this.tail) {
      this.scan(this.tail);
      this.tail = "";
    }
    if (!Object.keys(this.best).length) return null;
    const out: Used = { input: 0, output: 0 };
    for (const [re, side] of COUNTS) out[side] += this.best[re.source] ?? 0;
    return out;
  }

  private scan(line: string): void {
    if (!line.includes("TokenCount") && !line.includes("_tokens")) return;   // Gemini's, or OpenAI's
    for (const [re] of COUNTS) {
      for (const m of line.matchAll(re)) {
        const n = Number(m[1]);
        if (n > (this.best[re.source] ?? -1)) this.best[re.source] = n;
      }
    }
  }
}

/** Embeddings report no usage: about four characters a token. */
export function estimateTokens(bytes: number): number {
  return Math.ceil(Math.max(0, bytes) / 4);
}
