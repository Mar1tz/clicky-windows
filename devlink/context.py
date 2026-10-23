"""The Context Engine: what the user is working on, right now.

Three layers, as the design has them:

  hot state     in memory, rebuilt from the connector's events: the active
                file and cursor, the selection, open tabs, unsaved files,
                errors and warnings, git, running tasks, the debugger's stop.
                Never written to disk.
  project       read from the workspace's own files when it connects: the
                stack (package.json, pyproject.toml, go.mod, ...), the
                package manager from its lock file, how tests run, CI. The
                "project memory" a developer would otherwise have to say.
  timeline      the store's rolling log of what happened ("tests failed in
                auth.spec.ts", "switched to feature/billing").

A request gets a packet, not the repo: sections in order of how much they
matter, cut to a budget, with the model told it can ask for more (a file,
the references to a symbol, a log) by name.
"""
from __future__ import annotations

import json
import os
import re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .redact import is_ignored, is_secret_path, redact

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "out", "target",
             ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache", "coverage", ".idea", ".vs", "bin", "obj",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "vendor", ".gradle", "Pods"}
INDEX_FILE_LIMIT = 20_000
SEARCH_FILE_BYTES = 1_000_000
SEVERITY = {0: "error", 1: "warning", 2: "info", 3: "hint", "error": "error", "warning": "warning",
            "information": "info", "info": "info", "hint": "hint"}


@dataclass
class HotState:
    workspace_id: str
    name: str = ""
    folders: list[str] = field(default_factory=list)
    app: str = ""
    connected: bool = False
    focused_at: float = 0.0
    active: dict = field(default_factory=dict)            # path, language, line, character, dirty, line_count
    selection: str = ""
    visible: list = field(default_factory=list)
    tabs: list = field(default_factory=list)
    dirty: set = field(default_factory=set)
    diagnostics: dict = field(default_factory=dict)       # path -> [ {severity, message, line, ...} ]
    git: dict = field(default_factory=dict)               # repo root -> state
    tasks: dict = field(default_factory=dict)             # name -> {running, exit_code, at}
    terminals: dict = field(default_factory=dict)         # terminal id -> {name, command, running, exit_code}
    debug: dict = field(default_factory=dict)             # session -> {name, type, stopped}
    updated: float = 0.0

    def rel(self, path: str) -> str:
        """`path` relative to its workspace folder, for the reader."""
        if not path:
            return ""
        full = os.path.normcase(os.path.abspath(path))
        for folder in self.folders:
            root = os.path.normcase(os.path.abspath(folder))
            if full.startswith(root + os.sep):
                rel = os.path.abspath(path)[len(os.path.abspath(folder)) + 1:].replace("\\", "/")
                return (Path(folder).name + "/" + rel) if len(self.folders) > 1 else rel
        return path.replace("\\", "/")

    def counts(self) -> tuple[int, int]:
        errors = warnings = 0
        for items in self.diagnostics.values():
            for d in items:
                errors += d["severity"] == "error"
                warnings += d["severity"] == "warning"
        return errors, warnings


