"""Process tools: run a command, install packages, execute a script.

All three funnel into process.run_process(), so the Windows rules
(hidden console, UTF-8, DEVNULL stdin, tree kill on timeout) live in one
place. run_command goes through the shell on purpose - that is how
`npm`, `npx` and `vite` (.cmd shims) resolve on Windows - which is why
install_package validates package names instead of trusting the LLM:
a name containing `&&` must never reach the shell.

execute_script writes the code to <workspace>/.tora_tmp (git-ignored via
a dropped .gitignore) rather than the system temp dir, so the script
sees the project's relative paths and a stray file stays visible to the
user instead of hiding in %TEMP%.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from agents.core import RiskLevel, ToolContext, ToolResult, new_id

from .base import Tool, make_spec
from .process import DEFAULT_TIMEOUT, IS_WINDOWS, python_executable, run_process

CATEGORY = "terminal"
TMP_DIR = ".tora_tmp"
INSTALL_TIMEOUT = 600.0

_CWD = {"type": "string", "description": "Working directory (inside the workspace). Default: workspace root."}
_TIMEOUT = {"type": "number", "description": f"Seconds before the process is killed. Default {DEFAULT_TIMEOUT:g}."}

# npm/pip package specs: name, scope, version ranges, extras. No shell metacharacters.
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9@/._:\-\[\]<>=!~^*+,]+$")

# The shell models actually write on Windows is PowerShell: a Verb-Noun
# cmdlet (Get-Item, Test-Path, Start-Process, Select-Object), a $variable,
# $env:. cmd.exe answers all of those with "is not recognized", which in
# the field failed three builds whose files were already written (see
# MILESTONES "Field report 4").
_PS_CMDLET = re.compile(
    r"(?:^|[|;({&]\s*)(?:Get|Set|New|Remove|Test|Select|Where|ForEach|Start|Stop|Invoke|Out|Write|Copy|Move|"
    r"Rename|Clear|Add|Measure|Sort|Format|Resolve|Split|Join|Expand|Compress|ConvertTo|ConvertFrom|Import|"
    r"Export|Group|Tee|Wait|Push|Pop|Update)-[A-Za-z]+\b", re.I)
_PS_SYNTAX = re.compile(r"\$env:|\$[A-Za-z_]\w*\s*=|\$_\b|\$\(|\$PSVersionTable|\[[A-Za-z.]+\]::", re.I)
_PS_PREFIX = re.compile(r"^\s*(?:&\s*)?[\"']?(?:powershell|pwsh)(?:\.exe)?[\"']?(?:\s|$)", re.I)

# Commands that only LOOK. A failed look is information for the agent (the
# file is not there, the tool is not installed), never proof a step failed.
_LOOK_VERBS = {
    "get-item", "get-childitem", "get-content", "test-path", "select-object", "where-object", "measure-object",
    "format-table", "format-list", "sort-object", "resolve-path", "get-location", "get-command", "get-process",
    "select-string", "out-string", "dir", "ls", "type", "cat", "echo", "where", "which", "pwd", "tree", "find",
    "findstr", "head", "tail", "wc", "stat", "file",
}
_LOOK_TOOLS = {  # tool -> sub-commands that only look
    "git": {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files"},
    "npm": {"ls", "list", "view", "outdated", "-v", "--version", "config"},
    "node": {"-v", "--version", "-e"},
    "python": {"--version", "-V"},
    "py": {"--version", "-V"},
    "pip": {"list", "show", "freeze", "--version"},
}


def looks_like_powershell(command: str) -> bool:
    if _PS_PREFIX.match(command):
        return False                       # already wrapped: run as written
    return bool(_PS_CMDLET.search(command) or _PS_SYNTAX.search(command))


def is_read_only_command(command: str) -> bool:
    """True when every part of a command line only inspects."""
    text = _PS_PREFIX.sub("", command).strip().strip('"').strip("'")
    text = re.sub(r"^-Command\s+", "", text, flags=re.I).strip().strip('"').strip("'")
    parts = [p.strip() for p in re.split(r"\|\||&&|[|;&]", text) if p.strip()]
    if not parts:
        return False
    for part in parts:
        if re.search(r"(?<![-\w])>{1,2}", part):    # redirection writes a file
            return False
        words = part.split()
        head = words[0].lower().strip('"\'')
        if head in _LOOK_VERBS:
            continue
        sub = words[1].lower() if len(words) > 1 else ""
        if head in _LOOK_TOOLS and sub in _LOOK_TOOLS[head]:
            continue
        return False
    return True


def powershell_argv(command: str) -> list[str]:
    """PowerShell with UTF-8 output (5.1 defaults to the OEM code page when
    redirected) and no profile, policy or prompt getting in the way."""
    exe = "powershell" if IS_WINDOWS else "pwsh"
    return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]


class _ProcessTool(Tool):
    """Shared cwd / timeout handling."""

    def _cwd(self, ctx: ToolContext, raw: Optional[str]) -> tuple[str, Optional[ToolResult]]:
        cwd, err = self.guard(ctx, raw or ".")
        if err:
            return "", err
        if not os.path.isdir(cwd):
            return "", self.fail(f"cwd is not a folder: {cwd}")
        return cwd, None

    @staticmethod
    def _timeout(args: dict[str, Any], default: float = DEFAULT_TIMEOUT) -> float:
        value = args.get("timeout")
        return float(value) if value else default


class RunCommand(_ProcessTool):
    spec = make_spec(
        "run_command",
        "Run a shell command in the workspace and return its output and exit code. "
        "Non-interactive: anything that waits for input will time out. On Windows the "
        "command runs in cmd.exe, except that PowerShell (cmdlets like Get-Item or "
        "Start-Process, $variables) is detected and run in PowerShell - or set "
        "shell='powershell'. To look at files prefer read_file / list_dir.",
        risk=RiskLevel.MEDIUM, category=CATEGORY,
        properties={
            "command": {"type": "string", "description": "The command line, exactly as typed in a terminal."},
            "shell": {"type": "string", "enum": ["auto", "cmd", "powershell"],
                      "description": "Which shell. Default auto (PowerShell syntax is detected)."},
            "cwd": _CWD,
            "timeout": _TIMEOUT,
        },
        required=["command"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = (args.get("command") or "").strip()
        if not command:
            return self.fail("command must not be empty")
        cwd, err = self._cwd(ctx, args.get("cwd"))
        if err:
            return err
        shell = (args.get("shell") or "auto").lower()
        use_ps = shell == "powershell" or (shell == "auto" and IS_WINDOWS and looks_like_powershell(command))
        target = powershell_argv(command) if use_ps else command
        out = await run_process(target, cwd=cwd, timeout=self._timeout(args))
        return self.from_process(out, shell="powershell" if use_ps else ("cmd" if IS_WINDOWS else "sh"),
                                 read_only=is_read_only_command(command))


class InstallPackage(_ProcessTool):
    spec = make_spec(
        "install_package",
        "Install packages with npm, pnpm, yarn or pip. The manager is auto-detected from "
        "lock files / package.json / requirements.txt in cwd unless given. With no packages, "
        "installs the project's declared dependencies.",
        risk=RiskLevel.MEDIUM, category=CATEGORY,
        properties={
            "packages": {"type": ["array", "string"], "items": {"type": "string"},
                         "description": "Package names (optionally with versions), list or space-separated."},
            "manager": {"type": "string", "enum": ["npm", "pnpm", "yarn", "pip"],
                        "description": "Force a package manager."},
            "dev": {"type": "boolean", "description": "Install as a dev dependency (npm/pnpm/yarn)."},
            "cwd": _CWD,
            "timeout": _TIMEOUT,
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        cwd, err = self._cwd(ctx, args.get("cwd"))
        if err:
            return err
        packages = self._packages(args.get("packages"))
        for pkg in packages:
            if not _PACKAGE_RE.match(pkg):
                return self.fail(f"invalid package name: {pkg!r}")
        manager = args.get("manager") or _detect_manager(cwd)
        if manager is None:
            return self.fail("cannot detect a package manager in this folder "
                             "(no package.json / requirements.txt); pass manager=")
        command, err = self._command(manager, packages, bool(args.get("dev")), cwd)
        if err:
            return err
        out = await run_process(command, cwd=cwd, timeout=self._timeout(args, INSTALL_TIMEOUT))
        return self.from_process(out, manager=manager, packages=packages)

    @staticmethod
    def _packages(raw: Any) -> list[str]:
        if not raw:
            return []
        if isinstance(raw, str):
            return raw.split()
        return [str(p).strip() for p in raw if str(p).strip()]

    def _command(self, manager: str, packages: list[str], dev: bool,
                 cwd: str) -> tuple[Any, Optional[ToolResult]]:
        if manager == "pip":
            base = [python_executable(), "-m", "pip", "install"]
            if packages:
                return base + packages, None
            req = os.path.join(cwd, "requirements.txt")
            if not os.path.isfile(req):
                return None, self.fail("no packages given and no requirements.txt in cwd")
            return base + ["-r", req], None
        # JS managers are .cmd shims on Windows -> shell string; names are validated above.
        if not packages:
            return f"{manager} install", None
        if manager == "npm":
            flag = " --save-dev" if dev else ""
            return f"npm install{flag} {' '.join(packages)}", None
        if manager == "pnpm":
            flag = " -D" if dev else ""
            return f"pnpm add{flag} {' '.join(packages)}", None
        if manager == "yarn":
            flag = " --dev" if dev else ""
            return f"yarn add{flag} {' '.join(packages)}", None
        return None, self.fail(f"unsupported package manager: {manager}")


def _detect_manager(cwd: str) -> Optional[str]:
    def has(name: str) -> bool:
        return os.path.exists(os.path.join(cwd, name))
    if has("pnpm-lock.yaml"):
        return "pnpm"
    if has("yarn.lock"):
        return "yarn"
    if has("package.json") or has("package-lock.json"):
        return "npm"
    if has("requirements.txt") or has("pyproject.toml") or has("setup.py"):
        return "pip"
    return None


class ExecuteScript(_ProcessTool):
    spec = make_spec(
        "execute_script",
        "Run a snippet of Python, PowerShell or Node.js code. The code is written to a "
        "temporary file inside the workspace and executed with the workspace as cwd.",
        risk=RiskLevel.MEDIUM, category=CATEGORY,
        properties={
            "code": {"type": "string", "description": "Source code to run."},
            "language": {"type": "string", "enum": ["python", "powershell", "node"],
                         "description": "Default python."},
            "cwd": _CWD,
            "timeout": _TIMEOUT,
        },
        required=["code"],
    )

    _EXT = {"python": ".py", "powershell": ".ps1", "node": ".js"}

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        language = args.get("language") or "python"
        cwd, err = self._cwd(ctx, args.get("cwd"))
        if err:
            return err
        tmp_dir, err = self.guard(ctx, TMP_DIR)
        if err:
            return err
        script = os.path.join(tmp_dir, f"script_{new_id()}{self._EXT[language]}")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            ignore = os.path.join(tmp_dir, ".gitignore")
            if not os.path.exists(ignore):
                with open(ignore, "w", encoding="utf-8") as f:
                    f.write("*\n")
            with open(script, "w", encoding="utf-8", newline="") as f:
                f.write(args["code"])
        except OSError as exc:
            return self.fail(f"cannot write temp script: {exc}")
        try:
            out = await run_process(_interpreter(language, script), cwd=cwd,
                                    timeout=self._timeout(args))
        finally:
            try:
                os.remove(script)
            except OSError:
                pass
        return self.from_process(out, language=language, script_path=script)


def _interpreter(language: str, script: str) -> list[str]:
    if language == "python":
        return [python_executable(), script]
    if language == "node":
        return ["node", script]
    shell = "powershell" if IS_WINDOWS else "pwsh"
    return [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script]


def tools() -> list[Tool]:
    return [RunCommand(), InstallPackage(), ExecuteScript()]
