// The heytora function's rules (functions/heytora/rules.ts), without a
// network: node --test supabase/tests/heytora_rules.test.ts
// (Node 24 runs TypeScript as is.) tests/agents/test_e2e_heytora_metering.py
// runs this file too.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  checkGenerate, costMicro, DEFAULT_PRICES, estimateTokens, family, makeTicket, MAX_OUTPUT_TOKENS,
  omnirouteModels, prices, readTicket, UsageReader,
} from "../functions/heytora/rules.ts";

test("only the app's model families are forwarded", () => {
  assert.equal(family("gemini-3.8-flash"), "flash");
  assert.equal(family("models/gemini-3.1-pro-preview"), "pro");
  assert.equal(family("gemini-3.1-flash-lite"), "flash-lite");
  assert.equal(family("gemini-3.1-flash-tts-preview"), "tts");
  assert.equal(family("gemini-embedding-001"), "embedding");
  for (const never of ["gemini-2.5-flash-image", "veo-3", "imagen-4", "gemini-3.8-live",
    "gemini-2.5-flash-native-audio", "gemini-3-ultra", "gemini-2.5-computer-use-preview",
    "gemma-3-27b", "claude-x", "gemini-3.8-flash/../files", ""]) {
    assert.equal(family(never), null, never);
  }
});

test("cost is tokens times the price per million, in millionths of a dollar", () => {
  // 1,000 in + 200 out on flash: 1000*0.5 + 200*3 = 1,100 micro-dollars
  assert.equal(costMicro(DEFAULT_PRICES, "flash", 1000, 200), 1100);
  assert.equal(costMicro(DEFAULT_PRICES, "pro", 1_000_000, 0), 2_500_000);
  assert.equal(costMicro(DEFAULT_PRICES, "unknown", 1, 0), 3, "unknown is priced as pro");
  assert.equal(prices('{"flash": {"input": 1, "output": 1}}').flash.input, 1);
  assert.equal(prices("not json").flash.input, DEFAULT_PRICES.flash.input);
  assert.equal(estimateTokens(4000), 1000);
});

test("a request is made safe or refused", () => {
  const ok = checkGenerate({ contents: [{ parts: [{ text: "hi" }] }], generationConfig: { maxOutputTokens: 999999 } });
  assert.ok(ok.ok && (ok.body.generationConfig as { maxOutputTokens: number }).maxOutputTokens === MAX_OUTPUT_TOKENS);
  const unset = checkGenerate({ contents: [] });
  assert.ok(unset.ok && (unset.body.generationConfig as { maxOutputTokens: number }).maxOutputTokens === MAX_OUTPUT_TOKENS);
  const small = checkGenerate({ contents: [], generationConfig: { maxOutputTokens: 512, temperature: 0.2 } });
  assert.ok(small.ok && (small.body.generationConfig as { maxOutputTokens: number }).maxOutputTokens === 512);
  assert.ok(checkGenerate({ tools: [{ functionDeclarations: [{ name: "point" }] }] }).ok, "the app's own tools");
  for (const bad of [
    { tools: [{ googleSearch: {} }] },
    { tools: [{ functionDeclarations: [], codeExecution: {} }] },
    { tools: [{ urlContext: {} }] },
    { cachedContent: "cachedContents/x" },
    { contents: [{ parts: [{ fileData: { fileUri: "https://youtube.com/x" } }] }] },
    [], null, "text",
  ]) {
    assert.equal(checkGenerate(bad).ok, false, JSON.stringify(bad));
  }
});

test("a ticket is one user's, one window's, and ends", async () => {
  const now = 1_800_000_000;
  const t = await makeTicket("s3cret", "user-1", { kind: "talk", window: "42", expires: now + 600 });
  assert.match(t, /^talk\.42\.\d+\.[A-Za-z0-9_-]+$/);
  assert.deepEqual(await readTicket("s3cret", "user-1", t, now), { kind: "talk", window: "42", expires: now + 600 });
  assert.equal(await readTicket("s3cret", "user-2", t, now), null, "someone else's");
  assert.equal(await readTicket("s3cret", "user-1", t, now + 601), null, "over");
  assert.equal(await readTicket("other", "user-1", t, now), null, "wrong secret");
  const stretched = t.replace(`.${now + 600}.`, `.${now + 99999}.`);
  assert.equal(await readTicket("s3cret", "user-1", stretched, now), null, "cannot be stretched");
  const agent = t.replace(/^talk/, "agent");
  assert.equal(await readTicket("s3cret", "user-1", agent, now), null, "cannot change kind");
  for (const junk of [null, "", "a.b.c", "calls.1.2.x", `talk.x.${now + 5}.sig`]) {
    assert.equal(await readTicket("s3cret", "user-1", junk, now), null, String(junk));
  }
});

