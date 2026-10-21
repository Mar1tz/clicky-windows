"""M13: the OpenAI-compatible provider.

Driven through `httpx.MockTransport`, so the SSE reassembly, the tool-call
fragment handling and the health fall-through are all exercised against
real HTTP machinery without a network. The shapes here are copied from
what a live gateway actually returned, including the awkward parts:
arguments split mid-JSON across chunks, and a body that streams although
nothing asked it to.
"""
from __future__ import annotations

import json

import httpx
import pytest

from agents.core import ProviderMessage, ToolCall, ToolSpec
from agents.providers import GATEWAY_MODELS, OpenAICompatibleProvider, ProviderError


def sse(*events: dict) -> bytes:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    return (body + "data: [DONE]\n\n").encode()


def chunk(**delta) -> dict:
    return {"id": "x", "object": "chat.completion.chunk", "model": "claude/claude-sonnet-4-6",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}


def final(reason="stop", **extra) -> dict:
    return {"id": "x", "model": "claude/claude-sonnet-4-6",
            "choices": [{"index": 0, "delta": {}, "finish_reason": reason}], **extra}


def provider(handler, **kw) -> OpenAICompatibleProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kw.setdefault("model", "claude/claude-sonnet-4-6")
    return OpenAICompatibleProvider("https://gw.test/v1", "sk-test", client=client, **kw)


def stream_response(*events, status=200):
    return httpx.Response(status, content=sse(*events),
                          headers={"content-type": "text/event-stream"})


# -- SSE reassembly ----------------------------------------------------------

async def test_streams_text_even_when_not_asked():
    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return stream_response(chunk(role="assistant"), chunk(content="Hello "),
                               chunk(content="world"), final())
    r = await provider(handler).complete([ProviderMessage("user", "hi")])
    assert r.text == "Hello world"
    assert r.finish_reason == "stop"
    assert r.tool_calls == []
    assert r.model == "claude/claude-sonnet-4-6"


async def test_tool_call_arguments_split_across_chunks():
    """The shape a real gateway sends: the name arrives once, the JSON
    arguments dribble in across several fragments."""
    def handler(request):
        return stream_response(
            chunk(role="assistant"),
            chunk(tool_calls=[{"index": 0, "id": "call_1",
                               "function": {"name": "create_folder", "arguments": ""}}]),
            chunk(tool_calls=[{"index": 0, "function": {"arguments": '{"pa'}}]),
            chunk(tool_calls=[{"index": 0, "function": {"arguments": 'th": "My'}}]),
            chunk(tool_calls=[{"index": 0, "function": {"arguments": 'App"}'}}]),
            final("tool_calls"),
        )
    r = await provider(handler).complete([ProviderMessage("user", "make it")])
    assert r.finish_reason == "tool_calls"
    assert len(r.tool_calls) == 1
    call = r.tool_calls[0]
    assert call.tool_name == "create_folder"
    assert call.arguments == {"path": "MyApp"}
    assert call.id == "call_1"


async def test_parallel_tool_calls_keep_their_slots():
    def handler(request):
        return stream_response(
            chunk(tool_calls=[{"index": 0, "id": "a",
                               "function": {"name": "create_folder", "arguments": '{"path":"A"}'}}]),
            chunk(tool_calls=[{"index": 1, "id": "b",
                               "function": {"name": "create_file", "arguments": '{"path":"B"}'}}]),
            final("tool_calls"),
        )
    r = await provider(handler).complete([ProviderMessage("user", "two things")])
    assert [c.tool_name for c in r.tool_calls] == ["create_folder", "create_file"]
    assert [c.arguments["path"] for c in r.tool_calls] == ["A", "B"]


async def test_malformed_arguments_are_kept_not_dropped():
    """Bad JSON must reach the registry, which rejects it with a message
    the model can act on. Silently dropping it looks like a no-op."""
    def handler(request):
        return stream_response(
            chunk(tool_calls=[{"index": 0, "id": "c",
                               "function": {"name": "create_folder",
                                            "arguments": '{"path": "MyApp'}}]),
            final("tool_calls"))
    r = await provider(handler).complete([ProviderMessage("user", "x")])
    assert r.tool_calls[0].arguments == {"_raw": '{"path": "MyApp'}


