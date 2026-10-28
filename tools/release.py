"""Publish a new HeyTora - the live server first, then the app every
installed copy updates itself to.

    python tools/release.py --check                             # what live is missing; changes nothing
    python tools/release.py 1.6.5 --notes "What changed, in a sentence or two"
    python tools/release.py 1.6.6 --notes "..." --required      # old copies must update
    python tools/release.py 1.6.5 --notes "..." --dry-run       # build and check, publish nothing
    python tools/release.py 1.6.5 --notes "..." --skip-build    # publish what the dry run built
    python tools/release.py --server-only                       # migrations and functions, no app

Before anything changes:

  a. this is `main`, nothing uncommitted, the same as GitHub's main - what
     users get is exactly what is in git;
  b. tests/agents is green.

Then, in order - each step only after the one before it worked:

  s1. live's migrations against supabase/migrations: one live has that is
      not here stops the release (someone changed live by hand); the ones
      live lacks are listed and pushed after you type "yes";
  s2. the functions (heytora, paddle) are deployed. Live's function secrets
      are left as they are - set them in the dashboard;
  1. version.py and installer.iss get the new version (only those lines);
  2. build.bat installer; the installer is checked: its version, that no
     .env went into it;
  3. the GitHub release v<version> on Qwickly/heytora with the installer
     (always named Setup-HeyTora.exe, so the download link never changes);
  4. the download site (the same repository): index.html's version, size
     and SHA-256, and latest.json - the manifest every installed HeyTora
     reads (updates/service.py). Published last, so no app is ever told
     about an installer that is not there yet;
  5. the version bump is committed here (version.py, installer.iss only).

--required sets min_version to this version: copies older than it ask to
update at once ("HeyTora needs an update"). Use it when the server changes
in a way old apps cannot follow. Otherwise the previous min_version stays.
The server goes first, so between s2 and step 4 the OLD app talks to the
NEW server: a change to the server must keep the last app working.

.env.production (git-ignored, read only here):
    SUPABASE_DB_PASSWORD=     live database password (Project Settings > Database)
    SUPABASE_ACCESS_TOKEN=    optional: a personal access token, else `supabase login`

Needs: gh (signed in to an account that can publish to Qwickly/heytora),
git, Inno Setup (build.bat finds it), Node (npx: the Supabase CLI). Windows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
REPO = "Qwickly/heytora"
SITE_AUTHOR = ["-c", "user.name=Qwickly", "-c", "user.email=307737469+Qwickly@users.noreply.github.com"]
INSTALLER = ROOT / "dist" / "Setup-HeyTora.exe"
MANIFEST_URL = "https://qwickly.github.io/heytora/latest.json"
# HeyTora's live Supabase project (account/settings.py), and the CLI pinned in tools/dev.py
PROJECT_REF = "uizwfpxzzkokyrkvjzkr"
SUPABASE = "npx --yes supabase@2.120.0"
# (function, extra deploy flags) - the same switches as supabase/config.toml
FUNCTIONS = [("heytora", []), ("paddle", ["--no-verify-jwt"])]


def run(argv, cwd=ROOT, check=True, **kw) -> subprocess.CompletedProcess:
    print("  $", " ".join(str(a) for a in argv))
    return subprocess.run(argv, cwd=cwd, check=check, text=True, encoding="utf-8", errors="replace", **kw)


def parse(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def current_version() -> str:
    text = (ROOT / "version.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([0-9.]+)"', text).group(1)


def set_version(version: str) -> None:
    """version.py and installer.iss - byte for byte, only the version."""
    p = ROOT / "version.py"
    text = p.read_bytes()
    text, n = re.subn(rb'(__version__\s*=\s*")[0-9.]+(")', rb"\g<1>" + version.encode() + rb"\g<2>", text)
    assert n == 1, "version.py: __version__ not found"
    p.write_bytes(text)
    p = ROOT / "installer.iss"
    text = p.read_bytes()
    text, n = re.subn(rb'(#define MyAppVersion\s+")[0-9.]+(")', rb"\g<1>" + version.encode() + rb"\g<2>", text)
    assert n == 1, "installer.iss: MyAppVersion not found"
    p.write_bytes(text)


def product_version(path: Path) -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"(Get-Item '{path}').VersionInfo.ProductVersion"],
                         capture_output=True, text=True)
    return out.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_out(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def same_as_github_main() -> None:
    """(a) What users get is exactly what is on GitHub's main."""
    branch = git_out("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        sys.exit(f"on {branch!r}: releases are made from main. Merge dev into main first:\n"
                 "    git switch main && git pull && git merge dev && git push")
    if git_out("status", "--porcelain", "--untracked-files=no"):
        sys.exit("uncommitted changes: commit or drop them first - a release is what is in git")
    run(["git", "fetch", "-q", "origin", "main"])
    behind = git_out("rev-list", "--count", "HEAD..origin/main")
    ahead = git_out("rev-list", "--count", "origin/main..HEAD")
    if behind != "0" or ahead != "0":
        sys.exit(f"main is {ahead} ahead / {behind} behind GitHub: `git pull` then `git push` first")


