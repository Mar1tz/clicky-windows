"""M15: the direct Gemini provider.

Driven through httpx.MockTransport with the generateContent response
shapes Gemini actually returns. Two things matter most: the key travels
in a header (never the URL, which is how it reached tora.log), and the
id-based tool contract survives an API that has no ids.
"""
from __future__ import annotations

import json

import httpx
import pytest

from agents.core import ProviderMessage, TaskKind, ToolCall, ToolSpec
from agents.providers import GeminiAgentProvider, MockProvider, ProviderError, build_router
from agents.providers.gemini import api_key_from_env


def gemini(handler, **kw) -> GeminiAgentProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiAgentProvider("AIza-test-key", client=client, **kw)


def text_response(text, reason="STOP", **meta):
    return httpx.Response(200, json={
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]},
                        "finishReason": reason}],
        "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4,
                          "totalTokenCount": 15, **meta},
        "modelVersion": "gemini-3.8-flash-001",
    })


def call_response(name, args, text=""):
    parts = ([{"text": text}] if text else []) + [{"functionCall": {"name": name, "args": args}}]
    return httpx.Response(200, json={
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 9, "totalTokenCount": 39},
    })


# -- the secret stays out of the URL -----------------------------------------

async def test_key_is_a_header_never_a_query_param():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["header"] = request.headers.get("x-goog-api-key")
        return text_response("hi")

    await gemini(handler).complete([ProviderMessage("user", "x")])
    assert seen["header"] == "AIza-test-key"
    assert "key=" not in seen["url"], "a query-string key is what ends up in the httpx log"
    assert seen["url"].endswith("/models/gemini-3.8-flash:generateContent")


# -- decoding ----------------------------------------------------------------

async def test_text_reply_with_usage():
    r = await gemini(lambda req: text_response("Hello there")).complete(
        [ProviderMessage("user", "hi")])
    assert r.text == "Hello there" and r.finish_reason == "stop"
    assert r.usage == {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}
    assert r.model == "gemini-3.8-flash"           # the route we called


async def test_function_call_becomes_a_tool_call_with_a_minted_id():
    r = await gemini(lambda req: call_response("create_folder", {"path": "MyApp"},
                                               text="On it.")).complete(
        [ProviderMessage("user", "make it")])
    assert r.finish_reason == "tool_calls"
    assert r.text == "On it."
    assert len(r.tool_calls) == 1
    call = r.tool_calls[0]
    assert call.tool_name == "create_folder" and call.arguments == {"path": "MyApp"}
    assert call.id, "Gemini gives no id; we must mint one so the registry can match results"


async def test_max_tokens_maps_to_length():
    r = await gemini(lambda req: text_response("trunc", reason="MAX_TOKENS")).complete(
        [ProviderMessage("user", "x")])
    assert r.finish_reason == "length"


async def test_blocked_prompt_is_an_error_not_an_empty_success():
    def handler(request):
        return httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(ProviderError) as e:
        await gemini(handler).complete([ProviderMessage("user", "x")])
    assert "SAFETY" in str(e.value)


# -- encoding ----------------------------------------------------------------

def test_encode_messages_maps_roles_and_answers_calls_by_name():
    call = ToolCall("create_folder", {"path": "MyApp"}, id="c1")
    msgs = [
        ProviderMessage("system", "ignored here; goes to systemInstruction"),
        ProviderMessage("user", "make MyApp"),
        ProviderMessage("assistant", "doing it", tool_calls=[call]),
        ProviderMessage("tool", '{"ok": true, "path": "C:/MyApp"}', tool_call_id="c1"),
        ProviderMessage("tool", "plain text result", tool_call_id="c1"),
    ]
    out = GeminiAgentProvider.encode_messages(msgs)
    assert [c["role"] for c in out] == ["user", "model", "user", "user"]
    model_turn = out[1]
    assert model_turn["parts"][0] == {"text": "doing it"}
    assert model_turn["parts"][1] == {"functionCall": {"name": "create_folder",
                                                       "args": {"path": "MyApp"}}}
    fr = out[2]["parts"][0]["functionResponse"]
    assert fr["name"] == "create_folder", "the response must be matched by NAME for Gemini"
    assert fr["response"] == {"ok": True, "path": "C:/MyApp"}
    assert out[3]["parts"][0]["functionResponse"]["response"] == {"result": "plain text result"}


def test_encode_message_builds_inline_image_parts():
    out = GeminiAgentProvider.encode_message(
        ProviderMessage("user", "what is this?", images_b64=["data:image/jpeg;base64,QUJD"]), {})
    assert out["parts"][0] == {"text": "what is this?"}
    assert out["parts"][1] == {"inlineData": {"mimeType": "image/jpeg", "data": "QUJD"}}


