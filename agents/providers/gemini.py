"""Direct Gemini provider - agents that work with the key the app already has.

Tora requires a Google key to exist at all (the voice path runs on Gemini
Live), so this is the provider that needs **no extra setup**: with only
`GOOGLE_API_KEY` configured, planning, verification and the coding loop
run against Gemini's `generateContent` with function calling. A gateway
account, when the user adds one, outranks it in the router.

Two deliberate differences from the app's own `ai/gemini.py` client:

  * The key travels in the `x-goog-api-key` **header**, never as `?key=`
    in the URL. A URL query string is what `httpx` logs at INFO, which is
    how the app's key ended up in `tora.log` 166 times (M7). Headers are
    not logged.
  * Health and fall-through are per model, reusing the same `_Health` as
    the OpenAI-compatible provider, because Gemini model ids also come
    and go ("no longer available" is a real response).

Tool-call ids: Gemini's functionCall parts carry no id, and its
functionResponse wants the *name*. We mint an id per call and, when
encoding a tool turn, recover the name from the assistant turn that
issued it - so the rest of the system keeps its id-based contract.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Awaitable, Callable, Iterable, Optional

import httpx

from agents.core import (
    AgentProvider, ProviderCapabilities, ProviderMessage, ProviderResponse, ToolCall,
    ToolSpec, new_id,
)

from .openai_compatible import DEFAULT_TIMEOUT, ProviderError, _Health

log = logging.getLogger("agents.providers.gemini")

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-3.1-pro-preview", "gemini-2.5-flash"]

# JSON-schema keywords Gemini's OpenAPI subset rejects. Stripped, not
# translated: the model still sees the shape that matters.
_UNSUPPORTED_SCHEMA_KEYS = {"additionalProperties", "$schema", "default", "examples",
                            "title", "pattern", "minLength", "maxLength"}


def api_key_from_env() -> str:
    for name in ("TORA_GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        from config import cfg
        return (cfg.google_api_key or "").strip()
    except Exception:  # noqa: BLE001 - config is optional for library use
        return ""


# What a 400 says when the request itself is wrong - the same for every model
# in the chain. A 400 about the model ("not supported for this model") is not.
_REQUEST_FAULTS = ("function_declarations", "GenerateContentRequest", "Invalid JSON payload",
                   "Invalid value at", "response_schema")


def request_is_wrong(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) != 400:
        return False
    text = str(exc)
    return any(k in text for k in _REQUEST_FAULTS) and "not supported" not in text.lower()


class GeminiAgentProvider(AgentProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str = "",
        *,
        model: str = "",
        fallback_models: Optional[Iterable[str]] = None,
        base_url: str = API_ROOT,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        client: Optional[httpx.AsyncClient] = None,
        auth: Optional[Callable[[], Awaitable[dict]]] = None,
    ):
        # `auth`: headers for each call instead of a key - a HeyTora account
        # reaching Gemini through its server (the business key stays there).
        self._auth = auth
        self.api_key = "" if auth else (api_key or api_key_from_env())
        chain = [model] if model else []
        chain += [m for m in (fallback_models if fallback_models is not None else DEFAULT_MODELS)
                  if m and m not in chain]
        if not chain:
            chain = list(DEFAULT_MODELS)
        self.model = chain[0]
        self.fallback_models = chain[1:]
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
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
        return {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

    async def _request_headers(self) -> dict[str, str]:
        if self._auth is None:
            return self._headers()
        return {"Content-Type": "application/json", **(await self._auth())}

    def candidates(self, preferred: Optional[str] = None) -> list[str]:
        ordered: list[str] = []
        for m in [preferred or self.model, *self.fallback_models]:
            if m and m not in ordered:
                ordered.append(m)
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
        if not self.api_key and self._auth is None:
            raise ProviderError("no Gemini API key configured")
        headers = await self._request_headers()
        body: dict[str, Any] = {
            "contents": self.encode_messages(messages),
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [self.encode_tool(t) for t in tools]}]

        errors: list[str] = []
        for candidate in self.candidates(model):
            try:
                r = await self._http().post(
                    f"{self.base_url}/models/{candidate}:generateContent",
                    headers=headers, json=body)
                if r.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"HTTP {r.status_code}: {r.text[:300]}", request=r.request, response=r)
                response = self.decode_response(r.json())
            except Exception as exc:  # noqa: BLE001 - try the next model
                if request_is_wrong(exc):
                    # AG-06: the REQUEST is wrong - a tool schema, the JSON -
                    # and every model would say the same: "Create Three
                    # Monkeys Ball poster" failed three times over one schema.
                    # Said once; the model is not to blame.
                    log.warning("gemini refused the request itself (%s): %s", candidate, exc)
                    raise ProviderError(f"the request was refused ({candidate}): {exc}") from exc
                self.health.failed(candidate)
                errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
                log.warning("gemini model %s failed: %s", candidate, exc)
                continue
            self.health.succeeded(candidate)
            response.model = candidate
            return response
        raise ProviderError("every Gemini model failed: " + "; ".join(errors))

    # -- encoding ------------------------------------------------------------

    @classmethod
    def encode_messages(cls, messages: list[ProviderMessage]) -> list[dict[str, Any]]:
        # id -> function name, so a tool turn can answer by name
        names: dict[str, str] = {}
        for m in messages:
            for c in m.tool_calls:
                names[c.id] = c.tool_name
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue                       # carried in systemInstruction
            contents.append(cls.encode_message(m, names))
        return contents

    @staticmethod
    def encode_message(m: ProviderMessage, names: dict[str, str]) -> dict[str, Any]:
        if m.role == "tool":
            fn = names.get(m.tool_call_id or "", "tool")
            payload: Any
            try:
                payload = json.loads(m.content) if m.content.strip().startswith(("{", "[")) else None
            except ValueError:
                payload = None
            if not isinstance(payload, dict):
                payload = {"result": m.content}
            return {"role": "user",
                    "parts": [{"functionResponse": {"name": fn, "response": payload}}]}
        parts: list[dict[str, Any]] = []
        if m.content:
            parts.append({"text": m.content})
        for b64 in m.images_b64:
            data, mime = b64, "image/png"
            if b64.startswith("data:"):
                head, _, data = b64.partition(",")
                mime = head[5:].split(";")[0] or mime
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
        for c in m.tool_calls:
            parts.append({"functionCall": {"name": c.tool_name, "args": c.arguments}})
        if not parts:
            parts.append({"text": ""})
        return {"role": "model" if m.role == "assistant" else "user", "parts": parts}

    @classmethod
    def encode_tool(cls, spec: ToolSpec) -> dict[str, Any]:
        return {"name": spec.name, "description": spec.description,
                "parameters": cls.scrub_schema(spec.parameters or {"type": "object", "properties": {}})}

    @classmethod
    def scrub_schema(cls, schema: Any, _names: bool = False) -> Any:
        """A tool's JSON schema, as Gemini will take it. Gemini refuses the
        WHOLE request - every agent step - over one bad schema, and three
        kinds of it were found on 5 Oct 2026 (write_workbook's rows failed a
        user's task; sending all 45 tools to Gemini found the rest):

          * an array that does not say what it holds ("items: missing
            field"): its items are text;
          * a type that is a list (["array", "string"]): one type - the
            array when it has items;
          * a parameter NAMED like a schema word Gemini lacks ("title"):
            the names under "properties" are kept - stripping them left
            focus_window requiring a "title" it no longer had. And
            "required" only names what is there.
        """
        if isinstance(schema, list):
            return [cls.scrub_schema(v) for v in schema]
        if not isinstance(schema, dict):
            return schema
        if _names:                        # property name -> its schema: every name stays
            return {name: cls.scrub_schema(v) for name, v in schema.items()}
        out = {k: cls.scrub_schema(v, _names=(k == "properties")) for k, v in schema.items()
               if k not in _UNSUPPORTED_SCHEMA_KEYS}
        kind = out.get("type")
        if isinstance(kind, list):
            kinds = [t for t in kind if t != "null"]
            out["type"] = "array" if ("array" in kinds and "items" in out) else (kinds[0] if kinds else "string")
        if out.get("type") == "array":
            items = out.get("items")
            if not isinstance(items, dict) or not (items.get("type") or items.get("anyOf")):
                out["items"] = {**(items if isinstance(items, dict) else {}), "type": "string"}
        if isinstance(out.get("required"), list):
            props = out.get("properties") if isinstance(out.get("properties"), dict) else {}
            out["required"] = [r for r in out["required"] if r in props]
        return out

    # -- decoding ------------------------------------------------------------

    @staticmethod
    def decode_response(data: dict[str, Any]) -> ProviderResponse:
        cands = data.get("candidates") or []
        if not cands:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(f"no candidates" + (f" (blocked: {block})" if block else ""))
        first = cands[0]
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for part in (first.get("content") or {}).get("parts") or []:
            if "text" in part and part["text"]:
                text_parts.append(part["text"])
            fc = part.get("functionCall")
            if fc:
                args = fc.get("args")
                calls.append(ToolCall(tool_name=fc.get("name", ""),
                                      arguments=args if isinstance(args, dict) else {},
                                      id=new_id()))
        reason = str(first.get("finishReason") or "STOP").upper()
        finish = "tool_calls" if calls else ("length" if reason == "MAX_TOKENS" else "stop")
        meta = data.get("usageMetadata") or {}
        usage = {k: v for k, v in {
            "prompt_tokens": meta.get("promptTokenCount"),
            "completion_tokens": meta.get("candidatesTokenCount"),
            "total_tokens": meta.get("totalTokenCount"),
        }.items() if isinstance(v, int)}
        return ProviderResponse(text="".join(text_parts), tool_calls=calls,
                                finish_reason=finish, usage=usage,
                                model=str(data.get("modelVersion") or ""))

    # -- capabilities --------------------------------------------------------

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(tools=True, vision=True, streaming=False,
                                    max_context=1_000_000, cost_tier=1)

    async def health_check(self) -> bool:
        if self._auth is not None:
            # Through HeyTora's server: asking would take an agent ticket - an
            # agent message on the user's plan - just to say hello.
            return True
        if not self.api_key:
            return False
        try:
            r = await self._http().get(f"{self.base_url}/models", headers=self._headers())
            return r.status_code < 400
        except Exception:  # noqa: BLE001
            return False
