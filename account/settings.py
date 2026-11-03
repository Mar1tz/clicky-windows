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

# HeyTora's Supabase project (the website uses the same one)
_SUPABASE_URL = "https://uizwfpxzzkokyrkvjzkr.supabase.co"
# the project's anon / publishable key (Supabase → Project Settings → API)
_SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVpendmcHh6em"
    "tva3lya3ZqemtyIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk5NzUwODAsImV4cCI6MjEwNTU1MTA4MH0."
    "w3ySeG3DTJMf6wzBpFflA5diDY7hzvbMYm3_q96aDfE"
)
# the website: the privacy policy and terms, and Paddle's checkout page (/pay).
# Accounts are made in the app itself (account/service.send_code).
_WEBSITE_URL = "https://heytora.co.za"

FUNCTION = "heytora"


def supabase_url() -> str:
    return (os.environ.get("TORA_SUPABASE_URL", "").strip() or _SUPABASE_URL).rstrip("/")


def anon_key() -> str:
    return os.environ.get("TORA_SUPABASE_ANON_KEY", "").strip() or _SUPABASE_ANON_KEY


def website_url() -> str:
    return (os.environ.get("TORA_WEBSITE_URL", "").strip() or _WEBSITE_URL).rstrip("/")


def is_local() -> bool:
    """A service on this PC (tools/dev.py), not HeyTora's real one."""
    from urllib.parse import urlsplit
    return urlsplit(supabase_url()).hostname in ("127.0.0.1", "localhost")


def live_from_source_blocked() -> bool:
    """HeyTora run from a source folder, about to talk to the real service.

    The live address is the default, so `python main.py` in a checkout used
    to act on real accounts, real credits and the real database, from code
    nobody has released. Installed builds are unaffected. tools/dev.py is the
    way to run from source; TORA_ALLOW_LIVE=1 says it is meant (docs/LOCAL_DEV.md)."""
    import sys
    if getattr(sys, "frozen", False) or not supabase_url() or is_local():
        return False
    return os.environ.get("TORA_ALLOW_LIVE", "").strip().lower() not in ("1", "true", "yes", "on")


LIVE_BLOCKED_WORDS = """
HeyTora did not start: this is a source folder, and it would talk to the LIVE
service - real accounts, real credits, real users' database.

  python tools/dev.py          run it against the local copy (docs/LOCAL_DEV.md)

Meant to test against live?   $env:TORA_ALLOW_LIVE=1; python main.py
"""


def configured() -> bool:
    """Does this build know where HeyTora's service is?"""
    return bool(supabase_url() and anon_key())


def function_url(path: str = "") -> str:
    base = f"{supabase_url()}/functions/v1/{FUNCTION}"
    return f"{base}/{path.lstrip('/')}" if path else base
