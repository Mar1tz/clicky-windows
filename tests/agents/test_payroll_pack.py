"""The Payroll pack on a fresh folder: no credentials, no network.

Gross pay from salaries and timesheets with overtime, the payroll
software's deductions in, the reviewer's checks (and which ones block),
finalising with approval into payslips, a bank file and a balanced
journal, the next month compared with this one, leave balances.
"""
from __future__ import annotations

import csv
import os
import time
from datetime import date

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, Task, TaskStatus, ToolCall, ToolContext


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("payroll")
    m.extensions.configure("payroll", {"employer": "Hove Bakery", "minimum_hourly": 12.21, "pension_pct": 5})
    m.start()
    yield m
    m.stop()


def _pay(mgr):
    return mgr.extensions.get("payroll").ensure_store(mgr.extensions._context("payroll"))


def _run(mgr, tool: str, **args):
    ctx = ToolContext(workspace=_pay(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def _post(mgr, tool: str, *, approve: bool = True, **args):
    root = _pay(mgr).root
    task = mgr.submit_task(Task(title="t", goal="payroll", workspace=root, metadata={"extension": "payroll"}))
    mgr.wait(task.id, 30)
    ctx = ToolContext(workspace=root, task_id=task.id, policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH),
                      approver=mgr._approver_for(task))
    fut = mgr.runtime.submit(mgr.tools.execute(ToolCall(tool, args), ctx))
    deadline = time.monotonic() + 10
    while not mgr.pending_approvals():
        assert time.monotonic() < deadline, "no approval was requested"
        time.sleep(0.02)
    req = mgr.pending_approvals()[0]
    assert req.call.tool_name == tool and not req.sensitive
    mgr.approve(req.id, approve)
    return fut.result(10)


@pytest.fixture
def august(mgr, tmp_path):
    _run(mgr, "add_employee", code="E1", name="Ana Silva", pay_type="salary", rate=36000, bank="11-22-33 12345678",
         start="2026-01-01")
    _run(mgr, "add_employee", code="E2", name="Ben Okafor", pay_type="hourly", rate=15, bank="22-33-44 87654321",
         start="2026-01-01")
    _run(mgr, "add_employee", code="E3", name="Cleo Park", pay_type="hourly", rate=11.5, bank="22-33-44 87654321",
         start="2026-01-01")
    assert _run(mgr, "start_pay_run", period="2026-08").ok
    (tmp_path / "hours.csv").write_text("employee,hours,overtime\nE2,80,10\nBen Okafor,40,\nCleo Park,40,\nZed,5,\n",
                                        encoding="utf-8")
    (tmp_path / "ded.csv").write_text("code,income tax,ni\nE1,400,250\nE2,200,120\n", encoding="utf-8")
    t = _run(mgr, "import_timesheet", period="2026-08", path=str(tmp_path / "hours.csv"))
    assert [f["title"] for f in t.data["findings"]] == ["Timesheet names nobody on the payroll: Zed"]
    _run(mgr, "import_deductions", period="2026-08", path=str(tmp_path / "ded.csv"))
    return mgr


def test_only_finalising_asks_and_nothing_pays(mgr):
    levels = mgr.extensions.get("payroll").TOOLS.levels()
    assert {n for n, lvl in levels.items() if lvl == "post"} == {"finalise_pay_run"}
    assert "sensitive" not in levels.values()


def test_gross_and_net_from_salary_hours_overtime_and_deductions(august):
    lines = {x["code"]: x for x in _pay(august).run("2026-08")["lines"]}
    assert (lines["E1"]["gross"], lines["E1"]["pension"], lines["E1"]["net"]) == (3000.0, 150.0, 2200.0)
    # 120 h + 10 h overtime at 1.5x, at 15 an hour; pension 5% of gross
    assert (lines["E2"]["gross"], lines["E2"]["pension"], lines["E2"]["net"]) == (2025.0, 101.25, 1603.75)
    changed = _run(august, "set_pay_line", period="2026-08", employee="Ben", hours=100).data["line"]
    assert changed["gross"] == 1725.0 and changed["pension"] == 86.25, "pension follows the new gross"
    fixed = _run(august, "set_pay_line", period="2026-08", employee="Ben", pension=50).data["line"]
    assert fixed["pension"] == 50.0 and fixed["net"] == 1725 - 200 - 120 - 50


def test_the_checks_and_what_blocks_finalising(august):
    c = _run(august, "check_pay_run", period="2026-08")
    blocking = {f["title"] for f in c.data["findings"] if f["severity"] == "high"}
    assert blocking == {"Ben Okafor and Cleo Park are paid into the same bank account",
                        "Cleo Park is paid GBP 11.50 an hour, under the GBP 12.21 minimum you set"}
    assert "Cleo Park has no tax or social security - were the payroll software's figures imported?" in \
        {f["title"] for f in c.data["findings"]}
    refused = _post(august, "finalise_pay_run", period="2026-08")
    assert not refused.ok and "same bank account" in refused.error

    _run(august, "add_employee", code="E3", name="Cleo Park", pay_type="hourly", rate=12.5, bank="33-44-55 11112222")
    assert _pay(august).run("2026-08")["status"] == "draft"
    assert not _post(august, "finalise_pay_run", approve=False, period="2026-08").ok
    assert _pay(august).run("2026-08")["status"] == "draft", "saying no changes nothing"
    final = _post(august, "finalise_pay_run", period="2026-08")
    assert final.ok, final.error
    f = final.data["final"]
    assert f["balanced"] and len(f["payslips"]) == 3 and all(os.path.isfile(x) for x in f["payslips"])
    assert f["totals"]["gross"] == 3000 + 2025 + 500.0, "Cleo's new rate is in: 40 h at 12.50"
    with open(f["bank_file"], encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["name"], r["amount"]) for r in rows] == [("Ana Silva", "2200.00"), ("Ben Okafor", "1603.75"),
                                                         ("Cleo Park", "475.00")]
    with open(f["journal"], encoding="utf-8") as fh:
        j = list(csv.DictReader(fh))
    assert round(sum(float(r["debit"]) for r in j), 2) == round(sum(float(r["credit"]) for r in j), 2)
    assert "is final" in _run(august, "set_pay_line", period="2026-08", employee="E1", tax=1).error


