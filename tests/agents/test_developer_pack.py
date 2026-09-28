"""The Software developers pack on a throwaway git repo: no network.

The next version and release notes from conventional commits, a change
review that catches a key and a debug print and notices no test changed,
TODOs, loose dependencies, a JUnit report grouped by cause, an issues
export sorted - and nothing written into the repository.
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, TaskStatus, ToolCall, ToolContext


def g(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.test",
                    "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "shop-api"
    r.mkdir()
    g(r, "init", "-q")
    (r / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    g(r, "add", "app.py")
    g(r, "commit", "-q", "-m", "chore: start")
    g(r, "tag", "v1.2.0")
    for msg in ("feat(export): add CSV export", "fix(api): handle an empty body", "docs: explain the export",
                "Tidy the imports"):
        g(r, "commit", "-q", "--allow-empty", "-m", msg)
    return r


@pytest.fixture
def mgr(tmp_path, repo):
    m = build_agent_manager(workspace_root=str(tmp_path / "ws"), in_memory=True, offline=True)
    m.extensions.enable("developer")
    m.extensions.configure("developer", {"repo": str(repo)})
    m.start()
    yield m
    m.stop()


def _d(mgr):
    return mgr.extensions.get("developer").ensure_store(mgr.extensions._context("developer"))


def _run(mgr, tool: str, **args):
    ctx = ToolContext(workspace=_d(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def test_nothing_is_pushed_and_no_core_git_tool_is_shadowed(mgr):
    levels = mgr.extensions.get("developer").TOOLS.levels()
    assert set(levels.values()) == {"read", "draft"}
    assert not any(n.startswith("git_") for n in levels)


def test_the_next_version_follows_the_commits(mgr, repo):
    v = _run(mgr, "suggest_next_version").data["version"]
    assert v == {"current": "v1.2.0", "next": "v1.3.0", "bump": "minor", "commits": 4}
    g(repo, "commit", "-q", "--allow-empty", "-m", "feat!: drop Python 3.9", "-m", "BREAKING CHANGE: needs 3.10")
    assert _run(mgr, "suggest_next_version").data["version"]["next"] == "v2.0.0"


def test_release_notes_group_the_commits_and_leave_housekeeping_out(mgr, repo):
    r = _run(mgr, "write_release_notes")
    assert r.ok, r.error
    notes = r.data["notes"]
    assert notes["version"] == "v1.3.0" and notes["left_out"] == 1
    text = open(notes["path"], encoding="utf-8").read()
    assert "### Features\n\n- **export:** add CSV export" in text and "### Fixes\n\n- **api:** handle an empty body" in text
    assert "### Other changes\n\n- Tidy the imports" in text and "explain the export" not in text
    assert not os.path.exists(os.path.join(repo, "RELEASE_NOTES.md")) and notes["path"].startswith(_d(mgr).root)


def test_a_change_with_a_key_and_a_print_and_no_test_is_caught(mgr, repo):
    (repo / "app.py").write_text("def add(a, b):\n    print('debug', a)\n    return a + b\n\n"
                                 "AWS = 'AKIAABCDEFGHIJKLMNOP'\nAPI_KEY = \"sk_live_abcdef123456\"\n", encoding="utf-8")
    r = _run(mgr, "review_changes")
    assert r.ok, r.error
    titles = [f["title"] for f in r.data["findings"]]
    assert "app.py: looks like an AWS access key was added" in titles
    assert "app.py: looks like a hard-coded password or key was added" in titles
    assert "app.py: debug leftover `print('debug', a)`" in titles
    assert "1 source file(s) changed and no test changed" in titles
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("print('fine in a test')\n", encoding="utf-8")
    g(repo, "add", "tests/test_app.py")
    titles = [f["title"] for f in _run(mgr, "review_changes").data["findings"]]
    assert "1 source file(s) changed and no test changed" not in titles
    assert not any("fine in a test" in t for t in titles), "a print in a test is not a leftover"


def test_todos_and_loose_dependencies(mgr, repo):
    (repo / "jobs.py").write_text("# TODO: handle unicode\nx = 1  # FIXME later\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("requests==2.32.3\nflask\nnumpy>=1.26\nrequests==2.31.0\n", encoding="utf-8")
    (repo / "package.json").write_text(json.dumps({"dependencies": {"lodash": "*", "react": "^18.3.1"}}), encoding="utf-8")
    g(repo, "add", ".")
    t = _run(mgr, "find_todos").data["todos"]
    assert t["count"] == 2 and t["by_tag"] == {"FIXME": 1, "TODO": 1}
    dep = _run(mgr, "check_dependencies")
    assert sorted(f["title"] for f in dep.data["findings"]) == [
        "package.json: lodash is any version (*)", "requests is listed twice (requirements.txt and requirements.txt:1)",
        "requirements.txt: flask is no version (flask)", "requirements.txt: numpy is a range, not a pin (numpy>=1.26)"]


def test_a_junit_report_is_grouped_by_cause(mgr, tmp_path):
    xml = tmp_path / "report.xml"
    xml.write_text(
        '<testsuites><testsuite name="t" tests="4">'
        '<testcase classname="tests.test_cart" name="test_total"><failure message="AssertionError: 10 != 12" '
        'type="AssertionError">tb</failure></testcase>'
        '<testcase classname="tests.test_cart" name="test_tax"><failure message="AssertionError: 10 != 12" '
        'type="AssertionError">tb</failure></testcase>'
        '<testcase classname="tests.test_pay" name="test_card"><error message="ModuleNotFoundError: No module named '
        '\'stripe\'" type="ModuleNotFoundError">tb</error></testcase>'
        '<testcase classname="tests.test_pay" name="test_slow"><skipped/></testcase>'
        '<testcase classname="tests.test_pay" name="test_ok"/></testsuite></testsuites>', encoding="utf-8")
    r = _run(mgr, "explain_test_failures", path=str(xml))
    rep = r.data["report"]
    assert (rep["tests"], rep["passed"], rep["failed"], rep["skipped"]) == (5, 1, 3, 1)
    assert [f["title"] for f in r.data["findings"]] == [
        "2 test(s) fail with AssertionError: AssertionError: 10 != 12",
        "1 test(s) fail with ModuleNotFoundError: ModuleNotFoundError: No module named 'stripe'"]


def test_issues_are_sorted_without_being_labelled(mgr, tmp_path):
    f = tmp_path / "issues.json"
    f.write_text(json.dumps([
        {"number": 1, "title": "App crashes on save", "body": "Traceback...", "labels": [], "updatedAt": "2026-09-20T10:00:00Z"},
        {"number": 2, "title": "App crashes on save!", "body": "", "labels": [], "updatedAt": "2026-09-21T10:00:00Z"},
        {"number": 3, "title": "How do I export to Excel?", "body": "", "labels": [], "updatedAt": "2026-09-22T10:00:00Z"},
        {"number": 4, "title": "Dark mode", "body": "", "labels": [{"name": "feature"}], "updatedAt": "2026-05-01T10:00:00Z"},
    ]), encoding="utf-8")
    r = _run(mgr, "triage_issues", path=str(f), as_of="2026-09-28")
    t = r.data["triage"]
    assert [(s["number"], s["label"]) for s in t["suggest"]] == [(1, "bug"), (2, "bug"), (3, "question")]
    assert [(d["a"], d["b"]) for d in t["duplicates"]] == [(1, 2)]
    assert [(s["number"], s["days"]) for s in t["stale"]] == [(4, 150)]
    assert os.path.isfile(t["path"])


def test_a_folder_that_is_not_a_repo_is_a_clear_no(mgr, tmp_path):
    (tmp_path / "plain").mkdir()
    assert "git" in _run(mgr, "dev_overview", repo=str(tmp_path / "plain")).error.lower()


def test_the_review_runs_as_a_workflow(mgr, repo):
    (repo / "app.py").write_text("def add(a, b):\n    breakpoint()\n    return a + b\n", encoding="utf-8")
    task = mgr.wait(mgr.submit("review my changes").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert "app.py: debug leftover `breakpoint()`" in [f["title"] for f in task.metadata.get("findings", [])]