class ContextEngine:
    """Hot state per workspace, kept from events; the timeline lives in the
    store and is written by the hub."""

    def __init__(self):
        self.states: dict[str, HotState] = {}
        self.projects: dict[str, dict] = {}

    def state(self, ws: str) -> HotState:
        return self.states.setdefault(ws, HotState(ws))

    def apply(self, ws: str, kind: str, p: dict) -> Optional[tuple[str, str, dict]]:
        """An event into the hot state. A (kind, summary, data) for the
        timeline when it is worth remembering, else None."""
        s = self.state(ws)
        s.updated = time.time()
        if kind == "window.focused":
            s.focused_at = time.time()
        elif kind in ("editor.active", "editor.selection"):
            path = str(p.get("path") or "")
            if not path:
                if kind == "editor.active":
                    s.active, s.selection = {}, ""
            elif is_secret_path(path):
                s.active, s.selection = {"path": path, "secret": True}, ""      # named, never read
            else:
                kept = dict(s.active) if s.active.get("path") == path else {}
                s.active = kept | {k: p[k] for k in ("path", "language", "line", "character", "dirty", "line_count")
                                   if p.get(k) is not None}
                s.selection = redact(str(p.get("text") or ""))[:2000] if kind == "editor.selection" else ""
        elif kind == "editor.visible":
            s.visible = p.get("ranges") or []
        elif kind == "tabs.changed":
            s.tabs = [t for t in (p.get("tabs") or []) if isinstance(t, dict)][:60]
        elif kind == "document.changed":
            path = p.get("path") or ""
            (s.dirty.add if p.get("dirty") else s.dirty.discard)(path)
        elif kind == "document.saved":
            s.dirty.discard(p.get("path") or "")
        elif kind == "diagnostics.changed":
            return self._diagnostics(s, p)
        elif kind == "git.changed":
            return self._git(s, p)
        elif kind == "task.started":
            s.tasks[str(p.get("name"))] = {"running": True, "at": time.time()}
        elif kind == "task.ended":
            s.tasks[str(p.get("name"))] = {"running": False, "exit_code": p.get("exitCode"), "at": time.time()}
        elif kind == "terminal.started":
            s.terminals[str(p.get("terminal"))] = {"name": p.get("name"), "command": redact(str(p.get("command") or "")),
                                                   "running": True}
        elif kind == "terminal.ended":
            t = s.terminals.setdefault(str(p.get("terminal")), {"name": p.get("name")})
            t.update(running=False, exit_code=p.get("exitCode"))
        elif kind == "debug.started":
            s.debug[str(p.get("session"))] = {"name": p.get("name"), "type": p.get("type"), "stopped": None}
        elif kind == "debug.stopped":
            d = s.debug.setdefault(str(p.get("session")), {"name": p.get("name")})
            d["stopped"] = {k: p.get(k) for k in ("reason", "path", "line", "frame", "description") if p.get(k)}
            where = f"{s.rel(p.get('path') or '')}:{(p.get('line') or 0) + 1}" if p.get("path") else "an unknown place"
            return ("debug.stopped", f"debugger stopped at {where} ({p.get('reason') or 'paused'})", d["stopped"])
        elif kind == "debug.ended":
            s.debug.pop(str(p.get("session")), None)
        return None

    def _diagnostics(self, s: HotState, p: dict) -> Optional[tuple[str, str, dict]]:
        before = s.counts()
        if p.get("full"):
            s.diagnostics = {}
        for f in p.get("files") or []:
            path = f.get("path") or ""
            if not path or is_secret_path(path):
                continue
            items = [{"severity": SEVERITY.get(d.get("severity"), "info"), "message": redact(str(d.get("message", "")))[:400],
                      "line": d.get("line"), "character": d.get("character"), "source": d.get("source"),
                      "code": str(d.get("code")) if d.get("code") not in (None, "") else None}
                     for d in (f.get("items") or [])[:200] if isinstance(d, dict)]
            if items:
                s.diagnostics[path] = items
            else:
                s.diagnostics.pop(path, None)
        after = s.counts()
        if after[0] != before[0]:
            worst = next(((path, d) for path, items in s.diagnostics.items() for d in items
                          if d["severity"] == "error"), None)
            summary = f"{after[0]} error{'s' if after[0] != 1 else ''} in the problems" if after[0] else "no errors left"
            if worst:
                summary += f"; e.g. {s.rel(worst[0])}:{(worst[1]['line'] or 0) + 1}: {worst[1]['message'][:120]}"
            return ("problems", summary, {"errors": after[0], "warnings": after[1], "was": before[0]})
        return None

    def _git(self, s: HotState, p: dict) -> Optional[tuple[str, str, dict]]:
        root = p.get("root") or ""
        old = s.git.get(root, {})
        s.git[root] = {"branch": p.get("branch"), "commit": p.get("commit"), "ahead": p.get("ahead"),
                       "behind": p.get("behind"), "changes": (p.get("changes") or [])[:200],
                       "staged": (p.get("staged") or [])[:200], "conflicts": (p.get("conflicts") or [])[:50]}
        if old and old.get("branch") != p.get("branch") and p.get("branch"):
            return ("git.branch", f"switched branch to {p['branch']}", {"from": old.get("branch"), "to": p["branch"]})
        if old and old.get("commit") != p.get("commit") and p.get("commit") and old.get("branch") == p.get("branch"):
            return ("git.commit", f"new commit {p['commit']} on {p.get('branch')}", {"commit": p["commit"]})
        return None