def test_next_month_is_compared_with_this_one(august):
    p = _pay(august)
    p.add_employee("E3", "Cleo Park", "hourly", 12.5, bank="33-44-55 11112222")
    p.finalise("2026-08")
    p.leaver("E3", "2026-08-31")
    p.add_employee("E4", "Dan Moss", "salary", 30000, bank="44-55-66 22223333", start="2026-09-15")
    _run(august, "start_pay_run", period="2026-09")
    _run(august, "set_pay_line", period="2026-09", employee="Ben", hours=200, tax=200, social=120)
    titles = {f["title"] for f in _run(august, "check_pay_run", period="2026-09").data["findings"]}
    assert "Ben Okafor's gross pay moved from GBP 2,025.00 to GBP 3,000.00" in titles
    assert "Cleo Park was paid last period and is not in this run" in titles
    assert "Dan Moss is new this period" in titles
    assert any(t.startswith("Dan Moss starts or leaves part-way through 2026-09") for t in titles)
    assert not any(t.startswith("Ana Silva's gross") for t in titles), "the same salary is no swing"


def test_leave_is_earned_through_the_year(mgr):
    _run(mgr, "add_employee", code="E1", name="Ana Silva", pay_type="salary", rate=36000, start="2026-01-01")
    assert _run(mgr, "leave_balance", employee="Ana", as_of="2026-06-30").data["leave"]["earned"] == 14.0
    left = _run(mgr, "record_leave", employee="E1", day="2026-06-10", days=3).data["leave"]
    assert (left["taken"], left["left"]) == (3.0, 11.0), "January to June earns 28 x 6/12 = 14; 3 taken"
    _run(mgr, "add_employee", code="E2", name="Mid Year", pay_type="hourly", rate=13, start="2026-07-01")
    assert _run(mgr, "leave_balance", employee="E2", as_of="2026-09-30").data["leave"]["earned"] == 7.0


def test_the_monthly_check_runs_as_a_workflow(mgr):
    period = date.today().isoformat()[:7]
    _run(mgr, "add_employee", code="E1", name="Ana Silva", pay_type="salary", rate=36000, start="2020-01-01")
    _run(mgr, "start_pay_run", period=period)
    task = mgr.wait(mgr.submit("please check the pay run").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    titles = {f["title"] for f in task.metadata.get("findings", [])}
    assert "Ana Silva has no bank account" in titles
