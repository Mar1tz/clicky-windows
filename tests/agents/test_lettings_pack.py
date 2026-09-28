"""The Lettings pack on a fresh folder: no credentials, no network.

Rent paid oldest month first, arrears dated from the oldest unpaid month,
letters that escalate once each (and stop short of any legal notice),
bank money matched by reference or surname, repairs against their target,
certificates and renewals, and a deposit split the landlord approves.
"""
from __future__ import annotations

import os
import time

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, Task, TaskStatus, ToolCall, ToolContext

AS_OF = "2026-09-20"


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("lettings")
    m.extensions.configure("lettings", {"agent": "Kings Lettings"})
    m.start()
    yield m
    m.stop()


def _l(mgr):
    return mgr.extensions.get("lettings").ensure_store(mgr.extensions._context("lettings"))


def _run(mgr, tool: str, **args):
    ctx = ToolContext(workspace=_l(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def _post(mgr, tool: str, *, approve: bool = True, **args):
    root = _l(mgr).root
    task = mgr.submit_task(Task(title="t", goal="lettings", workspace=root, metadata={"extension": "lettings"}))
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
def let(mgr):
    assert _run(mgr, "add_tenancy", ref="T1", address="Flat 2, 4 Kings Road", tenants="Amy Reed, Joe Reed",
                rent=1200, due_day=1, start="2026-06-01", deposit=1380, email="reed@example.test").ok
    assert _run(mgr, "add_tenancy", ref="T2", address="9 Park Lane", tenants="Priya Shah", rent=950, due_day=15,
                start="2026-07-15", end="2026-11-01").ok
    for when in ("2026-06-01", "2026-07-02", "2026-08-03"):
        _run(mgr, "record_rent", tenancy="T1", amount=1200, date=when)
    for when in ("2026-07-15", "2026-08-15"):
        _run(mgr, "record_rent", tenancy="T2", amount=950, date=when)
    return mgr


def test_only_settling_a_deposit_asks(mgr):
    levels = mgr.extensions.get("lettings").TOOLS.levels()
    assert {n for n, lvl in levels.items() if lvl == "post"} == {"settle_deposit"} and "sensitive" not in levels.values()


def test_rent_is_paid_oldest_month_first(let):
    b = _run(let, "tenancy_balance", tenancy="Reed", as_of=AS_OF).data["balance"]
    assert (b["months_due"], b["due"], b["paid"], b["arrears"], b["oldest_unpaid"], b["days_late"]) == (
        4, 4800.0, 3600.0, 1200.0, "2026-09-01", 19)
    assert "already has an active tenancy" in _run(let, "add_tenancy", ref="T9", address="9 park lane", tenants="X",
                                                   rent=1, due_day=1, start="2026-07-01").error
    _run(let, "add_tenancy", ref="T3", address="3 Leap Street", tenants="Kay Moss", rent=500, due_day=31,
         start="2027-01-31")
    feb = _l(let).balance("T3", "2027-02-28")
    assert feb["months_due"] == 2, "rent due on the 31st falls on 28 February"


def test_letters_escalate_once_each_and_never_become_a_notice(let):
    r = _run(let, "draft_arrears_letters", as_of=AS_OF).data["letters"]
    assert [(d["ref"], d["level"]) for d in r["drafted"]] == [("T1", 2), ("T2", 1)], "19 days: second; 5 days: reminder"
    assert _run(let, "draft_arrears_letters", as_of="2026-09-25").data["letters"]["drafted"] == [], "not twice"
    later = _run(let, "draft_arrears_letters", as_of="2026-10-05")
    assert [(d["ref"], d["level"]) for d in later.data["letters"]["drafted"]] == [("T1", 3), ("T2", 2)]
    assert later.data["findings"][0]["title"] == "Flat 2, 4 Kings Road: 34 days in arrears after the final letter"
    assert "law where the property is" in later.data["findings"][0]["detail"]
    text = open(later.data["letters"]["drafted"][0]["path"], encoding="utf-8", errors="replace").read()
    assert "notice" not in text.lower().replace("noticed", ""), "no legal notice is drafted"


def test_bank_money_is_matched_by_reference_or_surname(let, tmp_path):
    f = tmp_path / "bank.csv"
    f.write_text("Date,Description,Amount\n2026-09-02,FP T1 SEPT RENT,1200.00\n2026-09-15,FP P SHAH,950.00\n"
                 "2026-09-16,FP UNKNOWN PERSON,400.00\n2026-09-17,CARD TESCO,-12.00\n", encoding="utf-8")
    r = _run(let, "import_rent_payments", path=str(f))
    assert (r.data["import"]["matched"], len(r.data["import"]["unmatched"])) == (2, 1)
    assert r.data["findings"][0]["title"].startswith("Money in not matched to a tenancy: 2026-09-16 FP UNKNOWN PERSON")
    assert _run(let, "import_rent_payments", path=str(f)).data["import"]["already_there"] == 2
    roll = _run(let, "rent_roll", as_of=AS_OF).data["roll"]
    assert roll["arrears"] == 0 and roll["rent_a_month"] == 2150


def test_repairs_go_to_a_contractor_and_are_chased_past_target(let):
    _run(let, "add_contractor", name="Hove Heating", trade="Plumbing", email="jobs@hoveheating.test")
    r = _run(let, "report_repair", address="Kings Road", issue="No hot water", priority="urgent").data["repair"]
    _l(let).db.execute("UPDATE repairs SET reported = '2026-09-18' WHERE id = ?", (r["id"],))
    _l(let).db.commit()
    order = _run(let, "send_works_order", repair_id=r["id"], trade="plumbing").data["repair"]
    assert order["contractor"] == "Hove Heating" and os.path.isfile(order["works_order"])
    late = [f["title"] for f in _run(let, "lettings_overview", as_of=AS_OF).data["findings"]]
    assert "Flat 2, 4 Kings Road: repair #1 (urgent) is 1 day(s) past target" in late
    _run(let, "complete_repair", repair_id=r["id"], cost=180)
    assert _run(let, "open_repairs").data["repairs"] == []
    assert "no contractor" in _run(let, "send_works_order", repair_id=_l(let).report_repair(
        "Park Lane", "Loose tile")["id"], trade="roofing").error


def test_certificates_and_renewals_come_up(let):
    _run(let, "set_property_certificate", address="Kings Road", kind="Gas safety", expires="2026-10-10")
    _run(let, "set_property_certificate", address="Park Lane", kind="Electrical (EICR)", expires="2026-09-01")
    titles = [f["title"] for f in _run(let, "lettings_overview", as_of=AS_OF).data["findings"]]
    assert "Flat 2, 4 Kings Road: Gas safety expires 2026-10-10" in titles
    assert "9 Park Lane: Electrical (EICR) expired 2026-09-01" in titles
    assert "9 Park Lane: tenancy T2 ends 2026-11-01" in titles


def test_a_deposit_is_proposed_then_settled_with_the_landlords_yes(let):
    assert "reason" in _run(let, "propose_deposit_return", tenancy="T1", deductions=[{"amount": 50}]).error
    assert "more than" in _run(let, "propose_deposit_return", tenancy="T1",
                               deductions=[{"reason": "Redecorate", "amount": 2000}]).error
    d = _run(let, "propose_deposit_return", tenancy="T1",
             deductions=[{"reason": "End of tenancy clean (invoice attached)", "amount": 150}]).data["deposit"]
    assert (d["to_tenant"], d["to_landlord"], d["status"]) == (1230.0, 150.0, "proposed")
    assert not _post(let, "settle_deposit", approve=False, tenancy="T1").ok
    assert _l(let).deposit("T1")["status"] == "proposed"
    assert _post(let, "settle_deposit", tenancy="T1").ok
    assert _l(let).tenancy("T1")["status"] == "ended" and _l(let).deposit("T1")["status"] == "settled"
    assert "already settled" in _run(let, "propose_deposit_return", tenancy="T1", deductions=[]).error


def test_the_morning_check_runs_as_a_workflow(let):
    task = let.wait(let.submit("check the rent roll").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert any("arrears" in f["title"] for f in task.metadata.get("findings", []))
