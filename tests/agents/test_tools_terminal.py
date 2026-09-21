"""Terminal tools: real subprocesses (python only), no network, no installs."""
import shutil
import sys

import pytest

from agents.core import PermissionPolicy, RiskLevel, ToolCall, ToolContext, auto_deny
from agents.tools import default_registry
from agents.tools.terminal_tools import _PACKAGE_RE, _detect_manager

PY = f'"{sys.executable}"'


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture
def run(ws):
    reg = default_registry()
    ctx = ToolContext(workspace=str(ws), policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))

    async def _run(_tool, **args):
        return await reg.execute(ToolCall(_tool, args), ctx)
    return _run


# -- run_command ---------------------------------------------------------------

async def test_run_command_captures_output_and_exit_code(ws, run):
    res = await run("run_command", command=f'{PY} -c "print(1)"')
    assert res.ok, res.error
    assert res.output.strip() == "1"
    assert res.data["exit_code"] == 0 and res.data["stdout"].strip() == "1"
    assert res.data["cwd"] == str(ws) and res.data["timed_out"] is False


async def test_run_command_nonzero_exit_keeps_output(ws, run):
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    res = await run("run_command", command=f'{PY} -c "{code}"')
    assert not res.ok and res.error == "exit code 3"
    assert "out" in res.output and "--- stderr ---" in res.output and "err" in res.output
    assert res.data["exit_code"] == 3 and res.data["stderr"].strip() == "err"


async def test_run_command_runs_in_cwd_inside_workspace(ws, run):
    (ws / "sub").mkdir()
    res = await run("run_command", command=f'{PY} -c "import os; print(os.getcwd())"', cwd="sub")
    assert res.ok and res.output.strip().lower() == str(ws / "sub").lower()
    res = await run("run_command", command="echo hi", cwd="..")
    assert not res.ok and res.error.startswith("outside workspace: ")
    res = await run("run_command", command="echo hi", cwd="missing")
    assert not res.ok and "not a folder" in res.error
    res = await run("run_command", command="   ")
    assert not res.ok and "empty" in res.error


async def test_run_command_timeout_kills_process(ws, run):
    res = await run("run_command", command=f'{PY} -c "import time; time.sleep(20)"', timeout=1)
    assert not res.ok and res.data["timed_out"] is True
    assert res.error.startswith("timed out") and "timed out after 1s" in res.data["stderr"]
    assert res.duration_ms < 15_000


async def test_run_command_truncates_long_output(ws, run):
    res = await run("run_command", command=f'{PY} -c "print(\'x\' * 50000)"')
    assert res.ok and res.data["truncated"] is True
    assert len(res.data["stdout"]) < 25_000 and "[truncated" in res.data["stdout"]


async def test_run_command_utf8_output(ws, run):
    res = await run("run_command", command=f'{PY} -c "print(\'héllo wörld ✓\')"')
    assert res.ok and "héllo wörld ✓" in res.output


async def test_run_command_is_medium_risk(ws):
    ctx = ToolContext(workspace=str(ws), policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW),
                      approver=auto_deny)
    res = await default_registry().execute(ToolCall("run_command", {"command": "echo x"}), ctx)
    assert not res.ok and res.error.startswith("permission denied")


# -- install_package -----------------------------------------------------------

def test_detect_manager(tmp_path):
    assert _detect_manager(str(tmp_path)) is None
    (tmp_path / "package.json").write_text("{}")
    assert _detect_manager(str(tmp_path)) == "npm"
    (tmp_path / "yarn.lock").write_text("")
    assert _detect_manager(str(tmp_path)) == "yarn"
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert _detect_manager(str(tmp_path)) == "pnpm"
    py = tmp_path / "py"
    py.mkdir()
    (py / "requirements.txt").write_text("")
    assert _detect_manager(str(py)) == "pip"


def test_package_name_validation():
    for good in ("react", "@types/node", "react@^18.2.0", "requests>=2.31,<3", "pkg[extra]",
                 "vite@latest", "alias@npm:react"):
        assert _PACKAGE_RE.match(good), good
    for bad in ("react && del /q *", "a;b", "x|y", "`id`", "$(x)", "a b", "'q'"):
        assert not _PACKAGE_RE.match(bad), bad


async def test_install_package_refuses_without_manager_or_bad_names(ws, run):
    res = await run("install_package", packages=["react"])
    assert not res.ok and "cannot detect a package manager" in res.error
    (ws / "package.json").write_text("{}")
    res = await run("install_package", packages=["react && echo pwned"])
    assert not res.ok and "invalid package name" in res.error
    res = await run("install_package", packages="react ; rm -rf /")
    assert not res.ok and "invalid package name" in res.error
    res = await run("install_package", manager="pip")
    assert not res.ok and "requirements.txt" in res.error
    res = await run("install_package", manager="conda", packages=["x"])
    assert not res.ok and "invalid arguments" in res.error


def test_install_package_builds_expected_commands(ws):
    from agents.tools.terminal_tools import InstallPackage
    tool = InstallPackage()
    cmd, err = tool._command("npm", ["react", "react-dom"], False, str(ws))
    assert err is None and cmd == "npm install react react-dom"
    cmd, _ = tool._command("npm", ["vitest"], True, str(ws))
    assert cmd == "npm install --save-dev vitest"
    cmd, _ = tool._command("pnpm", ["x"], True, str(ws))
    assert cmd == "pnpm add -D x"
    cmd, _ = tool._command("yarn", [], False, str(ws))
    assert cmd == "yarn install"
    cmd, _ = tool._command("pip", ["requests"], False, str(ws))
    assert isinstance(cmd, list) and cmd[1:] == ["-m", "pip", "install", "requests"]


# -- execute_script ------------------------------------------------------------

async def test_execute_script_python_and_cleanup(ws, run):
    res = await run("execute_script", code="import os\nprint('py', os.getcwd())")
    assert res.ok, res.error
    assert res.output.startswith("py ") and str(ws).lower() in res.output.lower()
    assert res.data["language"] == "python"
    tmp = ws / ".tora_tmp"
    assert tmp.is_dir() and (tmp / ".gitignore").read_text() == "*\n"
    assert not list(tmp.glob("script_*"))                    # temp script removed
    res = await run("execute_script", code="raise SystemExit(7)")
    assert not res.ok and res.data["exit_code"] == 7


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
async def test_execute_script_node(ws, run):
    res = await run("execute_script", code="console.log('node ok')", language="node")
    assert res.ok, res.error
    assert res.output.strip() == "node ok"


@pytest.mark.skipif(sys.platform != "win32", reason="powershell.exe is Windows only")
async def test_execute_script_powershell(ws, run):
    res = await run("execute_script", code="Write-Output 'ps ok'", language="powershell")
    assert res.ok, res.error
    assert res.output.strip() == "ps ok"


async def test_execute_script_rejects_unknown_language(ws, run):
    res = await run("execute_script", code="x", language="ruby")
    assert not res.ok and "invalid arguments" in res.error
