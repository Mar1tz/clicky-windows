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
