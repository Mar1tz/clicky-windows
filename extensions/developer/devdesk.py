"""A developer's desk: the checks around the code, read from the repo and CI.

    <root>/developer.json  default repo, what counts as a big change, stale issue age
    <root>/Release notes  Reviews  Triage

Everything here reads: git (log, diff, grep) run locally, a JUnit XML test
report, a GitHub issues export. From them, with no model: release notes
grouped from conventional commits and the next semantic version; a review
of a change that flags secrets, debug leftovers, source changed without
tests and changes too big to review; TODOs; loosely pinned dependencies;
failing tests grouped by cause; issues sorted into likely labels,
duplicates and stale. Nothing is pushed, merged, tagged or labelled.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Any

from extensions.packkit import Store, slug

CONVENTIONAL = re.compile(r"^(?P<type>feat|fix|perf|refactor|docs|test|build|ci|chore|style|revert)"
                          r"(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?:\s*(?P<desc>.+)$", re.I)
SECTIONS = [("breaking", "Breaking changes"), ("feat", "Features"), ("fix", "Fixes"), ("perf", "Performance"),
            ("other", "Other changes")]
SECRETS = [
    ("an AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("a private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("a Google API key", re.compile("AI" + r"za[0-9A-Za-z_\-]{35}")),
    ("a GitHub token", re.compile(r"gh[pousr]_[0-9A-Za-z]{36,}")),
    ("a hard-coded password or key", re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*"
                                               r"['\"][^'\"\s]{6,}['\"]")),
]
LEFTOVERS = re.compile(r"^\s*(?:print\(|console\.log\(|breakpoint\(\)|debugger;|import pdb|pdb\.set_trace)")
LABEL_WORDS = {
    "bug": ["error", "crash", "fails", "failing", "broken", "exception", "traceback", "doesn't work", "does not work",
            "bug", "regression"],
    "feature": ["feature request", "would be nice", "add support", "please add", "allow", "support for", "enhancement"],
    "docs": ["docs", "documentation", "readme", "typo", "example"],
    "question": ["how do i", "how to", "is it possible", "question"],
}


def git(repo: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        raise OSError("git is not installed") from None
    if r.returncode != 0:
        raise ValueError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout


def _is_test(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    return "/test" in f"/{p}" or p.endswith(("_test.py", ".test.js", ".test.ts", ".spec.ts", ".spec.js", "_test.go"))


class DevDesk(Store):
    SETTINGS_FILE = "developer.json"
    DB = "developer.sqlite"
    FOLDERS = ("Release notes", "Reviews", "Triage")
    SCHEMA = ""

    def repo(self, path: str = "") -> str:
        p = (path or self.cfg.get("repo") or "").strip()
        if not p:
            raise ValueError("which repository? Give its folder, or set the default repo")
        if not os.path.isdir(p):
            raise FileNotFoundError(f"no folder {p}")
        return git(p, "rev-parse", "--show-toplevel").strip()

    # -- history and releases ---------------------------------------------------------------------------------

    def last_tag(self, repo: str) -> str:
        try:
            return git(repo, "describe", "--tags", "--abbrev=0").strip()
        except ValueError:
            return ""

    def commits(self, repo: str, since: str = "", until: str = "HEAD") -> list[dict[str, Any]]:
        rng = f"{since}..{until}" if since else until
        out = git(repo, "log", "--no-merges", "--format=%H%x1f%an%x1f%ad%x1f%s%x1f%b%x1e", "--date=short", rng)
        rows = []
        for rec in out.split("\x1e"):
            parts = rec.strip("\n").split("\x1f")
            if len(parts) < 5:
                continue
            sha, author, when, subject, body = parts
            m = CONVENTIONAL.match(subject.strip())
            kind = m.group("type").lower() if m else ""
            breaking = bool(m and m.group("bang")) or "BREAKING CHANGE" in body
            rows.append({"sha": sha[:8], "author": author, "date": when, "subject": subject.strip(),
                         "type": kind, "scope": (m.group("scope") if m else "") or "",
                         "text": (m.group("desc") if m else subject).strip(), "breaking": breaking})
        return rows

    def next_version(self, repo: str, since: str = "") -> dict[str, Any]:
        tag = since or self.last_tag(repo)
        m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", tag or "")
        major, minor, patch = (int(x) for x in m.groups()) if m else (0, 0, 0)
        cs = self.commits(repo, tag)
        if any(c["breaking"] for c in cs):
            bump = "major" if major else "minor"          # before 1.0 a break moves the minor number
        elif any(c["type"] == "feat" for c in cs):
            bump = "minor"
        elif any(c["type"] in ("fix", "perf") for c in cs):
            bump = "patch"
        else:
            bump = ""
        new = {"major": (major + 1, 0, 0), "minor": (major, minor + 1, 0), "patch": (major, minor, patch + 1)}.get(
            bump, (major, minor, patch))
        prefix = "v" if (tag or "v").startswith("v") else ""
        return {"since": tag, "commits": len(cs), "bump": bump, "current": tag or "(none)",
                "next": f"{prefix}{new[0]}.{new[1]}.{new[2]}" if bump else "", "log": cs}

    def release_notes(self, repo: str, since: str = "", until: str = "HEAD", version: str = "") -> dict[str, Any]:
        v = self.next_version(repo, since)
        cs = self.commits(repo, v["since"], until)
        if not cs:
            raise ValueError(f"no commits since {v['since'] or 'the start'}")
        groups: dict[str, list[str]] = {k: [] for k, _ in SECTIONS}
        for c in cs:
            line = (f"**{c['scope']}:** " if c["scope"] else "") + c["text"] + f" ({c['sha']})"
            if c["breaking"]:
                groups["breaking"].append(line)
            if c["type"] in ("feat", "fix", "perf"):
                groups[c["type"]].append(line)
            elif not c["breaking"] and c["type"] not in ("docs", "test", "ci", "chore", "style", "build"):
                groups["other"].append(line)
        name = version or v["next"] or "Unreleased"
        lines = [f"## {name} ({date.today().isoformat()})", ""]
        for key, title in SECTIONS:
            if groups[key]:
                lines += [f"### {title}", ""] + [f"- {x}" for x in groups[key]] + [""]
        path = os.path.join(self.folder("Release notes"), f"{slug(os.path.basename(repo))} {slug(name)}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return {"version": name, "path": path, "counts": {k: len(g) for k, g in groups.items()},
                "commits": len(cs), "left_out": sum(1 for c in cs if c["type"] in ("docs", "test", "ci", "chore",
                                                                                     "style", "build") and not c["breaking"])}

    # -- reviewing a change -------------------------------------------------------------------------------------

    def review(self, repo: str, base: str = "", head: str = "HEAD") -> dict[str, Any]:
        """base..head, or with no base the uncommitted work against HEAD."""
        rng = [f"{base}...{head}"] if base else ["HEAD"]
        stats = git(repo, "diff", "--numstat", *rng)
        files, added_total, removed_total, binary = [], 0, 0, []
        for line in stats.splitlines():
            a, r, path = line.split("\t", 2)
            if a == "-":
                binary.append(path)
                continue
            files.append({"path": path, "added": int(a), "removed": int(r)})
            added_total += int(a)
            removed_total += int(r)
        patch = git(repo, "diff", "-U0", *rng)
        secrets, leftovers, current = [], [], ""
        for line in patch.splitlines():
            if line.startswith("+++ "):
                current = line[6:] if line.startswith("+++ b/") else line[4:]
            elif line.startswith("+") and not line.startswith("+++"):
                text = line[1:]
                for what, pat in SECRETS:
                    if pat.search(text):
                        secrets.append({"file": current, "what": what})
                if LEFTOVERS.match(text) and not _is_test(current):
                    leftovers.append({"file": current, "line": text.strip()[:80]})
        src = [f["path"] for f in files if not _is_test(f["path"]) and f["path"].endswith(
            (".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".cs", ".rb", ".php", ".kt"))]
        tests = [f["path"] for f in files if _is_test(f["path"])]
        big = int(self.cfg.get("big_change") or 400)
        return {"range": f"{base}...{head}" if base else "uncommitted", "files": files, "binary": binary,
                "added": added_total, "removed": removed_total, "secrets": secrets, "leftovers": leftovers,
                "untested": src if src and not tests else [], "too_big": added_total + removed_total > big}

    def todos(self, repo: str) -> dict[str, Any]:
        try:
            out = git(repo, "grep", "-n", "-I", "-E", r"\b(TODO|FIXME|HACK|XXX)\b")
        except ValueError:
            out = ""                                      # git grep exits 1 when nothing matches
        rows = []
        for line in out.splitlines():
            path, num, text = (line.split(":", 2) + ["", ""])[:3]
            tag = re.search(r"\b(TODO|FIXME|HACK|XXX)\b", text)
            rows.append({"file": path, "line": int(num) if num.isdigit() else 0, "tag": tag.group(1) if tag else "",
                         "text": text.strip()[:100]})
        by_tag: dict[str, int] = {}
        for r in rows:
            by_tag[r["tag"]] = by_tag.get(r["tag"], 0) + 1
        return {"count": len(rows), "by_tag": by_tag, "items": rows}

    def dependencies(self, repo: str) -> dict[str, Any]:
        loose, seen, dupes = [], {}, []
        for name in sorted(os.listdir(repo)):
            if re.fullmatch(r"requirements.*\.txt", name):
                with open(os.path.join(repo, name), encoding="utf-8", errors="replace") as fh:
                    for n, raw in enumerate(fh, 1):
                        line = raw.split("#", 1)[0].strip()
                        if not line or line.startswith(("-", "git+", "http")):
                            continue
                        pkg = re.split(r"[<>=!~\[; ]", line, 1)[0].lower()
                        if pkg in seen:
                            dupes.append({"file": name, "package": pkg, "also": seen[pkg]})
                        seen[pkg] = f"{name}:{n}"
                        if "==" not in line:
                            loose.append({"file": name, "package": pkg, "spec": line,
                                          "why": "no version" if not re.search(r"[<>=~]", line) else "a range, not a pin"})
        pj = os.path.join(repo, "package.json")
        if os.path.isfile(pj):
            with open(pj, encoding="utf-8") as fh:
                data = json.load(fh)
            for section in ("dependencies", "devDependencies"):
                for pkg, spec in (data.get(section) or {}).items():
                    if spec in ("*", "latest", "") or spec.startswith(("git", "http")):
                        loose.append({"file": "package.json", "package": pkg, "spec": spec, "why": "any version"})
        return {"loose": loose, "duplicates": dupes, "checked": len(seen)}

    # -- tests and issues ----------------------------------------------------------------------------------------

    def test_report(self, path: str) -> dict[str, Any]:
        """A JUnit XML report (pytest --junitxml, Jest, Maven, most CI): failures grouped by cause."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no file {path}")
        root = ET.parse(path).getroot()
        cases = list(root.iter("testcase"))
        groups: dict[str, dict[str, Any]] = {}
        skipped = 0
        for c in cases:
            bad = c.find("failure") if c.find("failure") is not None else c.find("error")
            if c.find("skipped") is not None:
                skipped += 1
            if bad is None:
                continue
            text = (bad.text or "").strip()
            msg = (bad.get("message") or "").strip() or (text.splitlines()[-1] if text else "")
            first = msg.splitlines()[0][:160] if msg else ""
            kind = bad.get("type") or (first.split(":", 1)[0] if ":" in first else "") or bad.tag
            g = groups.setdefault(f"{kind}|{first}", {"type": kind, "message": first, "tests": []})
            g["tests"].append(f"{c.get('classname', '')}::{c.get('name', '')}".strip(":"))
        failed = sum(len(g["tests"]) for g in groups.values())
        return {"tests": len(cases), "failed": failed, "skipped": skipped, "passed": len(cases) - failed - skipped,
                "groups": sorted(groups.values(), key=lambda g: -len(g["tests"]))}

    def triage(self, path: str, as_of: str = "") -> dict[str, Any]:
        """A GitHub issues export (gh issue list --json number,title,body,labels,updatedAt)."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no file {path}")
        with open(path, encoding="utf-8") as fh:
            issues = json.load(fh)
        if not isinstance(issues, list):
            raise ValueError("expected a list of issues (gh issue list --json ...)")
        today = date.fromisoformat(as_of[:10]) if as_of else datetime.now(timezone.utc).date()
        stale_days = int(self.cfg.get("stale_days") or 60)
        suggest, stale, dupes = [], [], []
        for i in issues:
            labels = [(x.get("name") if isinstance(x, dict) else str(x)) for x in i.get("labels") or []]
            text = f"{i.get('title', '')} {i.get('body', '')}".lower()
            if not labels:
                guess = next((lab for lab, words in LABEL_WORDS.items() if any(w in text for w in words)), "")
                suggest.append({"number": i.get("number"), "title": i.get("title", ""), "label": guess or "needs a look"})
            upd = str(i.get("updatedAt") or "")[:10]
            if upd and (today - date.fromisoformat(upd)).days > stale_days:
                stale.append({"number": i.get("number"), "title": i.get("title", ""), "days": (today - date.fromisoformat(upd)).days})
        for a_i, a in enumerate(issues):
            for b in issues[a_i + 1:]:
                ratio = difflib.SequenceMatcher(None, str(a.get("title", "")).lower(), str(b.get("title", "")).lower()).ratio()
                if ratio >= float(self.cfg.get("duplicate_ratio") or 0.8):
                    dupes.append({"a": a.get("number"), "b": b.get("number"), "similarity": round(ratio, 2)})
        report = [f"# Issue triage {today.isoformat()}", "", f"{len(issues)} issues.", "", "## Suggested labels", ""]
        report += [f"- #{s['number']} {s['title']}: **{s['label']}**" for s in suggest] + ["", "## Possible duplicates", ""]
        report += [f"- #{d['a']} and #{d['b']} ({d['similarity']:.0%} alike)" for d in dupes] + ["", "## Stale", ""]
        report += [f"- #{s['number']} {s['title']} ({s['days']} days quiet)" for s in stale]
        out = os.path.join(self.folder("Triage"), f"{today.isoformat()} issues.md")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(report) + "\n")
        return {"issues": len(issues), "suggest": suggest, "duplicates": dupes, "stale": stale, "path": out}