def live_env() -> dict:
    """os.environ plus .env.production's two keys - nothing else from it."""
    path = ROOT / ".env.production"
    values = dotenv_values(path) if path.exists() else {}
    env = dict(os.environ)
    for key in ("SUPABASE_DB_PASSWORD", "SUPABASE_ACCESS_TOKEN"):
        if values.get(key):
            env[key] = values[key]
    if not env.get("SUPABASE_DB_PASSWORD"):
        sys.exit("no SUPABASE_DB_PASSWORD: put the live database password in .env.production\n"
                 "(Supabase > Project Settings > Database). It never leaves this PC.")
    return env


def supabase_live(args: str, env: dict, capture: bool = False) -> subprocess.CompletedProcess:
    print(f"  $ supabase {args}")
    # npx is a .cmd on Windows: shell=True (CLAUDE.md)
    r = subprocess.run(f"{SUPABASE} {args} --project-ref {PROJECT_REF}", cwd=ROOT, shell=True, env=env,
                       text=True, encoding="utf-8", errors="replace", capture_output=capture)
    if r.returncode != 0:
        sys.exit(f"supabase {args.split()[0]} failed{': ' + (r.stderr or r.stdout)[-800:] if capture else ''}")
    return r


def live_migrations(env: dict) -> tuple[list[str], list[str]]:
    """(here but not live, live but not here) - by version, e.g. '0013'."""
    out = supabase_live("migration list --output-format json", env, capture=True).stdout
    rows = json.loads(out[out.find("{"):])["migrations"]
    missing = [r["local"] for r in rows if r.get("local") and not r.get("remote")]
    unknown = [r["remote"] for r in rows if r.get("remote") and not r.get("local")]
    return missing, unknown


def release_server(env: dict, dry_run: bool) -> None:
    print("s1. live's migrations")
    missing, unknown = live_migrations(env)
    if unknown:
        sys.exit(f"live has migration(s) {', '.join(unknown)} that supabase/migrations does not.\n"
                 "Someone changed live by hand. Add the same file here (or repair live's history) first.")
    if not missing:
        print("   live has every migration")
    else:
        files = sorted(p.name for v in missing for p in (ROOT / "supabase" / "migrations").glob(f"{v}_*.sql"))
        print("   live does not have:\n" + "".join(f"     {f}\n" for f in files), end="")
        if dry_run:
            print("   dry run: not applied")
        else:
            if input('   apply these to the LIVE database? type "yes": ').strip().lower() != "yes":
                sys.exit("stopped: nothing changed")
            supabase_live("db push --yes", env)
    print("s2. the functions")
    if dry_run:
        print("   dry run: not deployed - " + ", ".join(name for name, _ in FUNCTIONS))
        return
    for name, flags in FUNCTIONS:
        supabase_live(" ".join(["functions deploy", name, "--use-api", *flags]), env)


