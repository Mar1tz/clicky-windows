"""
Multilingual auto-detect.

Looks at the user's transcript and picks a reply-language hint to add to the
system prompt: speak Hindi to Tora and it answers in Hindi.

There is no voice to pick any more. Gemini's voices are language-agnostic —
the same voice speaks whatever language the text is in — so getting the
language right is entirely a matter of the model replying in it.

Backend: tries `langdetect` (fast, pure Python). Falls back to script
heuristics (Devanagari → Hindi, Cyrillic → Russian, Han → Chinese, etc.).
"""

from __future__ import annotations

import re

# ISO 639-1 → display name, as it appears in the language directive.
_LANG_NAMES = {
    "en": "English",    "hi": "Hindi",      "es": "Spanish",
    "fr": "French",     "de": "German",     "it": "Italian",
    "pt": "Portuguese", "ja": "Japanese",   "zh": "Chinese",
    "ko": "Korean",     "ru": "Russian",    "ar": "Arabic",
    "ta": "Tamil",      "te": "Telugu",     "bn": "Bengali",
    "ur": "Urdu",       "el": "Greek",
}


def _script_heuristic(text: str) -> str:
    """Cheap Unicode-block-based detector for common scripts."""
    if re.search(r"[ऀ-ॿ]", text):    return "hi"   # Devanagari
    if re.search(r"[ঀ-৿]", text):    return "bn"   # Bengali
    if re.search(r"[஀-௿]", text):    return "ta"   # Tamil
    if re.search(r"[ఀ-౿]", text):    return "te"   # Telugu
    if re.search(r"[؀-ۿ]", text):    return "ar"   # Arabic / Urdu
    if re.search(r"[一-鿿]", text):    return "zh"   # Han
    if re.search(r"[぀-ヿ]", text):    return "ja"   # Hiragana/Katakana
    if re.search(r"[가-힯]", text):    return "ko"   # Hangul
    if re.search("[Ͱ-Ͽἀ-῿]", text): return "el"   # Greek
    if re.search(r"[Ѐ-ӿ]", text):    return "ru"   # Cyrillic
    return "en"


def detect_language(text: str) -> str:
    if not text or not text.strip():
        return "en"

    # Script heuristic first — fastest and deterministic for non-Latin
    script = _script_heuristic(text)
    if script != "en":
        return script

    # For Latin-script languages (Spanish, French, German, etc.)
    # try langdetect; fall back gracefully if not installed or text too short
    try:
        from langdetect import detect, DetectorFactory   # type: ignore
        DetectorFactory.seed = 0
        # langdetect is unreliable on very short strings; require ≥ 12 chars
        if len(text.strip()) >= 12:
            code = detect(text).split("-")[0].lower()
            return code if code in _LANG_NAMES else "en"
    except Exception:
        pass

    return "en"


def name_for(code: str) -> str:
    return _LANG_NAMES.get(code, "English")


def language_directive(code: str) -> str:
    """Add to system prompt so the LLM matches the user's language."""
    if code == "en":
        return ""
    name = name_for(code)
    return (
        f"\n\nLANGUAGE (MANDATORY — never ignore this rule): "
        f"The user is communicating in {name}. "
        f"You MUST respond ENTIRELY in {name} — every single word of your reply. "
        f"Do NOT switch to English mid-response. "
        f"UI element names and technical terms may stay in their original form "
        f"if no {name} equivalent exists, but all explanations must be in {name}. "
        f"Match the user's tone and register."
    )
