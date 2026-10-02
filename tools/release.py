"""Publish a new HeyTora - every installed copy updates itself to it.

    python tools/release.py 1.6.5 --notes "What changed, in a sentence or two"
    python tools/release.py 1.6.6 --notes "..." --required      # old copies must update
    python tools/release.py 1.6.5 --notes "..." --dry-run       # build and check, publish nothing

In order - each step only after the one before it worked:

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

Needs: gh (signed in to an account that can publish to Qwickly/heytora),
git, Inno Setup (build.bat finds it). Windows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "Qwickly/heytora"
SITE_AUTHOR = ["-c", "user.name=Qwickly", "-c", "user.email=307737469+Qwickly@users.noreply.github.com"]
INSTALLER = ROOT / "dist" / "Setup-HeyTora.exe"
MANIFEST_URL = "https://qwickly.github.io/heytora/latest.json"


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version")
    ap.add_argument("--notes", required=True, help="what changed - shown in the app and on GitHub")
    ap.add_argument("--required", action="store_true", help="copies older than this must update")
    ap.add_argument("--min-version", default=None, help="copies older than this must update")
    ap.add_argument("--dry-run", action="store_true", help="build and check; publish nothing")
    args = ap.parse_args()

    version, was = args.version.strip().lstrip("v"), current_version()
    if parse(version) <= parse(was):
        sys.exit(f"{version} is not newer than {was} (version.py)")

    print(f"1. version {was} -> {version}")
    set_version(version)

    print("2. building - about ten minutes")
    started = time.time()
    run(["cmd", "/c", "build.bat", "installer"])
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