def check_live() -> int:
    """--check: what a release would change on the server. Changes nothing."""
    missing, unknown = live_migrations(live_env())
    print("live has every migration" if not missing else f"live does not have: {', '.join(missing)}")
    if unknown:
        print(f"live has, and supabase/migrations does not: {', '.join(unknown)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", nargs="?")
    ap.add_argument("--notes", help="what changed - shown in the app and on GitHub")
    ap.add_argument("--check", action="store_true", help="what live's database lacks; changes nothing")
    ap.add_argument("--server-only", action="store_true", help="migrations and functions; no app")
    ap.add_argument("--required", action="store_true", help="copies older than this must update")
    ap.add_argument("--min-version", default=None, help="copies older than this must update")
    ap.add_argument("--dry-run", action="store_true", help="build and check; publish nothing")
    ap.add_argument("--skip-build", action="store_true",
                    help="publish the installer already in dist (after a --dry-run), checked again")
    args = ap.parse_args()
    if args.check:
        return check_live()
    if not args.server_only and not (args.version and args.notes):
        ap.error("a version and --notes, or --server-only, or --check")

    print("a. main, the same as GitHub's")
    same_as_github_main()
    print("b. tests")
    if run([sys.executable, "-m", "pytest", "tests/agents", "-q"], check=False).returncode != 0:
        sys.exit("tests failed - nothing published")
    release_server(live_env(), args.dry_run)
    if args.server_only:
        print("done: the server is live" + (" (dry run: nothing changed)" if args.dry_run else "") + ".")
        return 0

    version, was = args.version.strip().lstrip("v"), current_version()
    if parse(version) < parse(was):
        sys.exit(f"{version} is older than {was} (version.py)")
    # The same version again is fine after a dry run or a failed run (the
    # bump is already done) - but never one that is already published.
    published = subprocess.run(["gh", "release", "view", f"v{version}", "--repo", REPO],
                               capture_output=True, text=True)
    if published.returncode == 0:
        sys.exit(f"v{version} is already published - pick the next version")

    print(f"1. version {was} -> {version}")
    set_version(version)

    if args.skip_build:
        print("2. not building: the installer already in dist, checked again")
        if not INSTALLER.exists():
            sys.exit("no dist\\Setup-HeyTora.exe - run without --skip-build")
    else:
        print("2. building - about ten minutes")
        started = time.time()
        # By its full path, and /d: a Command Processor AutoRun on this PC must
        # not move cmd somewhere build.bat isn't (it did, 1 Oct 2026).
        run(["cmd", "/d", "/c", str(ROOT / "build.bat"), "installer"])
        if not INSTALLER.exists() or INSTALLER.stat().st_mtime < started:
            sys.exit("build.bat did not make a new dist\\Setup-HeyTora.exe")
    if product_version(INSTALLER) != version:
        sys.exit(f"the installer says {product_version(INSTALLER)!r}, not {version}")
    if (ROOT / "dist" / "HeyTora" / ".env").exists():
        sys.exit("a .env is in the build - it would ship someone's keys. Not publishing.")
    digest, size = sha256(INSTALLER), INSTALLER.stat().st_size
    print(f"   Setup-HeyTora.exe {version}: {size / 2**20:.0f} MB, SHA-256 {digest}")

    url = f"https://github.com/{REPO}/releases/download/v{version}/Setup-HeyTora.exe"
    if args.dry_run:
        print("dry run: nothing published. version.py / installer.iss are left at", version)
        return 0

    print("3. GitHub release")
    with tempfile.TemporaryDirectory() as tmp:
        notes = Path(tmp) / "notes.md"
        notes.write_text(f"{args.notes.strip()}\n\nSHA-256 of Setup-HeyTora.exe: `{digest}`\n", encoding="utf-8")
        run(["gh", "release", "create", f"v{version}", str(INSTALLER), "--repo", REPO,
             "--title", f"HeyTora {version}", "--notes-file", str(notes), "--latest"])

        print("4. download site and latest.json")
        site = Path(tmp) / "site"
        run(["gh", "repo", "clone", REPO, str(site), "--", "--depth", "1"])
        page = site / "index.html"
        html = page.read_text(encoding="utf-8")
        html = re.sub(r"Version [0-9.]+ · \d+ MB", f"Version {version} · {round(size / 2**20)} MB", html)
        html = re.sub(r"test build [0-9.]+", f"test build {version}", html)
        html = re.sub(r"(SHA-256 of Setup-HeyTora\.exe: <code>)[0-9a-f]{64}(</code>)", rf"\g<1>{digest}\g<2>", html)
        page.write_text(html, encoding="utf-8", newline="")
        manifest = site / "latest.json"
        before = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
        min_version = version if args.required else (args.min_version or before.get("min_version", ""))
        manifest.write_text(json.dumps({
            "version": version, "url": url, "sha256": digest, "size": size,
            "notes": args.notes.strip(), "min_version": min_version,
            "published": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }, indent=2) + "\n", encoding="utf-8")
        run(["git", *SITE_AUTHOR, "add", "index.html", "latest.json"], cwd=site)
        run(["git", *SITE_AUTHOR, "commit", "-q", "-m", f"HeyTora {version}: {args.notes.strip()[:60]}"], cwd=site)
        run(["git", "push", "-q", "origin", "HEAD"], cwd=site)

    print("5. committing the version here")
    run(["git", "add", "version.py", "installer.iss"])
    run(["git", "commit", "-q", "-m", f"[mgr] Release HeyTora {version}", "-m", args.notes.strip()], check=False)

    print("   waiting for the site to publish latest.json ...")
    for _ in range(36):                       # GitHub Pages takes a minute or two
        try:
            with urllib.request.urlopen(f"{MANIFEST_URL}?t={int(time.time())}", timeout=10) as r:
                if json.loads(r.read().decode()).get("version") == version:
                    print(f"done: every installed HeyTora will update to {version}"
                          + (" - and must, before it keeps working" if args.required else "") + ".")
                    return 0
        except Exception:  # noqa: BLE001 - not there yet
            pass
        time.sleep(5)
    print("published - latest.json is not live yet; give GitHub Pages a few minutes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
