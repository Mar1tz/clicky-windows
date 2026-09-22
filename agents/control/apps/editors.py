"""Code editors: VS Code, Cursor, Windsurf, Antigravity - all VS Code underneath.

"Open this project in VS Code and ask the chat to explain it" is two CLI
calls on a machine where VS Code is installed:

    code -r C:\\path\\to\\project
    code chat -m agent "explain this codebase"

No window is focused, no key is pressed, and it works while the editor is
minimised. Cursor has no `chat` subcommand but does take a prompt deep
link (`cursor://anysphere.cursor-deeplink/prompt?text=…`), which prefills
its composer and waits for the human - the same shape of promise.

Projects are named, not typed: the editor's own "recently opened" list is
read from its storage, so "open my clicky windows project" resolves to
C:\\Users\\…\\clicky-windows-main without the user spelling a path.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import dataclass
from typing import Any, Optional

from ..discovery import open_uri, run_cli, scan
from ..model import Action, Param, Refused, Risk, Tier, Unavailable

# name, CLI stem, URI scheme, storage folder under %APPDATA%
EDITORS = (
    ("vscode", "Visual Studio Code", "code", "vscode", "Code",
     (r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd",)),
    ("cursor", "Cursor", "cursor", "cursor", "Cursor",
     (r"%LOCALAPPDATA%\Programs\cursor\resources\app\bin\cursor.cmd",)),
    ("windsurf", "Windsurf", "windsurf", "windsurf", "Windsurf",
     (r"%LOCALAPPDATA%\Programs\Windsurf\bin\windsurf.cmd",)),
    ("antigravity", "Antigravity", "antigravity", "antigravity", "Antigravity", ()),
)

PROJECT_ROOTS = (r"%USERPROFILE%\Documents", r"%USERPROFILE%\source\repos", r"%USERPROFILE%\Projects",
                 r"%USERPROFILE%\Desktop", r"%USERPROFILE%\dev", r"%USERPROFILE%\code")


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


@dataclass
class Editor:
    app: str
    display_name: str
    cli_name: str
    scheme: str
    storage: str
    fallbacks: tuple[str, ...]
    aliases: tuple[str, ...] = ()

    # -- where it lives ----------------------------------------------------------
    def cli(self) -> Optional[str]:
        """The editor's own launcher. PATH is not trusted blindly: Cursor
        installs a `code` shim, so `code` on PATH is not always VS Code."""
        for candidate in self.fallbacks:
            path = _expand(candidate)
            if os.path.isfile(path):
                return path
        found = scan().clis.get(self.cli_name)
        if found and self._identity(found) == self.app:
            return found
        return None

    def _identity(self, cli: str) -> str:
        code, out = run_cli([cli, "--version"], timeout=12)
        low = out.lower()
        for app, display, *_rest in EDITORS:
            if app in low or display.lower() in low:
                return app
        return "vscode" if code == 0 else ""

    def available(self) -> bool:
        return bool(self.cli()) or scan().has_scheme(self.scheme)

    # -- recent projects, so a name is enough -------------------------------------
    def recent(self, limit: int = 40) -> list[str]:
        path = os.path.join(os.environ.get("APPDATA", ""), self.storage, "User", "globalStorage",
                            "storage.json")
        if not os.path.isfile(path):
            return []
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        found: list[str] = []

        def walk(node: Any, depth: int = 0) -> None:
            if depth > 8 or len(found) > limit:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("folderUri", "workspace") and isinstance(value, str):
                        found.append(value)
                    else:
                        walk(value, depth + 1)
            elif isinstance(node, list):
                for value in node:
                    walk(value, depth + 1)
        walk(data)
        out = []
        for uri in found:
            if uri.startswith("file:///"):
                p = os.path.normpath(urllib.parse.unquote(uri[len("file:///"):]))
                if p not in out:
                    out.append(p)
        return out

    def resolve_project(self, name: str) -> str:
        """A path, a recent project, or a folder in the usual places."""
        raw = _expand(name.strip().strip('"'))
        if os.path.isdir(raw):
            return os.path.abspath(raw)
        want = _norm(name)
        if not want:
            raise Refused("say which project")
        recents = self.recent()
        for path in recents:                       # exact folder name first
            if _norm(os.path.basename(path)) == want:
                return path
        for path in recents:                       # then a recent one that contains it
            if want in _norm(os.path.basename(path)):
                return path
        for root in PROJECT_ROOTS:
            base = _expand(root)
            if not os.path.isdir(base):
                continue
            try:
                entries = os.listdir(base)
            except OSError:
                continue
            for entry in entries:
                full = os.path.join(base, entry)
                if os.path.isdir(full) and want in _norm(entry):
                    return full
        raise Refused(f"no project called {name!r} in {self.display_name}'s recent list or your usual "
                      f"folders. Recent: {', '.join(os.path.basename(p) for p in recents[:6]) or 'none'}")

    # -- the actions -----------------------------------------------------------------
    def actions(self) -> list[Action]:
        A = []

        async def open_project(project: str, new_window: bool = False) -> str:
            path = self.resolve_project(project)
            cli = self.cli()
            if cli:
                code, out = run_cli([cli, "-n" if new_window else "-r", path])
                if code not in (0, None):
                    raise Unavailable(out or f"{self.cli_name} exited {code}")
                return f"opened {os.path.basename(path)} in {self.display_name} ({path})"
            if scan().has_scheme(self.scheme):
                open_uri(f"{self.scheme}://file/{path.replace(os.sep, '/')}/")
                return f"opened {os.path.basename(path)} in {self.display_name} by link"
            raise Unavailable(f"{self.display_name} has no CLI or link handler here")

        async def open_file(path: str, line: int = 0) -> str:
            full = _expand(path)
            if not os.path.isabs(full):
                raise Refused("give the file's full path")
            cli = self.cli()
            target = f"{full}:{int(line)}" if line else full
            if cli:
                code, out = run_cli([cli, "-g", target, "-r"])
                if code not in (0, None):
                    raise Unavailable(out or f"{self.cli_name} exited {code}")
                return f"opened {os.path.basename(full)}" + (f" at line {line}" if line else "")
            open_uri(f"{self.scheme}://file/{full.replace(os.sep, '/')}" + (f":{line}" if line else ""))
            return f"opened {os.path.basename(full)} by link"

        async def chat(prompt: str, project: str = "", file: str = "", mode: str = "agent") -> str:
            """Put a prompt into the editor's AI chat. Never sends it: both
            routes hand the text over and wait for the person."""
            if project:
                await open_project(project)
            cli = self.cli()
            if cli and self._supports_chat(cli):
                argv = [cli, "chat", "-r"]
                if mode:
                    argv += ["-m", mode]
                if file:
                    argv += ["-a", _expand(file)]
                argv.append(prompt)
                code, out = run_cli(argv, timeout=30)
                if code not in (0, None):
                    raise Unavailable(out or f"chat exited {code}")
                return f"put the prompt in {self.display_name}'s chat ({mode} mode): {prompt[:80]}"
            if self.app == "cursor" and scan().has_scheme("cursor"):
                if len(prompt) > 9000:
                    raise Refused("that prompt is too long for a Cursor link (9,000 characters)")
                open_uri("cursor://anysphere.cursor-deeplink/prompt?text="
                         + urllib.parse.quote(prompt, safe=""))
                return f"prefilled Cursor's chat - it waits for you to press send: {prompt[:80]}"
            raise Unavailable(f"{self.display_name} has no chat command on this machine")

        async def recent_projects() -> str:
            names = self.recent(limit=20)
            if not names:
                return f"{self.display_name} has no recent projects"
            return f"{self.display_name} recent projects: " + ", ".join(
                f"{os.path.basename(p)} ({p})" for p in names[:12])

        async def install_extension(extension_id: str) -> str:
            cli = self.cli()
            if not cli:
                raise Unavailable(f"{self.display_name} has no CLI here")
            code, out = run_cli([cli, "--install-extension", extension_id], timeout=120)
            if code not in (0, None):
                raise Unavailable(out or f"exited {code}")
            return f"installed {extension_id} into {self.display_name}"

        async def list_extensions() -> str:
            cli = self.cli()
            if not cli:
                raise Unavailable(f"{self.display_name} has no CLI here")
            _code, out = run_cli([cli, "--list-extensions"], timeout=60)
            names = [l for l in out.splitlines() if l.strip()]
            return f"{len(names)} extensions: " + ", ".join(names[:30])

        async def diff(left: str, right: str) -> str:
            cli = self.cli()
            if not cli:
                raise Unavailable(f"{self.display_name} has no CLI here")
            run_cli([cli, "-d", _expand(left), _expand(right)])
            return f"opened a diff of {os.path.basename(left)} and {os.path.basename(right)}"

        A.append(Action(self.app, "open_project", f"Open a project (by name or path) in {self.display_name}.",
                        open_project, Tier.API, Risk.CHANGES,
                        (Param("project", "Project name or full path", True),
                         Param("new_window", "Open in a new window", type="boolean")),
                        examples=(f"open my clicky windows project in {self.display_name}",)))
        A.append(Action(self.app, "open_file", "Open a file, optionally at a line.", open_file,
                        Tier.API, Risk.CHANGES,
                        (Param("path", "Full path to the file", True), Param("line", "Line number", type="integer"))))
        A.append(Action(self.app, "chat", f"Put a prompt into {self.display_name}'s AI chat (it waits for you "
                        "to send it).", chat, Tier.API, Risk.CHANGES,
                        (Param("prompt", "What to ask", True),
                         Param("project", "Open this project first"),
                         Param("file", "Attach this file as context"),
                         Param("mode", "ask | edit | agent", choices=("ask", "edit", "agent"))),
                        examples=("open the clicky windows project in vs code and ask the chat to explain "
                                  "the agent system",)))
        A.append(Action(self.app, "recent_projects", "List recently opened projects.", recent_projects,
                        Tier.API, Risk.PASSIVE))
        A.append(Action(self.app, "install_extension", "Install an extension by id.", install_extension,
                        Tier.API, Risk.CHANGES, (Param("extension_id", "e.g. ms-python.python", True),)))
        A.append(Action(self.app, "list_extensions", "List installed extensions.", list_extensions,
                        Tier.API, Risk.PASSIVE))
        A.append(Action(self.app, "diff", "Open a diff of two files.", diff, Tier.API, Risk.CHANGES,
                        (Param("left", "First file", True), Param("right", "Second file", True))))
        return A

    def _supports_chat(self, cli: str) -> bool:
        code, out = run_cli([cli, "chat", "--help"], timeout=15)
        return code == 0 and "chat" in out.lower() and "usage" in out.lower()


def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def adapters() -> list[Editor]:
    out = []
    for app, display, cli_name, scheme, storage, fallbacks in EDITORS:
        editor = Editor(app, display, cli_name, scheme, storage, fallbacks,
                        aliases=(display.lower(), cli_name))
        if editor.available():
            out.append(editor)
    return out
