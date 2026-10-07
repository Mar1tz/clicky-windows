// Referrals - the rules that need no database, tested on their own
// (supabase/tests/referrals.test.ts, under Node). The rest of the rules live
// in migration 0010, where a modified app can't reach them. docs/REFERRALS.md.

/** A code: up to six letters of the referrer's email name, four digits
 *  (migration 0010, _heytora_new_code) - DYLANH4821. */
export const CODE = /^[A-Z]{1,6}[0-9]{4}$/;

/** A PKCE challenge: base64url of a SHA-256, 43 characters. */
export const CHALLENGE = /^[A-Za-z0-9_-]{43}$/;

export function cleanCode(code: unknown): string {
  return String(code ?? "").trim().toUpperCase().replace(/\s+/g, "");
}

/** The challenge a PKCE verifier answers: proves the app claiming a
 *  referral is the one that started the sign-in the website noted. */
export async function challengeOf(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  let bin = "";
  for (const b of new Uint8Array(digest)) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The two routes the website calls without a sign-in answer it here. */
export function corsHeaders(origin: string | null, allowed: string[]): Record<string, string> {
  const ok = origin && allowed.some((a) => a === origin || (a.endsWith("*") && origin.startsWith(a.slice(0, -1))));
  return {
    "access-control-allow-origin": ok ? origin! : allowed[0] ?? "",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "authorization, apikey, content-type, x-client-info",
    "access-control-max-age": "86400",
    vary: "origin",
  };
}