test("usage is read from Google's own counts, plain or streamed", () => {
  const plain = new UsageReader();
  plain.push(JSON.stringify({
    candidates: [{ content: { parts: [{ text: "ok" }] } }],
    usageMetadata: { promptTokenCount: 1200, candidatesTokenCount: 30, thoughtsTokenCount: 400,
      totalTokenCount: 1630, promptTokensDetails: [{ modality: "TEXT", tokenCount: 1200 }] },
  }, null, 2));
  assert.deepEqual(plain.used(), { input: 1200, output: 430 });

  // A stream: running totals in each chunk, split anywhere.
  const sse = [1, 2, 3].map((i) =>
    `data: ${JSON.stringify({ usageMetadata: { promptTokenCount: 900, candidatesTokenCount: i * 10 } })}\r\n\r\n`).join("");
  const streamed = new UsageReader();
  for (let i = 0; i < sse.length; i += 7) streamed.push(sse.slice(i, i + 7));
  assert.deepEqual(streamed.used(), { input: 900, output: 30 });

  assert.equal(new UsageReader().used(), null, "no counts, no guess");
});

test("OmniRoute (OpenAI-style) usage is read from the last chunk of the stream", () => {
  const sse = [
    `data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n`,
    `data: {"choices":[],"usage":{"prompt_tokens":1450,"completion_tokens":212,"prompt_tokens_details":{"cached_tokens":3}}}\n\n`,
    "data: [DONE]\n\n",
  ].join("");
  const reader = new UsageReader();
  for (let i = 0; i < sse.length; i += 11) reader.push(sse.slice(i, i + 11));
  assert.deepEqual(reader.used(), { input: 1450, output: 212 });
  // 1,450 in + 212 out of Claude Sonnet: 1450*3 + 212*15 = 7,530 micro-dollars
  assert.equal(costMicro(DEFAULT_PRICES, "claude", 1450, 212), 7530);
});

test("the agents may only reach the Claude models on OmniRoute", () => {
  const allowed = omnirouteModels(undefined);
  assert.ok(allowed.has("claude/claude-sonnet-4-6") && allowed.has("cc/claude-sonnet-4-6"));
  assert.equal(allowed.has("gpt-5"), false);
  assert.deepEqual([...omnirouteModels(" a/x , b/y ")], ["a/x", "b/y"]);
});

// -- AC-02: the sign-in from the verified token -------------------------------------------------------------------

import { alertBody, billingProblem, signedInUser } from "../functions/heytora/rules.ts";

test("only a signed-in user's own, current token is a user", () => {
  const iss = "https://proj.supabase.co/auth/v1";
  const now = 1_800_000_000;
  const user = { role: "authenticated", sub: "u-1", aud: "authenticated", iss, exp: now + 600, email: "amy@example.com" };
  assert.deepEqual(signedInUser(user, iss, now), { id: "u-1", email: "amy@example.com" });
  assert.equal(signedInUser({ ...user, role: "anon", sub: undefined }, iss, now), null, "the anon key");
  assert.equal(signedInUser({ ...user, role: "service_role" }, iss, now), null, "a service key");
  assert.equal(signedInUser({ ...user, iss: "https://other.supabase.co/auth/v1" }, iss, now), null);
  assert.equal(signedInUser({ ...user, exp: now - 1 }, iss, now), null, "past its time");
  assert.equal(signedInUser({ ...user, aud: ["authenticated", "x"] }, iss, now)?.id, "u-1");
  assert.equal(signedInUser({ ...user, aud: "anon" }, iss, now), null);
});

// -- FX-06: billing refusals reach the owners --------------------------------------------------------------------

test("a billing refusal is told apart from an ordinary one", () => {
  assert.ok(billingProblem(429, '{"error":{"message":"Your prepayment credits are depleted. Please go to AI Studio"}}'));
  assert.ok(billingProblem(403, "Billing is not enabled for this project"));
  assert.ok(!billingProblem(429, "Resource has been exhausted (e.g. check quota)."), "a rate limit is not billing");
  assert.ok(!billingProblem(500, "prepayment credits are depleted"), "only refusals");
  assert.ok(!billingProblem(400, "Invalid JSON payload"));
  const body = alertBody("a voice pass", 429, "Your prepayment credits are depleted");
  assert.ok(body.text.includes("billing") && body.text === body.content);
});
