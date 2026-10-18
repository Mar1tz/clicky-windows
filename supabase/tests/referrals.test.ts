// The referral rules that need no database (functions/_shared/referrals.ts):
// node --test supabase/tests/referrals.test.ts
// tests/agents/test_e2e_referrals.py runs this file too.

import { test } from "node:test";
import assert from "node:assert/strict";
import { CHALLENGE, challengeOf, cleanCode, CODE, corsHeaders } from "../functions/_shared/referrals.ts";

test("a verifier's challenge is PKCE's own (RFC 7636's example)", async () => {
  const challenge = await challengeOf("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk");
  assert.equal(challenge, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  assert.ok(CHALLENGE.test(challenge));
});

test("codes are cleaned, and only real-looking ones are passed on", () => {
  assert.equal(cleanCode(" dylanh 4821 "), "DYLANH4821");
  for (const ok of ["DYLANH4821", "TORA0007", "A1234"]) assert.ok(CODE.test(ok), ok);
  for (const bad of ["", "DYLANHX4821", "DYLAN482", "1234", "DY-LA1234", "DYLAN4821'--"]) {
    assert.ok(!CODE.test(cleanCode(bad)), bad);
  }
});

test("only the website may call the two routes without a sign-in", () => {
  const sites = ["https://heytora.co.za", "http://localhost:3500"];
  assert.equal(corsHeaders("https://heytora.co.za", sites)["access-control-allow-origin"],
    "https://heytora.co.za");
  assert.equal(corsHeaders("http://localhost:3500", sites)["access-control-allow-origin"], "http://localhost:3500");
  assert.equal(corsHeaders("https://evil.example", sites)["access-control-allow-origin"],
    "https://heytora.co.za", "anyone else is told the site's own origin - the browser refuses them");
});