# ── The project, from its own files ────────────────────────────────────────

_LOCKS = (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun"),
          ("package-lock.json", "npm"), ("uv.lock", "uv"), ("poetry.lock", "poetry"), ("Pipfile.lock", "pipenv"),
          ("Cargo.lock", "cargo"), ("go.sum", "go modules"), ("composer.lock", "composer"), ("Gemfile.lock", "bundler"))
_FRAMEWORKS = ("next", "react", "vue", "nuxt", "svelte", "@sveltejs/kit", "@angular/core", "express", "fastify",
               "vite", "electron", "react-native", "expo", "nestjs", "@nestjs/core", "astro", "remix", "tailwindcss",
               "django", "flask", "fastapi", "pyqt6", "pyside6", "streamlit", "pytest", "jest", "vitest", "playwright")


def index_project(folders: list[str]) -> dict:
    """What a developer would tell a new colleague first: the stack, the
    package manager, how tests and builds run, where CI is. From files only."""
    out: dict[str, Any] = {"stack": [], "frameworks": [], "package_managers": [], "scripts": {}, "notes": [],
                           "packages": [], "files": 0, "languages": {}}
    for folder in folders:
        root = Path(folder)
        for manifest in _manifests(root):
            _read_manifest(manifest, out, root)
        for lock, manager in _LOCKS:
            if (root / lock).exists() and manager not in out["package_managers"]:
                out["package_managers"].append(manager)
        if (root / ".github" / "workflows").is_dir():
            out["notes"].append("CI: GitHub Actions")
        if (root / ".gitlab-ci.yml").exists():
            out["notes"].append("CI: GitLab CI")
        if (root / "Dockerfile").exists() or (root / "docker-compose.yml").exists() or (root / "compose.yaml").exists():
            out["notes"].append("Docker")
        if (root / "pytest.ini").exists() or (root / "conftest.py").exists():
            out["scripts"].setdefault("test", "pytest")
        _count_files(root, out)
    langs = sorted(out["languages"].items(), key=lambda kv: -kv[1])[:6]
    out["languages"] = dict(langs)
    for key in ("stack", "frameworks", "notes"):
        out[key] = list(dict.fromkeys(out[key]))
    return out


def _manifests(root: Path) -> list[Path]:
    """The root's manifests, and a monorepo's packages one or two levels down."""
    names = ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml", "pom.xml",
             "build.gradle", "build.gradle.kts", "composer.json", "Gemfile")
    found = [root / n for n in names if (root / n).is_file()]
    found += sorted(root.glob("*.csproj")) + sorted(root.glob("*.sln"))
    for group in ("packages", "apps", "libs", "services"):
        if (root / group).is_dir():
            found += sorted(p for p in (root / group).glob("*/package.json"))[:30]
            found += sorted(p for p in (root / group).glob("*/pyproject.toml"))[:30]
    return found


