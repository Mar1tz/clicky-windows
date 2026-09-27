"""
Live model discovery for the model dropdown.

GET /v1beta/models tells us what this key can actually call, which is better
than a hardcoded list that goes stale every time Google ships something. The
result is cached to %LOCALAPPDATA%\\HeyTora\\models_gemini.json for 7 days —
short enough that a new release shows up within the week, long enough that
startup is not making a network call it does not need.

Only conversation models are listed. The endpoint also returns embedding,
TTS, image, video and live models; picking one of those from a dropdown
labelled "Model" gives a companion that cannot hold a conversation, so they
are filtered out here. The models behind Tora's other jobs are not chosen
this way — they are fixed per role in ai/gemini.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

from ai import gemini
from ai.gemini import Models

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60

PROVIDER = "gemini"

# Used before the first successful refresh, and if the network is down.
_FALLBACK: list[dict] = [
    {"id": Models.VISION_FAST, "label": "Gemini 3.8 Flash — fast", "vision": True},
    {"id": Models.VISION_DEEP, "label": "Gemini 3.1 Pro — careful", "vision": True},
    {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash", "vision": True},
    {"id": "gemini-3.1-flash-lite", "label": "Gemini 3.1 Flash Lite", "vision": True},
]

# Substrings that mark a model as something other than a chat model.
_NOT_CHAT = (
    "embedding", "tts", "-image", "image-", "veo-", "lyria", "aqa",
    "transcribe", "live", "robotics", "computer-use", "deep-research",
    "antigravity", "nano-banana",
)


def _data_dir() -> Path:
    from config import data_dir
    return data_dir()


def _cache_path(provider: str = PROVIDER) -> Path:
    return _data_dir() / f"models_{provider}.json"


def _label(model_id: str, display: str) -> str:
    if model_id == Models.VISION_FAST:
        return f"{display} — fast (default)"
    if model_id == Models.VISION_DEEP:
        return f"{display} — careful"
    return display


async def _fetch() -> list[dict]:
    if not gemini.api_key():
        return []
    r = await gemini.client().get(
        f"{gemini.API_ROOT}/models",
        params={"key": gemini.api_key(), "pageSize": 200},
        timeout=15.0,
    )
    if r.status_code != 200:
        raise gemini.GeminiError(r.status_code, r.text)

    out = []
    for m in r.json().get("models", []):
        mid = (m.get("name") or "").replace("models/", "")
        if not mid or mid in _NOT_CHAT:
            continue
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        if any(marker in mid for marker in _NOT_CHAT):
            continue
        out.append({
            "id": mid,
            "label": _label(mid, m.get("displayName") or mid),
            "vision": True,   # every current Gemini chat model takes images
        })

    # Newest first, with the two role defaults pinned to the top so the
    # dropdown opens on the models Tora actually uses.
    out.sort(key=lambda m: m["id"], reverse=True)
    pinned = [m for m in out if m["id"] in (Models.VISION_FAST, Models.VISION_DEEP)]
    rest = [m for m in out if m["id"] not in (Models.VISION_FAST, Models.VISION_DEEP)]
    return pinned + rest


# ─── Public API ───────────────────────────────────────────────────────────────

def cached_models(provider: str = PROVIDER) -> list[dict]:
    p = _cache_path(provider)
    if p.exists():
        try:
            ms = json.loads(p.read_text()).get("models", [])
            if ms:
                return ms
        except Exception:
            pass
    return list(_FALLBACK)


def cache_is_stale(provider: str = PROVIDER, ttl: int = CACHE_TTL_SECONDS) -> bool:
    p = _cache_path(provider)
    if not p.exists():
        return True
    try:
        blob = json.loads(p.read_text())
        return (time.time() - float(blob.get("fetched_at", 0))) > ttl
    except Exception:
        return True


async def refresh(provider: str = PROVIDER) -> list[dict]:
    """Fetch live + write the cache. Returns the new list."""
    models = await _fetch()
    if not models:
        return cached_models(provider)   # no key — don't cache an empty list
    _cache_path(provider).write_text(
        json.dumps({"fetched_at": time.time(), "models": models}, indent=2)
    )
    return models


async def refresh_all_stale() -> dict[str, int]:
    """Refresh if the cache has aged out. Returns {provider: count}."""
    if not cache_is_stale():
        return {}
    try:
        return {PROVIDER: len(await refresh())}
    except Exception:
        return {PROVIDER: -1}


def model_ids(provider: str = PROVIDER) -> list[str]:
    return [m["id"] for m in cached_models(provider)]


def best_default(provider: str = PROVIDER) -> Optional[str]:
    return Models.VISION_FAST


# ─── CLI: `python -m ai.model_registry [show|refresh|roles]` ──────────────────

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) >= 2 else "show"

    if cmd == "roles":
        width = max(len(k) for k in Models.all_roles())
        for role, model in Models.all_roles().items():
            print(f"  {role:<{width}}  {model}")
    elif cmd == "show":
        print(f"[gemini] {'stale' if cache_is_stale() else 'fresh'}")
        for m in cached_models():
            print(f"  {m['id']:<36} {m['label']}")
    elif cmd == "refresh":
        async def _run():
            try:
                print(f"[gemini] refreshed {len(await refresh())} models")
            except Exception as e:
                print(f"[gemini] FAILED: {e}")
            finally:
                await gemini.aclose()
        asyncio.run(_run())
    else:
        print("Usage: python -m ai.model_registry [show|refresh|roles]")
