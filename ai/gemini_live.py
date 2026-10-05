"""
Transport for Gemini's Live API — the bidirectional websocket that carries
audio both ways.

Two things ride on this: the conversation itself (gemini_realtime.py) and
dictation (audio/stt/gemini_stt.py). Neither of them should have to know
about frame shapes, base64 or turn bookkeeping, so all of that lives here and
the callers just push audio in and read LiveEvents out.

Wire notes worth keeping, all confirmed against the live endpoint:

  • The first frame must be {"setup": ...} and the server answers
    {"setupComplete": {}} before it will accept anything else.

  • Push-to-talk wants MANUAL turn control. With
    realtimeInputConfig.automaticActivityDetection.disabled the hotkey
    defines the turn: activityStart on press, activityEnd on release. Left on
    automatic, the server's own VAD decides, which fights the hotkey and — on
    a short clip with no leading silence — can sit there and never answer at
    all.

  • A tool call ends the turn immediately: the server sends toolCall and then
    turnComplete with no audio at all. The answer only arrives in the NEXT
    turn, after toolResponse goes back. A reader that stops at the first
    turnComplete hangs up on the model mid-sentence, every time it uses a
    tool. LiveEvent.produced_output exists so callers can tell the two kinds
    of turnComplete apart.

  • Audio in is 16 kHz PCM16 mono; audio out is 24 kHz PCM16 mono.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from ai.gemini import LIVE_WS, api_key, own_key

_log = logging.getLogger("tora.live")

INPUT_RATE = 16000
OUTPUT_RATE = 24000

# 100 ms of 16 kHz mono PCM16. Small enough that the server's VAD and
# transcriber see speech as it happens, big enough that we are not paying
# websocket framing overhead on every 20 ms.
CHUNK_BYTES = INPUT_RATE * 2 // 10

# SP-03: what a live answer costs. $ per million tokens, by modality, from
# Google's list for gemini-3.8-live (checked 6 Oct 2026). The Live API bills
# every generation for the WHOLE prompt - system instruction, tools, history -
# so a question that calls a tool pays for it twice. gemini_vision.PRICES has
# the screen looks; these are the voice.
LIVE_PRICES = {
    "in": {"TEXT": 0.75, "AUDIO": 3.00, "IMAGE": 1.00, "VIDEO": 1.00},
    "out": {"TEXT": 4.50, "AUDIO": 12.00},
}


def _by_modality(details, total: int, default: str) -> dict:
    """Tokens by modality. The breakdown can come up short of the count -
    on 6 Oct by about 460 of a 4,500-token prompt - and the rest is priced at
    `default`, so nothing billed goes uncounted."""
    out: dict = {}
    for d in details or []:
        out[d.get("modality", default)] = out.get(d.get("modality", default), 0) + int(d.get("tokenCount", 0))
    gap = int(total or 0) - sum(out.values())
    if gap > 0:
        out[default] = out.get(default, 0) + gap
    return out


def live_cost_usd(usage: dict) -> float:
    """The dollars one usageMetadata (or a sum of them, add_usage) stands for.
    Thinking is billed as text out; a modality the table does not know, at
    the dearest rate on its side."""
    if not usage:
        return 0.0
    ins = _by_modality(usage.get("promptTokensDetails"), usage.get("promptTokenCount", 0), "TEXT")
    for m, n in _by_modality(usage.get("toolUsePromptTokensDetails"),
                             usage.get("toolUsePromptTokenCount", 0), "TEXT").items():
        ins[m] = ins.get(m, 0) + n
    outs = _by_modality(usage.get("responseTokensDetails"), usage.get("responseTokenCount", 0), "AUDIO")
    outs["TEXT"] = outs.get("TEXT", 0) + int(usage.get("thoughtsTokenCount", 0))
    top_in, top_out = max(LIVE_PRICES["in"].values()), max(LIVE_PRICES["out"].values())
    dollars = sum(n * LIVE_PRICES["in"].get(m, top_in) for m, n in ins.items())
    dollars += sum(n * LIVE_PRICES["out"].get(m, top_out) for m, n in outs.items())
    return dollars / 1e6


def add_usage(total: dict, more: dict) -> dict:
    """Two usageMetadata summed: every count, and each *Details list by modality."""
    out = dict(total or {})
    for k, v in (more or {}).items():
        if isinstance(v, (int, float)):
            out[k] = out.get(k, 0) + v
        elif isinstance(v, list):
            by = {d.get("modality"): int(d.get("tokenCount", 0)) for d in out.get(k, [])}
            for d in v:
                by[d.get("modality")] = by.get(d.get("modality"), 0) + int(d.get("tokenCount", 0))
            out[k] = [{"modality": m, "tokenCount": n} for m, n in by.items()]
    return out


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LiveEvent:
    kind: str                    # see below
    text: str = ""
    audio: bytes = b""
    sample_rate: int = OUTPUT_RATE
    calls: list[ToolCall] = field(default_factory=list)
    produced_output: bool = False
    raw: dict = field(default_factory=dict)

    # kinds:
    #   ready             setup accepted
    #   input_transcript  what the server heard us say
    #   output_transcript what the model is saying, as text
    #   audio             spoken audio, PCM16 @ sample_rate
    #   text              text response (TEXT-modality sessions)
    #   tool_call         model wants a tool run; answer with send_tool_response
    #   turn_complete     model finished a turn
    #   interrupted       the user talked over the model; drop queued audio
    #   voice_activity    server VAD: text is ACTIVITY_START or ACTIVITY_END
    #   go_away           server is about to close this session
    #   usage             raw is the server's usageMetadata (SP-03)
    #   closed            socket is gone


class LiveSession:
    """One websocket to one Live model. Use as an async context manager."""

    def __init__(self, model: str, setup: dict, resume_handle: Optional[str] = None):
        self._model = model
        self._setup = dict(setup)
        self._setup["model"] = f"models/{model}"
        # The server hands out a handle for THIS conversation. Passing it back
        # on the next connect is what makes a session continue instead of
        # starting again with no memory of anything said so far.
        self._setup["sessionResumption"] = {"handle": resume_handle} if resume_handle else {}
        self._ws = None
        self._resume_handle: Optional[str] = resume_handle
        self._closed = False

    @property
    def resume_handle(self) -> Optional[str]:
        """The latest handle the server gave us, to resume this conversation."""
        return self._resume_handle

    @property
    def setup_for_test(self) -> dict:
        """The setup exactly as it will be sent. Only tests read this."""
        return dict(self._setup)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def connect(self, timeout: float = 20.0) -> None:
        address = await self._address()
        self._ws = await asyncio.wait_for(
            websockets.connect(address, max_size=None),
            timeout=timeout,
        )
        await self._send({"setup": self._setup})
        raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        msg = json.loads(raw if isinstance(raw, str) else raw.decode())
        if "setupComplete" not in msg:
            raise RuntimeError(f"Live setup refused: {json.dumps(msg)[:300]}")

    async def _address(self) -> str:
        """Where the websocket goes. Own key: Google with the key. HeyTora
        account: Google with a voice pass - a one-session token HeyTora's
        server mints with the business key, which never comes here. Each
        pass is a talk message (or a dictation) on the user's plan."""
        if own_key():
            key = api_key()
            if not key:
                raise RuntimeError("No Google API key. Add GOOGLE_API_KEY in Tray → API Key.")
            return f"{LIVE_WS}?key={key}"
        from account.service import account
        from ai.gemini import Models, _kind
        kind = "dictation" if self._model == Models.STT else "talk"
        if kind == "dictation" and _kind.get() == "wake":
            kind = "wake"                 # "is that 'Tora'?" - not a dictation (0006)
        voice_pass = await account().avoice_pass(kind, self._model)
        self.usage = voice_pass.usage
        return voice_pass.address()

    usage: dict = {}                      # what the voice pass said about the plan

    async def close(self) -> None:
        self._closed = True
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                _log.debug("live close failed", exc_info=True)

    async def __aenter__(self) -> "LiveSession":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @property
    def is_open(self) -> bool:
        return self._ws is not None and not self._closed

    # ── Sending ──────────────────────────────────────────────────────────────

    async def _send(self, payload: dict) -> None:
        if self._ws is None:
            raise RuntimeError("Live session is not connected")
        await self._ws.send(json.dumps(payload))

    async def activity_start(self) -> None:
        """Hotkey pressed — the user is about to talk."""
        await self._send({"realtimeInput": {"activityStart": {}}})

    async def activity_end(self) -> None:
        """Hotkey released — answer now."""
        await self._send({"realtimeInput": {"activityEnd": {}}})

    async def send_audio(self, pcm16: bytes) -> None:
        """Stream mic audio, split into 100 ms frames."""
        for i in range(0, len(pcm16), CHUNK_BYTES):
            await self._send({
                "realtimeInput": {
                    "audio": {
                        "data": base64.b64encode(pcm16[i:i + CHUNK_BYTES]).decode(),
                        "mimeType": f"audio/pcm;rate={INPUT_RATE}",
                    }
                }
            })

    async def send_image(self, jpeg_b64: str) -> None:
        """Put a screenshot in front of the model without ending the turn.

        turnComplete stays false on purpose: this is context for the question
        that is about to be asked, not a question in itself. Sent before the
        mic opens, so the image is already uploaded and encoded by the time
        the user stops speaking.
        """
        await self._send({
            "clientContent": {
                "turns": [{
                    "role": "user",
                    "parts": [{"inlineData": {
                        "mimeType": "image/jpeg", "data": jpeg_b64,
                    }}],
                }],
                "turnComplete": False,
            }
        })

    async def send_text(self, text: str, *, turn_complete: bool = True) -> None:
        await self._send({
            "clientContent": {
                "turns": [{"role": "user", "parts": [{"text": text}]}],
                "turnComplete": turn_complete,
            }
        })

    async def send_tool_response(self, results: list[tuple[ToolCall, dict]]) -> None:
        await self._send({
            "toolResponse": {
                "functionResponses": [
                    {"id": call.id, "name": call.name, "response": result}
                    for call, result in results
                ]
            }
        })

    async def audio_stream_end(self) -> None:
        """Only for automatic-VAD sessions — tells the server the clip is over."""
        await self._send({"realtimeInput": {"audioStreamEnd": True}})

    # ── Receiving ────────────────────────────────────────────────────────────

    async def events(self) -> AsyncIterator[LiveEvent]:
        """Yield LiveEvents until the socket closes."""
        if self._ws is None:
            return
        produced = False
        try:
            async for raw in self._ws:
                msg: dict[str, Any] = json.loads(
                    raw if isinstance(raw, str) else raw.decode()
                )

                if "sessionResumptionUpdate" in msg:
                    self._resume_handle = (
                        msg["sessionResumptionUpdate"].get("newHandle")
                    )
                    continue

                # Before the branches below: it can ride on any message,
                # a toolCall included, and those `continue`.
                if msg.get("usageMetadata"):
                    yield LiveEvent(kind="usage", raw=msg["usageMetadata"])

                if "goAway" in msg:
                    yield LiveEvent(kind="go_away", raw=msg)
                    continue

                if "toolCall" in msg:
                    calls = [
                        ToolCall(
                            id=c.get("id", ""),
                            name=c.get("name", ""),
                            args=c.get("args") or {},
                        )
                        for c in msg["toolCall"].get("functionCalls", [])
                    ]
                    if calls:
                        yield LiveEvent(kind="tool_call", calls=calls, raw=msg)
                    continue

                # Server-side VAD verdict. The transcribe model reports
                # ACTIVITY_END as soon as it hears the utterance finish,
                # which beats waiting out a local silence timer.
                va = msg.get("voiceActivity")
                if va:
                    yield LiveEvent(
                        kind="voice_activity", text=va.get("type", ""), raw=msg
                    )

                sc = msg.get("serverContent")
                if sc is None:
                    continue

                if sc.get("interrupted"):
                    produced = False
                    yield LiveEvent(kind="interrupted", raw=msg)

                it = sc.get("inputTranscription")
                if it and it.get("text"):
                    yield LiveEvent(kind="input_transcript", text=it["text"])

                ot = sc.get("outputTranscription")
                if ot and ot.get("text"):
                    produced = True
                    yield LiveEvent(kind="output_transcript", text=ot["text"])

                for part in sc.get("modelTurn", {}).get("parts", []):
                    blob = part.get("inlineData") or part.get("inline_data")
                    if blob and blob.get("data"):
                        produced = True
                        mime = blob.get("mimeType") or blob.get("mime_type") or ""
                        rate = OUTPUT_RATE
                        if "rate=" in mime:
                            try:
                                rate = int(mime.split("rate=")[1].split(";")[0])
                            except (ValueError, IndexError):
                                pass
                        yield LiveEvent(
                            kind="audio",
                            audio=base64.b64decode(blob["data"]),
                            sample_rate=rate,
                        )
                    elif part.get("text"):
                        produced = True
                        yield LiveEvent(kind="text", text=part["text"])

                if sc.get("turnComplete"):
                    yield LiveEvent(kind="turn_complete", produced_output=produced)
                    produced = False

        except ConnectionClosed:
            pass
        except Exception as e:
            _log.debug("live receive loop ended: %s", e, exc_info=True)
        # NOT in a finally. When the pump task is cancelled, GeneratorExit
        # is thrown in at the suspended yield; yielding again from a finally
        # is the textbook 'async generator ignored GeneratorExit' and was
        # logged on every reconnect. A cancelled consumer does not need to
        # be told the stream closed.
        yield LiveEvent(kind="closed")