async def test_keepalive_and_junk_frames_are_ignored():
    def handler(request):
        body = (b": keep-alive\n\n"
                + b"data: not json\n\n"
                + sse(chunk(content="ok"), final()))
        return httpx.Response(200, content=body,
                              headers={"content-type": "text/event-stream"})
    r = await provider(handler).complete([ProviderMessage("user", "x")])
    assert r.text == "ok"


async def test_plain_json_body_also_works():
    """Not every endpoint streams; a compliant one returns JSON."""
    def handler(request):
        return httpx.Response(200, json={
            "model": "gpt-x",
            "choices": [{"message": {"content": "done", "tool_calls": [
                {"id": "t1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'}}]},
                "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        })
    r = await provider(handler).complete([ProviderMessage("user", "x")])
    assert r.text == "done" and r.finish_reason == "tool_calls"
    assert r.tool_calls[0].arguments == {"path": "a.txt"}
    assert r.usage == {"prompt_tokens": 10, "completion_tokens": 3}


async def test_usage_is_captured_from_the_stream():
    def handler(request):
        return stream_response(chunk(content="hi"),
                               final("stop", usage={"prompt_tokens": 7, "completion_tokens": 2}))
    r = await provider(handler).complete([ProviderMessage("user", "x")])
    assert r.usage == {"prompt_tokens": 7, "completion_tokens": 2}


async def test_length_finish_reason_is_mapped():
    def handler(request):
        return stream_response(chunk(content="cut off"), final("length"))
    assert (await provider(handler).complete([ProviderMessage("user", "x")])).finish_reason == "length"


# -- encoding ----------------------------------------------------------------

def test_encode_tool_matches_the_openai_shape():
    spec = ToolSpec("create_file", "Create a file",
                    {"type": "object", "properties": {"path": {"type": "string"}},
                     "required": ["path"]})
    out = OpenAICompatibleProvider.encode_tool(spec)
    assert out["type"] == "function"
    assert out["function"]["name"] == "create_file"
    assert out["function"]["parameters"]["required"] == ["path"]


def test_encode_messages_roundtrips_a_tool_exchange():
    call = ToolCall("create_folder", {"path": "MyApp"}, id="call_9")
    msgs = [
        ProviderMessage("user", "make MyApp"),
        ProviderMessage("assistant", "on it", tool_calls=[call]),
        ProviderMessage("tool", "created", tool_call_id="call_9"),
    ]
    out = OpenAICompatibleProvider.encode_messages(msgs, system="be useful")
    assert out[0] == {"role": "system", "content": "be useful"}
    assistant = out[2]
    assert assistant["tool_calls"][0]["id"] == "call_9"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"path": "MyApp"}
    assert out[3] == {"role": "tool", "tool_call_id": "call_9", "content": "created"}


def test_encode_message_builds_vision_parts():
    out = OpenAICompatibleProvider.encode_message(
        ProviderMessage("user", "what is this?", images_b64=["QUJD"]))
    assert out["content"][0] == {"type": "text", "text": "what is this?"}
    assert out["content"][1]["image_url"]["url"].startswith("data:image/png;base64,QUJD")


# -- health / fall-through ---------------------------------------------------

async def test_falls_through_to_the_next_model_on_502():
    """A dead route must not sink the call - this is what a real gateway
    does to you."""
    seen: list[str] = []

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "dead/model":
            return httpx.Response(502, text="error code: 502")
        return stream_response(chunk(content="recovered"), final())

    p = provider(handler, model="dead/model", fallback_models=["good/model"])
    r = await p.complete([ProviderMessage("user", "x")])
    assert r.text == "recovered"
    assert seen == ["dead/model", "good/model"]
    assert r.model == "good/model"


async def test_repeated_failures_bench_a_model():
    def handler(request):
        model = json.loads(request.content)["model"]
        if model == "dead/model":
            return httpx.Response(502, text="nope")
        return stream_response(chunk(content="ok"), final())

    p = provider(handler, model="dead/model", fallback_models=["good/model"])
    for _ in range(3):
        await p.complete([ProviderMessage("user", "x")])
    assert "dead/model" in p.health.benched
    assert p.candidates() == ["good/model"], "a benched model should be skipped"


async def test_success_clears_the_strike_count():
    state = {"fail": True}

    def handler(request):
        if state["fail"]:
            return httpx.Response(502, text="nope")
        return stream_response(chunk(content="ok"), final())

    p = provider(handler, model="m1", fallback_models=["m2"])
    with pytest.raises(ProviderError):          # everything is down
        await p.complete([ProviderMessage("user", "x")])
    state["fail"] = False
    await p.complete([ProviderMessage("user", "x")])       # m1 recovers
    assert p.health.benched == []
    assert p.candidates()[0] == "m1"


async def test_all_models_failing_raises_with_detail():
    def handler(request):
        return httpx.Response(503, text="everything is down")
    p = provider(handler, model="a", fallback_models=["b"])
    with pytest.raises(ProviderError) as e:
        await p.complete([ProviderMessage("user", "x")])
    assert "a:" in str(e.value) and "b:" in str(e.value)


def test_every_model_benched_still_returns_candidates():
    """A stale health record must never make the provider refuse to try."""
    p = OpenAICompatibleProvider("https://gw.test/v1", model="a", fallback_models=["b"])
    for _ in range(3):
        p.health.failed("a")
        p.health.failed("b")
    assert p.candidates() == ["a", "b"]


def test_no_model_configured_is_an_error():
    with pytest.raises(ProviderError):
        OpenAICompatibleProvider("https://gw.test/v1").candidates()


# -- wiring ------------------------------------------------------------------

async def test_health_check_and_model_listing():
    def handler(request):
        assert request.url.path.endswith("/models")
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"data": [{"id": "b/model"}, {"id": "a/model"}]})
    p = provider(handler)
    assert await p.health_check() is True
    assert await p.list_models() == ["a/model", "b/model"]


async def test_health_check_is_false_when_unreachable():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)
    assert await provider(handler).health_check() is False


def test_capabilities_flag_local_endpoints():
    assert OpenAICompatibleProvider("http://localhost:11434/v1").capabilities().local
    assert not OpenAICompatibleProvider("https://gw.test/v1").capabilities().local


def test_provider_from_env(monkeypatch):
    from agents.providers import provider_from_env
    monkeypatch.delenv("TORA_OMNIROUTE_BASE_URL", raising=False)
    assert provider_from_env() is None

    monkeypatch.setenv("TORA_OMNIROUTE_BASE_URL", "https://gw.test/v1/")
    monkeypatch.setenv("TORA_OMNIROUTE_API_KEY", "sk-abc")
    p = provider_from_env()
    assert p is not None
    assert p.base_url == "https://gw.test/v1" and p.api_key == "sk-abc"
    assert p.model == GATEWAY_MODELS["general"][0]
    assert p.fallback_models == GATEWAY_MODELS["general"][1:]


def test_build_router_registers_the_gateway_when_configured(monkeypatch):
    from agents.providers import build_router
    from agents.core import TaskKind
    monkeypatch.setenv("TORA_OMNIROUTE_BASE_URL", "https://gw.test/v1")
    monkeypatch.setenv("TORA_OMNIROUTE_API_KEY", "sk-abc")
    router = build_router()
    assert any(isinstance(p, OpenAICompatibleProvider) for p in router.providers())
    assert isinstance(router.pick(TaskKind.CODING), OpenAICompatibleProvider)


def test_build_router_without_a_gateway_uses_the_mock(monkeypatch):
    from agents.providers import MockProvider, build_router
    monkeypatch.delenv("TORA_OMNIROUTE_BASE_URL", raising=False)
    router = build_router()
    assert all(isinstance(p, MockProvider) for p in router.providers())


def test_a_fallback_does_not_suppress_the_gateway(monkeypatch):
    """Regression: bootstrap always passes a fallback, and the gateway
    used to be registered only when it did not. The app ran on the mock
    while looking fully configured, and an agent 'completed' having done
    nothing at all."""
    from agents.providers import MockProvider, build_router
    monkeypatch.setenv("TORA_OMNIROUTE_BASE_URL", "https://gw.test/v1")
    router = build_router(fallback=MockProvider())
    assert any(isinstance(p, OpenAICompatibleProvider) for p in router.providers())


def test_gateway_false_means_offline(monkeypatch):
    from agents.providers import build_router
    monkeypatch.setenv("TORA_OMNIROUTE_BASE_URL", "https://gw.test/v1")
    router = build_router(gateway=False)
    assert not any(isinstance(p, OpenAICompatibleProvider) for p in router.providers())


def test_bootstrap_offline_flag_forces_the_mock(monkeypatch, tmp_path):
    from agents.bootstrap import build_agent_manager
    from agents.providers import MockProvider
    monkeypatch.setenv("TORA_OMNIROUTE_BASE_URL", "https://gw.test/v1")
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    try:
        assert all(isinstance(p, MockProvider) for p in mgr.router.providers())
    finally:
        mgr.stop()


# -- live: a real gateway, opt in with `-m live` -----------------------------

@pytest.mark.live
async def test_live_gateway_calls_a_tool():
    """Proves the whole chain against a real endpoint: SSE reassembly,
    fragmented arguments, and a model that actually decides to act."""
    import os

    from dotenv import load_dotenv

    from agents.providers import provider_from_env
    load_dotenv()
    p = provider_from_env()
    if p is None:
        pytest.skip("TORA_OMNIROUTE_BASE_URL not configured")
    try:
        spec = ToolSpec("create_folder", "Create a folder on disk.",
                        {"type": "object", "properties": {"path": {"type": "string"}},
                         "required": ["path"]})
        r = await p.complete(
            [ProviderMessage("user", "Create a folder called MyApp. Use the tool.")],
            tools=[spec],
            system="You are an agent. Use tools to act; never just describe.")
        assert r.finish_reason == "tool_calls", r.text
        assert r.tool_calls[0].tool_name == "create_folder"
        assert r.tool_calls[0].arguments.get("path")
        assert r.model in p.candidates()
        assert r.usage.get("total_tokens", 0) > 0
    finally:
        await p.aclose()


@pytest.mark.live
def test_live_agent_writes_a_real_file(tmp_path):
    """The fallback planner path: no template matches, so the model must
    plan and call the tools itself."""
    from dotenv import load_dotenv

    from agents.bootstrap import build_agent_manager
    from agents.core import Task, TaskKind, TaskStatus
    load_dotenv()
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True).start()
    try:
        if not isinstance(mgr.router.pick(TaskKind.CODING), OpenAICompatibleProvider):
            pytest.skip("no gateway configured")
        task = mgr.wait(mgr.submit_task(Task(
            title="Write a haiku file",
            goal="Create a file called haiku.txt containing a three-line haiku about "
                 "debugging. Use the create_file tool.",
            workspace=str(tmp_path), kind=TaskKind.CODING)).id, 180)
        assert task.status is TaskStatus.COMPLETED, task.error
        written = tmp_path / "haiku.txt"
        assert written.is_file(), f"nothing written; workspace has {list(tmp_path.iterdir())}"
        assert written.read_text(encoding="utf-8").strip()
    finally:
        mgr.stop()


# -- AG-06: a request every route would refuse is refused once ---------------------------------------------------

async def test_a_bad_tool_schema_is_refused_once_but_a_low_credit_route_falls_through():
    seen: list[str] = []

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "a":
            return httpx.Response(400, json={"error": {"type": "invalid_request_error",
                                                       "message": "tools.9.custom.input_schema: JSON schema is invalid"}})
        return stream_response(chunk(content="ok"), final())

    p = provider(handler, model="a", fallback_models=["b"])
    with pytest.raises(ProviderError, match="refused"):
        await p.complete([ProviderMessage("user", "x")])
    assert seen == ["a"]

    seen.clear()

    def low_credit(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "a":
            return httpx.Response(400, json={"error": {"type": "invalid_request_error", "message":
                "Your credit balance is too low to access the Anthropic API (messages.create)"}})
        return stream_response(chunk(content="ok"), final())

    p = provider(low_credit, model="a", fallback_models=["b"])
    assert (await p.complete([ProviderMessage("user", "x")])).text == "ok" and seen == ["a", "b"]
