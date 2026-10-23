"""Secrets out, before anything is kept or sent.

Code, terminals and logs routinely hold keys: a `.env` printed by a script,
a token in a curl command, a connection string in a stack trace. The design
puts redaction at the door - it runs on every line before the Log Hub or
the timeline stores it, and again on every packet before it goes to the
model - so no later step has to remember to.

Three nets, coarse to fine:
  1. Files that are secrets by nature (`.env*`, `*.pem`, `id_rsa*`, key
     stores, a project's `.heytoraignore`) are never read at all.
  2. Known token shapes (gitleaks-style rules: Google, OpenAI, Anthropic,
     GitHub, Slack, Stripe, AWS, Supabase, JWTs, private key blocks) and
     `password=` / `secret:` style assignments are replaced.
  3. A long, random-looking value assigned to anything is replaced too:
     an unknown provider's key still has the entropy of a key.
"""
from __future__ import annotations

import fnmatch
import math
import os
import re
from pathlib import Path, PurePath
from typing import Iterable

from logsafe import SECRET_PATTERNS as _LOG_PATTERNS

MASK = "[REDACTED]"

_RULES: list[tuple[re.Pattern[str], str]] = [
    # A private key block, whole.
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
     "-----BEGIN PRIVATE KEY----- " + MASK),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), "sk-ant-" + MASK),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), "sk-" + MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "AIza" + MASK),
    (re.compile(r"\b(gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}"), r"\1_" + MASK),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"), "xox-" + MASK),
    (re.compile(r"\b(sk|rk|pk)_(live|test)_[A-Za-z0-9]{16,}"), r"\1_\2_" + MASK),
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), r"\1" + MASK),
    (re.compile(r"\bsbp_[A-Za-z0-9]{20,}"), "sbp_" + MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "eyJ" + MASK),
    # A password inside a URL: scheme://user:password@host
    (re.compile(r"(\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:)[^\s@/]+(@)", re.I), r"\1" + MASK + r"\2"),
    # password = "...", api_key: ..., SECRET_TOKEN=...
    (re.compile(r"((?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
                r"client[_-]?secret|auth)[A-Za-z0-9_]*['\"]?\s*[:=]\s*['\"]?)([^\s'\",;]{6,})", re.I),
     r"\1" + MASK),
]

# Any assignment's value, checked for randomness: NAME=value, "name": "value".
_ASSIGNED = re.compile(r"""([A-Za-z_][A-Za-z0-9_\-]*['"]?\s*[:=]\s*['"]?)([A-Za-z0-9+/=_\-\.]{24,})""")
ENTROPY_BITS = 4.0           # per character: hex is 4.0 at most, base64 keys run 5+


def _entropy(value: str) -> float:
    counts = {c: value.count(c) for c in set(value)}
    n = len(value)
    return -sum(k / n * math.log2(k / n) for k in counts.values())


def _random_looking(value: str) -> bool:
    if value.isdigit() or value.isalpha() or "/" in value.strip("/") and value.count("/") > 2:
        return False                 # a number, a word, a path
    return _entropy(value) >= ENTROPY_BITS and any(c.isdigit() for c in value)


def redact(text: str) -> str:
    """`text` with anything that looks like a secret replaced by MASK."""
    if not text:
        return text
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    for pattern, repl in _LOG_PATTERNS:
        text = pattern.sub(repl, text)
    return _ASSIGNED.sub(lambda m: m.group(1) + MASK if _random_looking(m.group(2)) else m.group(0), text)


# ── Files that are never read ──────────────────────────────────────────────

SECRET_FILES = (".env", ".env.*", "*.env", "*.pem", "*.key", "*.pfx", "*.p12", "*.jks", "*.keystore",
                "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.kdbx", "credentials", "credentials.*",
                ".npmrc", ".pypirc", ".netrc", "_netrc", "*.ppk", "secrets.*", "*.secret", "*.secrets")
SECRET_DIRS = (".ssh", ".gnupg", ".aws", ".azure", ".kube")
IGNORE_FILE = ".heytoraignore"


def is_secret_path(path: str) -> bool:
    """Is this a file HeyTora never reads, whatever is granted?"""
    if not path:
        return False
    parts = PurePath(path.replace("\\", "/")).parts
    name = parts[-1].lower() if parts else ""
    if any(p.lower() in SECRET_DIRS for p in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in SECRET_FILES)


def ignore_rules(folders: Iterable[str]) -> list[tuple[str, str]]:
    """(folder, pattern) from each workspace folder's .heytoraignore -
    gitignore-style lines; blank lines and # comments skipped."""
    rules = []
    for folder in folders:
        try:
            text = (Path(folder) / IGNORE_FILE).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                rules.append((folder, line.lstrip("/")))
    return rules


def is_ignored(path: str, rules: list[tuple[str, str]]) -> bool:
    """Does a .heytoraignore pattern cover `path`?"""
    if not rules or not path:
        return False
    full = os.path.normcase(os.path.abspath(path))
    for folder, pattern in rules:
        root = os.path.normcase(os.path.abspath(folder))
        if not full.startswith(root + os.sep):
            continue
        rel = full[len(root) + 1:].replace("\\", "/")
        pat = os.path.normcase(pattern).replace("\\", "/").rstrip("/")
        if (fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat + "/*")
                or any(fnmatch.fnmatch(part, pat) for part in rel.split("/"))):
            return True
    return False
