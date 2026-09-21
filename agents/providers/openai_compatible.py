"""One provider for every OpenAI-compatible endpoint.

This replaces what were going to be three adapters (Claude, Gemini,
OpenAI). A gateway such as OmniRoute fronts all of them behind one
`/chat/completions` surface, so the useful unit of work is a correct
OpenAI client rather than three vendor clients. It also works unchanged
against api.openai.com, a local llama.cpp/Ollama server, or anything
else that speaks the same shape.

Two things learned from testing a real gateway (see
docs/agents/FRAMEWORK_RESEARCH.md), both of which this file exists to
absorb:

  * **It may stream even when you did not ask.** Every reply came back
    as `text/event-stream` with no `stream` parameter set, so a client
    that calls `response.json()` raises `JSONDecodeError` and the
    endpoint looks broken rather than merely different. We reassemble
    SSE whenever the content type says so, and parse plain JSON when it
    does not.
  * **Reliability is per model, not per endpoint.** On one gateway, some
    routes answered in two seconds while others returned 502, timed out
    at 90 s, or returned 403 for lack of entitlement. A single dead route
    must not sink a running agent, so each model gets a health record and
    the provider falls through a preference chain.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterable, Optional

import httpx

from agents.core import (
    AgentProvider, ProviderCapabilities, ProviderMessage, ProviderResponse,
    ToolCall, ToolSpec, new_id,
)

log = logging.getLogger("agents.providers.openai")

# How long a model stays benched after it fails, and how many strikes it
# takes. One 502 is noise; three in a row is a dead route.
COOLDOWN_S = 120.0
STRIKES = 3

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


class ProviderError(RuntimeError):
    """Every candidate model failed."""


class _Health:
    """Per-model failure memory, so a bad route is skipped rather than
    retried into a running agent's face."""

    def __init__(self, cooldown: float = COOLDOWN_S, strikes: int = STRIKES):
        self._cooldown = cooldown
        self._strikes = strikes
        self._fails: dict[str, int] = {}
        self._benched: dict[str, float] = {}

    def usable(self, model: str, *, now: Optional[float] = None) -> bool:
        until = self._benched.get(model)
        if until is None:
            return True
        if (now or time.monotonic()) >= until:
            self._benched.pop(model, None)
            self._fails.pop(model, None)
            return True
        return False

    def succeeded(self, model: str) -> None:
        self._fails.pop(model, None)
        self._benched.pop(model, None)

    def failed(self, model: str, *, now: Optional[float] = None) -> None:
        count = self._fails.get(model, 0) + 1
        self._fails[model] = count
        if count >= self._strikes:
            self._benched[model] = (now or time.monotonic()) + self._cooldown
            log.warning("benching model %s for %.0fs after %d failures",
                        model, self._cooldown, count)

    @property
    def benched(self) -> list[str]:
        return sorted(self._benched)


