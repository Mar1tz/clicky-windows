"""HeyTora on this PC, against a local Supabase - never the live one.

    python tools/dev.py            # start the local stack, its functions, and the app
    python tools/dev.py setup      # the same, without the app: write .env.development, say what is missing
    python tools/dev.py signin [email]   # sign the dev app in (default free@heytora.test) - the code from Mailpit
    python tools/dev.py reset      # empty the local database: every migration again, then the seed
    python tools/dev.py status     # where everything is
    python tools/dev.py stop       # stop the local stack (its data is kept)

What it does, in order - each step only after the one before it worked:

  1. Docker is running (Docker Desktop);
  2. the local Supabase (supabase/config.toml, ports 573xx) is up. The first
     start applies supabase/migrations and supabase/seed.sql;
  3. .env.development has the local address and keys (from `supabase
     status`), a ticket secret of its own, and the Gemini key from .env when
     it has none. Keys you filled in by hand are kept;
  4. the functions in supabase/functions are served from this folder, with
     .env.development as their secrets - edit one, it reloads;
  5. the app runs from source against all of it, with its own data folder
     (%LOCALAPPDATA%\\HeyTora-dev), so the installed HeyTora stays signed in
     to the live service. Close the installed one first: one HeyTora at a time.
     Closing the app stops the functions; the database keeps running.

Sign in with free@heytora.test or pro@heytora.test (supabase/seed.sql), or
any new email. The code arrives in Mailpit, the local inbox it prints.

Needs: Docker Desktop, Node (npx fetches the pinned Supabase CLI). Windows.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.development"
# Pinned, so every PC runs the same CLI; raise it on purpose.
SUPABASE = "npx --yes supabase@2.120.0"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "HeyTora-dev"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

HEADER = """\
# HeyTora local development - written by `python tools/dev.py`.
# Local and test keys only: the live ones belong in .env.production.
# The functions read this file (supabase functions serve --env-file); the
# app gets the TORA_* lines from tools/dev.py.
"""

# (key, what it is) - in the order they are written. Empty is allowed for
# the optional ones; `setup` lists what is still empty.
KEYS = [
    ("TORA_SUPABASE_URL", "the local Supabase (written from `supabase status`)"),
    ("TORA_SUPABASE_ANON_KEY", "its anon key (written from `supabase status`)"),
    ("TORA_WEBSITE_URL", "the website: heytora-web's `pnpm dev`"),
    ("GEMINI_API_KEY", "a Gemini key for the heytora function (copied from .env's GOOGLE_API_KEY)"),
    ("HEYTORA_TICKET_SECRET", "signs talk and agent tickets (made up here)"),
    ("PADDLE_ENV", "always sandbox here"),
    ("PADDLE_API_KEY", "optional: Paddle SANDBOX API key, for checkout"),
    ("PADDLE_WEBHOOK_SECRET", "optional: Paddle SANDBOX notification secret"),
    ("PADDLE_PRICES", "optional: pro=pri_...,max=pri_... (sandbox price ids)"),
    ("PADDLE_CREDITS_PRODUCT", "optional: the sandbox credit packs' product id"),
    ("OMNIROUTE_API_KEY", "optional: the agents' gateway"),
    ("OMNIROUTE_BASE_URL", "optional: the agents' gateway"),
]
REQUIRED = {"TORA_SUPABASE_URL", "TORA_SUPABASE_ANON_KEY", "GEMINI_API_KEY", "HEYTORA_TICKET_SECRET"}


def supabase(args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    # npx is a .cmd on Windows: shell=True (CLAUDE.md)
    if not capture:
        print(f"  $ supabase {args}")
    return subprocess.run(f"{SUPABASE} {args}", cwd=ROOT, shell=True, check=check, text=True,
                          encoding="utf-8", errors="replace", capture_output=capture)


def docker_running() -> bool:
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    except FileNotFoundError:
        return False
    return r.returncode == 0


def local_status() -> dict | None:
    """The running stack's addresses and keys, or None when it is down."""
    r = supabase("status -o json", check=False, capture=True)
    if r.returncode != 0:
        return None
    start = r.stdout.find("{")
    try:
        return json.loads(r.stdout[start:]) if start >= 0 else None
    except json.JSONDecodeError:
        return None


def ensure_stack() -> dict:
    if not docker_running():
        sys.exit("Docker is not running. Start Docker Desktop, then run this again.")
    status = local_status()
    if status is None:
        print("Starting the local Supabase (the first time downloads it - a few minutes)...")
        supabase("start")
        status = local_status()
    if status is None:
        sys.exit("The local Supabase did not start - see the output above.")
    return status


