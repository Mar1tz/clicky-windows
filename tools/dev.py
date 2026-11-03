"""HeyTora on this PC, against a local Supabase - never the live one.

    python tools/dev.py            # everything: the local stack, its functions, signed in, the app
    python tools/dev.py --as pro@heytora.test     # ... signed in as this account
    python tools/dev.py --tour     # ... and the first-launch tour
    python tools/dev.py --signed-out              # ... at the sign-in window
    python tools/dev.py doctor     # is everything right? what to do if not
    python tools/dev.py setup      # the stack and .env.development, without the app
    python tools/dev.py signin [email]   # sign the dev app in (default free@heytora.test)
    python tools/dev.py reset      # empty the local database: every migration again, then the seed
    python tools/dev.py status     # where everything is
    python tools/dev.py stop       # stop the local stack (its data is kept)

What it does, in order - each step only after the one before it worked:

  1. Docker is running (Docker Desktop);
  2. the local Supabase (supabase/config.toml, ports 573xx) is up. The first
     start applies supabase/migrations and supabase/seed.sql, and migrations
     pulled since then are applied each time;
  3. .env.development has the local address and keys (from `supabase
     status`), a ticket secret of its own, and the Gemini key from .env when
     it has none. Keys you filled in by hand are kept. heytora-web, next to
     this folder, is pointed at the same local stack;
  4. the functions in supabase/functions are served from this folder, with
     .env.development as their secrets - edit one, it reloads;
  5. the first time, the app is signed in (free@heytora.test);
  6. the app runs from source against all of it, with its own data folder
     (%LOCALAPPDATA%\\HeyTora-dev), so the installed HeyTora stays signed in
     to the live service. Close the installed one first: one HeyTora at a time.
     Closing the app stops the functions; the database keeps running.

Accounts (supabase/seed.sql): free@, pro@, max@ and out@heytora.test (credits
used up), or any new email. Codes arrive in Mailpit, the local inbox it prints.

Everything here refuses a service that is not on this PC.

Needs: Docker Desktop, Node (npx fetches the pinned Supabase CLI). Windows.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.development"
# Pinned, so every PC runs the same CLI; raise it on purpose.
SUPABASE = "npx --yes supabase@2.120.0"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "HeyTora-dev"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULT_ACCOUNT = "free@heytora.test"

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


def is_local_url(url: str) -> bool:
    return urlsplit(url or "").hostname in ("127.0.0.1", "localhost")


def migration_gaps(rows: list[dict]) -> tuple[list[str], list[str]]:
    """`supabase migration list` rows -> (in the folder but not applied here,
    applied here but no longer in the folder)."""
    pending = [r["local"] for r in rows if r.get("local") and not r.get("remote")]
    unknown = [r["remote"] for r in rows if r.get("remote") and not r.get("local")]
    return pending, unknown


def local_migrations() -> tuple[list[str], list[str]] | None:
    done = supabase("migration list --local --output-format json", check=False, capture=True)
    try:
        out = done.stdout
        return migration_gaps(json.loads(out[out.find("{"):])["migrations"])
    except (ValueError, KeyError):
        return None


def catch_up_migrations() -> None:
    """Pulled someone's migration? `supabase start` does not apply it to a
    database that already exists, and the app then fails in ways that look
    nothing like that. Applied here, on the local database only."""
    gaps = local_migrations()
    if gaps is None or not gaps[0]:
        return
    print(f"Applying {len(gaps[0])} new migration(s) to the local database: {', '.join(gaps[0])}")
    if supabase("migration up --local", check=False, capture=True).returncode != 0:
        sys.exit("A new migration did not apply. `python tools/dev.py reset` rebuilds the local "
                 "database from empty, and shows the error.")


def write_website_env(status: dict) -> None:
    """heytora-web, next to this folder, signs in against the local stack too.
    .env.development.local: `next dev` reads it and a production build never
    does - .env.local would ship the local address to real users."""
    web = ROOT.parent / "heytora-web"
    if not (web / "package.json").exists():
        return
    target = web / ".env.development.local"
    if not target.exists():
        target.write_text("# Written by heytora-app's tools/dev.py. `next dev` only.\n", encoding="utf-8")
    set_key(target, "NEXT_PUBLIC_SUPABASE_URL", status["API_URL"], quote_mode="never")
    set_key(target, "NEXT_PUBLIC_SUPABASE_ANON_KEY", status["ANON_KEY"], quote_mode="never")


def functions_answering(api_url: str) -> bool:
    """A 401 is an answer: the function ran and asked who is calling."""
    try:
        urllib.request.urlopen(f"{api_url}/functions/v1/heytora/me", timeout=3)
        return True
    except urllib.error.HTTPError as e:
        return e.code < 500
    except OSError:
        return False


def wait_for_functions(api_url: str, seconds: float = 60.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if functions_answering(api_url):
            return True
        time.sleep(1.0)
    return False


def write_env(status: dict) -> list[str]:
    """Fill .env.development; keep what is there. Returns the empty required keys."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text(HEADER, encoding="utf-8")
    have = dotenv_values(ENV_FILE)
    # the live values could only come from a pasted line - say so, never use them
    url = have.get("TORA_SUPABASE_URL") or ""
    if (url and not is_local_url(url)) or (have.get("PADDLE_ENV") or "sandbox") != "sandbox":
        sys.exit(".env.development points at live (an address not on this PC, or PADDLE_ENV not sandbox). "
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
    print("  Accounts    free@, pro@, max@, out@heytora.test (out@: credits used up) - or any new email")
    print()


def cmd_setup() -> int:
    status = ensure_stack()
    catch_up_migrations()
    missing = write_env(status)
    write_website_env(status)
    print(f"Wrote {ENV_FILE.name}.")
    print_where(status)
    if missing:
        print("Still empty - fill these in .env.development:")
        for key, what in KEYS:
            if key in missing:
                print(f"  {key}: {what}")
        return 1
    return 0


def cmd_run(email: str = "", tour: bool = False, signed_out: bool = False) -> int:
    if cmd_setup():
        return 1
    env_values = {k: v or "" for k, v in dotenv_values(ENV_FILE).items()}
    api_url = env_values.get("TORA_SUPABASE_URL", "")
    if not is_local_url(api_url):                  # never, whatever the files say
        sys.exit(f"Refusing to run against {api_url}: not on this PC.")
    print("Serving the functions (they reload when you save)...")
    functions = subprocess.Popen(f"{SUPABASE} functions serve --env-file \"{ENV_FILE}\"", cwd=ROOT,
                                 shell=True)
    try:
        if not wait_for_functions(api_url):
            print("The functions did not answer - see the output above. `python tools/dev.py doctor`")
            return 1
        kept = DATA_DIR / "account.bin"
        if signed_out:
            kept.unlink(missing_ok=True)           # the dev folder's only: live's is another folder
        elif email or not kept.exists():
            done = subprocess.run([sys.executable, __file__, "signin", email or DEFAULT_ACCOUNT], cwd=ROOT)
            if done.returncode != 0:
                return done.returncode
        env = os.environ | {k: v for k, v in env_values.items() if k.startswith("TORA_")}
        env["TORA_DATA_DIR"] = str(DATA_DIR)
        env["TORA_DEV"] = "1"                      # config.py: this address wins over .env
        env.pop("TORA_ALLOW_LIVE", None)
        if tour:
            env["TORA_FORCE_SETUP"] = "1"
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
    status = ensure_stack()
    if write_env(status):
        return cmd_setup()
    mailpit = (status.get("MAILPIT_URL") or status.get("INBUCKET_URL") or "").rstrip("/")
    for k, v in dotenv_values(ENV_FILE).items():
        if k.startswith("TORA_"):
            os.environ[k] = v or ""
    if not is_local_url(os.environ.get("TORA_SUPABASE_URL", "")):
        sys.exit("Refusing to sign in: the address in .env.development is not on this PC.")
    os.environ["TORA_DATA_DIR"] = str(DATA_DIR)
    os.environ["TORA_DEV"] = "1"
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
    # Its sessions went with the database: the app's saved one is no use now,
    # and would open on an error instead of signing in again.
    (DATA_DIR / "account.bin").unlink(missing_ok=True)
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


def cmd_doctor() -> int:
    """Everything checked, with what to do about each. Changes nothing."""
    problems = 0

    def line(ok: bool, what: str, fix: str = "") -> None:
        nonlocal problems
        problems += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FIX '} {what}" + (f"\n         -> {fix}" if fix and not ok else ""))

    print("HeyTora dev environment\n")
    line(bool(shutil.which("npx")), "Node (npx)", "Install Node 22 or newer.")
    running = docker_running()
    line(running, "Docker is running", "Start Docker Desktop.")
    status = local_status() if running else None
    line(status is not None, "The local Supabase is up", "python tools/dev.py setup")
    env = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
    line(bool(env), ".env.development exists", "python tools/dev.py setup")
    url = env.get("TORA_SUPABASE_URL") or ""
    line(is_local_url(url), f"It points at this PC ({url or 'nothing yet'})",
         "Fix it by hand, or delete it and run setup: live keys belong in .env.production.")
    line((env.get("PADDLE_ENV") or "sandbox") == "sandbox", "Paddle is the sandbox",
         "Set PADDLE_ENV=sandbox in .env.development.")
    empty = [k for k, _ in KEYS if k in REQUIRED and not env.get(k)]
    line(not empty, "Required keys are filled",
         f"Fill in .env.development: {', '.join(empty)} (a free Gemini key: https://aistudio.google.com/app/apikey)")
    if status is not None:
        gaps = local_migrations()
        if gaps is None:
            line(False, "Migrations could be read", "python tools/dev.py reset")
        else:
            line(not gaps[0], "Every migration is applied", f"python tools/dev.py setup applies {', '.join(gaps[0])}")
            line(not gaps[1], "No migration is applied that the folder lacks",
                 f"{', '.join(gaps[1])} - another branch's? python tools/dev.py reset")
        mail = (status.get("MAILPIT_URL") or status.get("INBUCKET_URL") or "").rstrip("/")
        try:
            urllib.request.urlopen(f"{mail}/api/v1/messages?limit=1", timeout=3)
            line(True, "Mailpit answers (sign-in codes)")
        except OSError:
            line(False, "Mailpit answers (sign-in codes)", "python tools/dev.py stop, then setup")
        serving = functions_answering(status["API_URL"])
        print(f"  {'ok  ' if serving else 'note'} Functions "
              + ("answering" if serving else "not served now: python tools/dev.py serves them while the app runs"))
    signed_in = (DATA_DIR / "account.bin").exists()
    print(f"  note Dev app {'has a sign-in' if signed_in else 'not signed in yet: python tools/dev.py does it'}"
          f" ({DATA_DIR})")
    tasks = subprocess.run(["tasklist", "/FI", "IMAGENAME eq HeyTora.exe", "/NH"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW).stdout
    line("HeyTora.exe" not in tasks, "The installed HeyTora is closed",
         "Quit it from the tray: one HeyTora at a time - they share the hotkey and microphone.")
    print("\n" + ("All good: python tools/dev.py" if not problems else f"{problems} to fix."))
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "setup", "signin", "reset", "status", "stop", "doctor"])
    ap.add_argument("email", nargs="?", default=DEFAULT_ACCOUNT,
                    help="signin: the account (free@, pro@, max@, out@heytora.test or any new email)")
    ap.add_argument("--as", dest="account", default="", metavar="EMAIL", help="run: signed in as this account")
    ap.add_argument("--tour", action="store_true", help="run: open the first-launch tour")
    ap.add_argument("--signed-out", action="store_true", help="run: start at the sign-in window")
    args = ap.parse_args()
    if args.command == "signin":
        return cmd_signin(args.email)
    if args.command == "run":
        return cmd_run(args.account, args.tour, args.signed_out)
    return {"setup": cmd_setup, "reset": cmd_reset, "status": cmd_status, "stop": cmd_stop,
            "doctor": cmd_doctor}[args.command]()


if __name__ == "__main__":
    sys.exit(main())