class OpenAICompatibleProvider(AgentProvider):
    """Tool-calling against any `/chat/completions` endpoint."""

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        model: str = "",
        fallback_models: Optional[Iterable[str]] = None,
        name: Optional[str] = None,
        vision: bool = True,
        cost_tier: int = 2,
        max_context: int = 128_000,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        client: Optional[httpx.AsyncClient] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback_models = list(fallback_models or [])
        if name:
            self.name = name
        self._vision = vision
        self._cost_tier = cost_tier
        self._max_context = max_context
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._extra_headers = dict(extra_headers or {})
        self.health = _Health()

    # -- plumbing ------------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self._extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def candidates(self, preferred: Optional[str] = None) -> list[str]:
        """Models to try, in order, skipping any currently benched.

        If every candidate is benched we return the original order rather
        than nothing: a stale health record must never make the provider
        refuse to try at all.
        """
        ordered: list[str] = []
        for m in [preferred or self.model, *self.fallback_models]:
            if m and m not in ordered:
                ordered.append(m)
        if not ordered:
            raise ProviderError("no model configured")
        usable = [m for m in ordered if self.health.usable(m)]
        return usable or ordered

    # -- the interface -------------------------------------------------------

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: Optional[list[ToolSpec]] = None,
        *,
        system: str = "",
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> ProviderResponse:
        payload_base: dict[str, Any] = {
            "messages": self.encode_messages(messages, system=system),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload_base["tools"] = [self.encode_tool(t) for t in tools]
            payload_base["tool_choice"] = "auto"

        errors: list[str] = []
        for candidate in self.candidates(model):
            payload = {**payload_base, "model": candidate}
            try:
                response = await self._post(payload)
            except Exception as exc:  # noqa: BLE001 - try the next route
                self.health.failed(candidate)
                errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
                log.warning("model %s failed: %s", candidate, exc)
                continue
            self.health.succeeded(candidate)
            # Report the ROUTE we called, not the upstream's self-reported
            # name. A gateway answers "claude-sonnet-4-6" for several
            # different route ids; the route is what health is keyed on,
            # what the router chose, and what reproduces the call.
            response.model = candidate
            return response
        raise ProviderError("every model failed: " + "; ".join(errors))

    async def _post(self, payload: dict[str, Any]) -> ProviderResponse:
        url = f"{self.base_url}/chat/completions"
        client = self._http()
        # Ask for the stream explicitly. Some gateways stream regardless,
        # and asking makes the two cases one case.
        body = {**payload, "stream": True}
        async with client.stream("POST", url, json=body, headers=self._headers()) as r:
            if r.status_code >= 400:
                detail = (await r.aread())[:400].decode("utf-8", "replace")
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}: {detail}",
                                            request=r.request, response=r)
            content_type = r.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                return await self._read_sse(r)
            raw = await r.aread()
        return self.decode_response(json.loads(raw))

    @staticmethod
    async def _read_sse(response: httpx.Response) -> ProviderResponse:
        """Reassemble a streamed completion.

        Tool calls arrive in fragments keyed by `index`: the name usually
        lands in the first fragment and the JSON arguments are split
        across later ones, so both are concatenated rather than replaced.
        """
        text = ""
        finish = "stop"
        model = ""
        usage: dict[str, int] = {}
        slots: dict[int, dict[str, str]] = {}

        # Breaking out of `async for` leaves the generator suspended, and
        # Python complains that its aclose() was never awaited. Hold the
        # iterator so it can be closed deterministically on the way out.
        lines = response.aiter_lines()
        try:
            async for line in lines:
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    event = json.loads(chunk)
                except json.JSONDecodeError:
                    continue                   # keep-alive or comment frame
                model = event.get("model") or model
                if isinstance(event.get("usage"), dict):
                    usage = {k: v for k, v in event["usage"].items() if isinstance(v, int)}
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or choice.get("message") or {}
                    text += delta.get("content") or ""
                    for fragment in delta.get("tool_calls") or []:
                        slot = slots.setdefault(fragment.get("index", len(slots)),
                                                {"id": "", "name": "", "args": ""})
                        slot["id"] = fragment.get("id") or slot["id"]
                        fn = fragment.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["args"] += fn.get("arguments") or ""
                    finish = choice.get("finish_reason") or finish
        finally:
            await lines.aclose()

        calls = [_to_call(s) for _i, s in sorted(slots.items()) if s["name"]]
        return ProviderResponse(
            text=text,
            tool_calls=calls,
            finish_reason="tool_calls" if calls else _finish(finish),
            usage=usage,
            model=model,
        )

    @staticmethod
    def decode_response(data: dict[str, Any]) -> ProviderResponse:
        """A non-streamed `/chat/completions` body."""
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            calls.append(_to_call({"id": raw.get("id") or "",
                                   "name": fn.get("name") or "",
                                   "args": fn.get("arguments") or ""}))
        usage = {k: v for k, v in (data.get("usage") or {}).items() if isinstance(v, int)}
        return ProviderResponse(
            text=message.get("content") or "",
            tool_calls=calls,
            finish_reason="tool_calls" if calls else _finish(choice.get("finish_reason")),
            usage=usage,
            model=data.get("model") or "",
        )

    # -- encoding ------------------------------------------------------------

    @staticmethod
    def encode_tool(spec: ToolSpec) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters or {"type": "object", "properties": {}},
        }}

    @classmethod
    def encode_messages(cls, messages: list[ProviderMessage], *,
                        system: str = "") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            out.append(cls.encode_message(m))
        return out

    @staticmethod
    def encode_message(m: ProviderMessage) -> dict[str, Any]:
        if m.role == "tool":
            # A tool result must carry the id of the call it answers, or
            # the model cannot match them up.
            return {"role": "tool", "tool_call_id": m.tool_call_id or "",
                    "content": m.content or ""}
        if m.role == "assistant" and m.tool_calls:
            return {
                "role": "assistant",
                "content": m.content or None,
                "tool_calls": [{
                    "id": c.id, "type": "function",
                    "function": {"name": c.tool_name,
                                 "arguments": json.dumps(c.arguments, ensure_ascii=False)},
                } for c in m.tool_calls],
            }
        if m.images_b64:
            parts: list[dict[str, Any]] = []
            if m.content:
                parts.append({"type": "text", "text": m.content})
            for b64 in m.images_b64:
                url = b64 if b64.startswith("data:") else f"data:image/png;base64,{b64}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            return {"role": m.role, "content": parts}
        return {"role": m.role, "content": m.content or ""}

    # -- capabilities --------------------------------------------------------

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=True, vision=self._vision, streaming=True,
            local="localhost" in self.base_url or "127.0.0.1" in self.base_url,
            max_context=self._max_context, cost_tier=self._cost_tier,
        )

    async def health_check(self) -> bool:
        try:
            r = await self._http().get(f"{self.base_url}/models", headers=self._headers())
            return r.status_code < 400
        except Exception:  # noqa: BLE001
            return False

    async def list_models(self) -> list[str]:
        r = await self._http().get(f"{self.base_url}/models", headers=self._headers())
        r.raise_for_status()
        return sorted(m["id"] for m in (r.json().get("data") or []) if m.get("id"))


def _to_call(slot: dict[str, str]) -> ToolCall:
    """A fragment set becomes a ToolCall. Malformed argument JSON is kept
    as `_raw` rather than dropped: the registry's validation will reject
    it with a message the model can act on, which beats a silent no-op."""
    raw = (slot.get("args") or "").strip()
    try:
        args = json.loads(raw) if raw else {}
        if not isinstance(args, dict):
            args = {"value": args}
    except json.JSONDecodeError:
        args = {"_raw": raw}
    return ToolCall(tool_name=slot["name"].strip(), arguments=args,
                    id=slot.get("id") or new_id())


def _finish(reason: Optional[str]) -> str:
    if reason in ("length", "max_tokens"):
        return "length"
    if reason in ("tool_calls", "function_call"):
        return "tool_calls"
    return "stop"