def _read_manifest(path: Path, out: dict, root: Path) -> None:
    name = path.name
    nested = path.parent != root
    try:
        if name == "package.json":
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            out["stack"].append("Node.js" + (" / TypeScript" if (path.parent / "tsconfig.json").exists() else ""))
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            out["frameworks"] += [f for f in _FRAMEWORKS if f in deps]
            if nested:
                out["packages"].append(str(path.parent.relative_to(root)).replace("\\", "/") + (f" ({data['name']})" if data.get("name") else ""))
            elif isinstance(data.get("scripts"), dict):
                out["scripts"].update({k: str(v)[:120] for k, v in data["scripts"].items()
                                       if k in ("dev", "start", "build", "test", "lint", "typecheck", "format")})
            if isinstance(data.get("workspaces"), (list, dict)):
                out["notes"].append("monorepo (workspaces)")
        elif name == "pyproject.toml":
            data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
            out["stack"].append("Python")
            deps = " ".join(map(str, (data.get("project") or {}).get("dependencies") or [])).lower()
            deps += " ".join((data.get("tool", {}).get("poetry", {}).get("dependencies") or {}).keys()).lower()
            out["frameworks"] += [f for f in _FRAMEWORKS if re.search(rf"\b{re.escape(f)}\b", deps)]
            if "pytest" in (data.get("tool") or {}):
                out["scripts"].setdefault("test", "pytest")
            if nested:
                out["packages"].append(str(path.parent.relative_to(root)).replace("\\", "/"))
        elif name == "requirements.txt":
            out["stack"].append("Python")
            deps = path.read_text(encoding="utf-8", errors="replace").lower()
            out["frameworks"] += [f for f in _FRAMEWORKS if re.search(rf"^{re.escape(f)}\b", deps, re.M)]
        elif name == "go.mod":
            out["stack"].append("Go")
        elif name == "Cargo.toml":
            out["stack"].append("Rust")
        elif name in ("pom.xml", "build.gradle", "build.gradle.kts"):
            out["stack"].append("Java/Kotlin (" + ("Maven" if name == "pom.xml" else "Gradle") + ")")
        elif name.endswith((".csproj", ".sln")):
            out["stack"].append(".NET")
        elif name == "composer.json":
            out["stack"].append("PHP")
        elif name == "Gemfile":
            out["stack"].append("Ruby")
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        pass


def _count_files(root: Path, out: dict) -> None:
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext:
                out["languages"][ext] = out["languages"].get(ext, 0) + 1
            n += 1
        if n >= INDEX_FILE_LIMIT:
            break
    out["files"] += n


def search_code(folders: list[str], query: str, *, rules: list, limit: int = 30,
                seconds: float = 3.0) -> list[dict]:
    """Lines in the workspace's files that hold every word of `query`
    (case-insensitive) - what is on disk, secrets and ignored files skipped."""
    words = [w.lower() for w in query.split() if w]
    if not words:
        return []
    hits, deadline = [], time.monotonic() + seconds
    for folder in folders:
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for f in filenames:
                path = os.path.join(dirpath, f)
                if time.monotonic() > deadline or len(hits) >= limit:
                    return hits
                if is_secret_path(path) or is_ignored(path, rules):
                    continue
                try:
                    if os.path.getsize(path) > SEARCH_FILE_BYTES:
                        continue
                    with open(path, "rb") as fh:
                        raw = fh.read()
                except OSError:
                    continue
                if b"\0" in raw[:1024]:
                    continue
                text = raw.decode("utf-8", errors="replace")
                low = text.lower()
                if not all(w in low for w in words):
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    l = line.lower()
                    if all(w in l for w in words) or (len(words) > 1 and words[0] in l):
                        hits.append({"path": path, "line": i, "text": redact(line.strip())[:240]})
                        if len(hits) >= limit:
                            return hits
    return hits


# ── The packet ─────────────────────────────────────────────────────────────

def fit(sections: list[tuple[str, str]], budget: int) -> str:
    """Sections in the order given (most important first), each whole or
    cut short; what does not fit is named so the model can ask for it."""
    out, left, dropped = [], budget, []
    for title, body in sections:
        if not body:
            continue
        block = f"## {title}\n{body.rstrip()}\n"
        if len(block) <= left:
            out.append(block)
            left -= len(block)
        elif left > 400:
            out.append(block[:left - 40].rstrip() + "\n… (cut short)\n")
            left = 0
        else:
            dropped.append(title)
    if dropped:
        out.append("Not included (ask for them): " + ", ".join(dropped) + "\n")
    return "\n".join(out)