def write_env(status: dict) -> list[str]:
    """Fill .env.development; keep what is there. Returns the empty required keys."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text(HEADER, encoding="utf-8")
    have = dotenv_values(ENV_FILE)
    # the live values could only come from a pasted line - say so, never use them
    if "supabase.co" in (have.get("TORA_SUPABASE_URL") or "") or (have.get("PADDLE_ENV") or "sandbox") != "sandbox":
        sys.exit(".env.development points at live (a supabase.co address or PADDLE_ENV not sandbox). "
                 "Fix it by hand - live keys belong in .env.production.")
    wanted = {
        "TORA_SUPABASE_URL": status["API_URL"],
        "TORA_SUPABASE_ANON_KEY": status["ANON_KEY"],
        "PADDLE_ENV": "sandbox",
    }
    defaults = {
        "TORA_WEBSITE_URL": "http://localhost:3500",
        "HEYTORA_TICKET_SECRET": secrets.token_urlsafe(32),
        "GEMINI_API_KEY": (dotenv_values(ROOT / ".env").get("GOOGLE_API_KEY") or ""
                           if (ROOT / ".env").exists() else ""),
    }
    for key, _ in KEYS:
        value = wanted.get(key) or have.get(key) or defaults.get(key, "")
        if key not in have or have.get(key) != value:
            set_key(ENV_FILE, key, value, quote_mode="never")
    have = dotenv_values(ENV_FILE)
    return [k for k, _ in KEYS if k in REQUIRED and not have.get(k)]


def print_where(status: dict) -> None:
    print()
    print(f"  Database    {status['DB_URL']}")
    print(f"  API         {status['API_URL']}")
    print(f"  Studio      {status['STUDIO_URL']}      (tables, users, SQL)")
    print(f"  Mailpit     {status.get('MAILPIT_URL') or status.get('INBUCKET_URL')}      (sign-in codes)")
    print(f"  Data folder {DATA_DIR}")
    print(f"  Accounts    free@heytora.test, pro@heytora.test - or any new email")
    print()


def cmd_setup() -> int:
    status = ensure_stack()
    missing = write_env(status)
    print(f"Wrote {ENV_FILE.name}.")
    print_where(status)
    if missing:
        print("Still empty - fill these in .env.development:")
        for key, what in KEYS:
            if key in missing:
                print(f"  {key}: {what}")
        return 1
    return 0


def cmd_run() -> int:
    if cmd_setup():
        return 1
    env_values = {k: v or "" for k, v in dotenv_values(ENV_FILE).items()}
    print("Serving the functions (they reload when you save)...")
    functions = subprocess.Popen(f"{SUPABASE} functions serve --env-file \"{ENV_FILE}\"", cwd=ROOT,
                                 shell=True)
    try:
        env = os.environ | {k: v for k, v in env_values.items() if k.startswith("TORA_")}
        env["TORA_DATA_DIR"] = str(DATA_DIR)
        print("Starting HeyTora against the local service. Close it to stop.")
        return subprocess.run([sys.executable, "main.py"], cwd=ROOT, env=env).returncode
    finally:
        # shell=True: the CLI runs under cmd - end the whole tree, not only cmd
        subprocess.run(["taskkill", "/PID", str(functions.pid), "/T", "/F"], capture_output=True,
                       creationflags=NO_WINDOW)


def cmd_signin(email: str) -> int:
    """Sign the dev app in, here: the code is read from Mailpit. The window's
    only door is the website, and heytora-web doesn't run against the local
    database yet - so without this a dev run couldn't get past signing in
    (29 Oct 2026)."""
    import re
    import time
    import urllib.request
    status = ensure_stack()
    if write_env(status):
        return cmd_setup()
    mailpit = (status.get("MAILPIT_URL") or status.get("INBUCKET_URL") or "").rstrip("/")
    for k, v in dotenv_values(ENV_FILE).items():
        if k.startswith("TORA_"):
            os.environ[k] = v or ""
    os.environ["TORA_DATA_DIR"] = str(DATA_DIR)
    os.environ["TORA_ACCESS"] = "account"
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from account.service import account
    from config import cfg

    def newest_code(after: float) -> str:
        with urllib.request.urlopen(f"{mailpit}/api/v1/messages?limit=20", timeout=5) as r:
            listing = json.load(r)
        for m in listing.get("messages") or []:
            to = [a.get("Address", "").lower() for a in m.get("To") or []]
            if email.lower() not in to:
                continue
            with urllib.request.urlopen(f"{mailpit}/api/v1/message/{m['ID']}", timeout=5) as r:
                body = json.load(r)
            found = re.search(r"\b(\d{6})\b", body.get("Text") or body.get("HTML") or "")
            created = m.get("Created", "")
            if found and created and _when(created) >= after - 2:
                return found.group(1)
        return ""

    sent_at = time.time()
    print(f"Sending a sign-in code to {email} (local Mailpit)...")
    account().send_code(email)
    code = ""
    for _ in range(40):
        code = newest_code(sent_at)
        if code:
            break
        time.sleep(0.25)
    if not code:
        print(f"No code arrived in Mailpit ({mailpit}). Is the local stack up? `python tools/dev.py status`")
        return 1
    summary = account().verify(email, code)
    cfg.set_access_mode("account")
    print(f"Signed in as {email} ({summary.get('plan_name') or summary.get('plan') or 'signed in'}), "
          f"in {DATA_DIR}.")
    print("Now: python tools/dev.py   (add $env:TORA_FORCE_SETUP=1 for the first-launch tour)")
    return 0


def _when(created: str) -> float:
    """Mailpit's time ("2026-10-29T18:13:01.123Z") as seconds."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def cmd_reset() -> int:
    ensure_stack()
    print("Emptying the local database: every migration again, then the seed.")
    supabase("db reset")
    return 0


def cmd_status() -> int:
    status = local_status()
    if status is None:
        print("The local Supabase is not running. `python tools/dev.py` starts it.")
        return 1
    print_where(status)
    return 0


def cmd_stop() -> int:
    supabase("stop")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "setup", "signin", "reset", "status", "stop"])
    ap.add_argument("email", nargs="?", default="free@heytora.test",
                    help="signin: the account (free@heytora.test, pro@heytora.test or any new email)")
    args = ap.parse_args()
    if args.command == "signin":
        return cmd_signin(args.email)
    return {"run": cmd_run, "setup": cmd_setup, "reset": cmd_reset,
            "status": cmd_status, "stop": cmd_stop}[args.command]()


if __name__ == "__main__":
    sys.exit(main())
