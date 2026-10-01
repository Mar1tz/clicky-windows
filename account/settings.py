"""Where HeyTora's own service lives - public values only.

A Supabase project's address and its anon ("publishable") key are made to
ship inside apps: what a signed-in user may do is decided by the database's
row-level security and by the `heytora` server function, not by keeping
these secret. The Gemini key is NOT here and must never be: it is a secret
of the server function (supabase/functions/heytora, `GEMINI_API_KEY`).

Fill the three values in below for a build that offers HeyTora accounts
(or set TORA_SUPABASE_URL / TORA_SUPABASE_ANON_KEY / TORA_WEBSITE_URL).
Empty, the app offers only "use my own Gemini key".
"""
from __future__ import annotations

import os

# e.g. "https://abcdefghijklmnop.supabase.co"
_SUPABASE_URL = ""
# the project's anon / publishable key (Supabase → Project Settings → API)
_SUPABASE_ANON_KEY = ""
# the website where people create an account and choose a plan
_WEBSITE_URL = ""

FUNCTION = "heytora"


def supabase_url() -> str:
    return (os.environ.get("TORA_SUPABASE_URL", "").strip() or _SUPABASE_URL).rstrip("/")


def anon_key() -> str:
    return os.environ.get("TORA_SUPABASE_ANON_KEY", "").strip() or _SUPABASE_ANON_KEY


def website_url() -> str:
    return (os.environ.get("TORA_WEBSITE_URL", "").strip() or _WEBSITE_URL).rstrip("/")


def configured() -> bool:
    """Does this build know where HeyTora's service is?"""
    return bool(supabase_url() and anon_key())


def function_url(path: str = "") -> str:
    base = f"{supabase_url()}/functions/v1/{FUNCTION}"
    return f"{base}/{path.lstrip('/')}" if path else base