def test_tool_schema_is_scrubbed_of_unsupported_keywords():
    spec = ToolSpec("t", "d", {"type": "object", "additionalProperties": False,
                               "properties": {"path": {"type": "string", "pattern": "^.+$",
                                                       "title": "Path"}},
                               "required": ["path"]})
    out = GeminiAgentProvider.encode_tool(spec)
    params = out["parameters"]
    assert "additionalProperties" not in params
    assert "pattern" not in params["properties"]["path"]
    assert "title" not in params["properties"]["path"]
    assert params["required"] == ["path"]


async def test_request_body_shape():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return text_response("ok")

    spec = ToolSpec("read_file", "Read", {"type": "object", "properties": {"path": {"type": "string"}}})
    await gemini(handler).complete([ProviderMessage("user", "hi")], tools=[spec],
                                   system="be brief", temperature=0.0, max_tokens=77)
    body = seen["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "read_file"
    assert body["generationConfig"] == {"temperature": 0.0, "maxOutputTokens": 77}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]


# -- health / fall-through ---------------------------------------------------

async def test_falls_through_to_the_next_model():
    seen = []

    def handler(request):
        model = request.url.path.split("/models/")[1].split(":")[0]
        seen.append(model)
        if model == "gone-model":
            return httpx.Response(404, json={"error": {"message": "not found"}})
        return text_response("recovered")

    p = gemini(handler, model="gone-model", fallback_models=["gemini-3.8-flash"])
    r = await p.complete([ProviderMessage("user", "x")])
    assert r.text == "recovered" and r.model == "gemini-3.8-flash"
    assert seen == ["gone-model", "gemini-3.8-flash"]


async def test_no_key_is_a_clear_error():
    p = GeminiAgentProvider("", client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: text_response("x"))))
    p.api_key = ""
    with pytest.raises(ProviderError) as e:
        await p.complete([ProviderMessage("user", "x")])
    assert "no Gemini API key" in str(e.value)


async def test_health_check():
    assert await gemini(lambda r: httpx.Response(200, json={"models": []})).health_check() is True
    assert await gemini(lambda r: httpx.Response(403, json={})).health_check() is False


# -- wiring: zero-setup default ----------------------------------------------

def test_key_lookup_order(monkeypatch):
    for n in ("TORA_GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    assert api_key_from_env() == ""
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert api_key_from_env() == "g"
    monkeypatch.setenv("GOOGLE_API_KEY", "goog")
    assert api_key_from_env() == "goog"
    monkeypatch.setenv("TORA_GEMINI_API_KEY", "tora")
    assert api_key_from_env() == "tora"


def test_google_key_alone_makes_agents_think(monkeypatch):
    """The app already requires this key. With nothing else configured the
    router must still pick a real provider, not the mock."""
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-x")
    router = build_router()
    picked = router.pick(TaskKind.CODING)
    assert isinstance(picked, GeminiAgentProvider)
    assert isinstance(router.pick(TaskKind.FAST), GeminiAgentProvider)


def test_a_gateway_outranks_gemini(monkeypatch):
    from agents.providers import OpenAICompatibleProvider
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-x")
    monkeypatch.setenv("TORA_OMNIROUTE_BASE_URL", "https://gw.test/v1")
    router = build_router()
    assert isinstance(router.pick(TaskKind.CODING), OpenAICompatibleProvider)
    assert any(isinstance(p, GeminiAgentProvider) for p in router.providers())


def test_offline_flag_suppresses_gemini_too(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-x")
    router = build_router(gateway=False)
    assert all(isinstance(p, MockProvider) for p in router.providers())


# -- live: needs Google credits ---------------------------------------------

@pytest.mark.live
async def test_live_gemini_calls_a_tool():
    from dotenv import load_dotenv
    load_dotenv()
    p = GeminiAgentProvider()
    if not p.api_key:
        pytest.skip("no Google key")
    try:
        spec = ToolSpec("create_folder", "Create a folder.",
                        {"type": "object", "properties": {"path": {"type": "string"}},
                         "required": ["path"]})
        try:
            r = await p.complete([ProviderMessage("user", "Create a folder called MyApp. Use the tool.")],
                                 tools=[spec], system="Use tools to act.")
        except ProviderError as e:
            if any(code in str(e) for code in ("429", "402", "403", "credits", "quota")):
                pytest.skip(f"Gemini unavailable: {str(e)[:100]}")
            raise
        assert r.finish_reason == "tool_calls"
        assert r.tool_calls[0].tool_name == "create_folder"
    finally:
        await p.aclose()
