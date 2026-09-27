"""The Freelancer pack on a fresh business folder: no credentials, no network.

The path a freelancer takes through it - hours to invoice to payment to
set-aside, a quote the client accepts, late payers chased, receipts into
the books, the year-end pack - plus what keeps it safe: every tool's
level, approval before anything touches the books, nothing ever sent.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, Task, TaskStatus, ToolCall, ToolContext
from extensions.accounting.permissions import ActionLevel
from extensions.freelancer.business import Business
from extensions.freelancer.tools import TOOL_LEVELS


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("freelancer")
    m.extensions.configure("freelancer", {"name": "Sam Rivera Design", "rate": 80, "terms_days": 14})
    m.start()
    yield m
    m.stop()


def _biz(mgr) -> Business:
    return mgr.extensions.get("freelancer").ensure_business(mgr.extensions._context("freelancer"))


def _run(mgr, tool: str, **args):
    """As the agent would, with READ and DRAFT allowed unasked."""
    ctx = ToolContext(workspace=_biz(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def _approval(mgr, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = mgr.pending_approvals()
        if pending:
            return pending[0]
        time.sleep(0.02)
    raise AssertionError("no approval was requested")


def _post(mgr, tool: str, *, approve: bool = True, **args):
    """A POST tool inside a task, answering the approval it must ask for."""
    root = _biz(mgr).root
    task = mgr.submit_task(Task(title="t", goal="freelancer", workspace=root, metadata={"extension": "freelancer"}))
    mgr.wait(task.id, 30)
    ctx = ToolContext(workspace=root, task_id=task.id, policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH),
                      approver=mgr._approver_for(task))
    fut = mgr.runtime.submit(mgr.tools.execute(ToolCall(tool, args), ctx))
    req = _approval(mgr)
    assert req.call.tool_name == tool and not req.sensitive
    mgr.approve(req.id, approve)
    return fut.result(10)


# -- loading --------------------------------------------------------------------------------------------

def test_enable_plugs_the_pack_in_and_disable_takes_exactly_it_out(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    core = set(m.tools.names())
    m.extensions.enable("freelancer")
    added = set(m.tools.names()) - core
    assert added == set(TOOL_LEVELS)
    assert all(m.tools.get(n).spec.category == "freelancer" for n in added)
    assert "freelancer" in m.workers.types()
    assert not m.extensions.is_enabled("accounting"), "it uses Accounting's ledger, not its agents"
    for text, workflow in (("run my weekly admin", "weekly_admin"), ("please do my weekly admin", "weekly_admin"), ("chase my late payers", "weekly_admin"),
                           ("please prepare my year-end pack", "year_end"), ("log 3 hours for Acme", ""),
                           ("invoice Acme for my hours", ""), ("write a quote for Bloom", "")):
        meta = m.understand(text).metadata
        assert meta.get("template") == "freelance_admin" and meta.get("workflow", "") == workflow, text
    assert m.extensions.match_intent("what is a year-end pack?") is None, "a question is conversation"
    m.extensions.disable("freelancer")
    assert set(m.tools.names()) == core and "freelancer" not in m.workers.types()


def test_it_says_so_when_the_accounting_pack_is_missing(tmp_path):
    from agents.extensions import ExtensionRegistry
    from extensions.freelancer import create_extension
    reg = ExtensionRegistry(None, state_path=str(tmp_path / "ext.json"), package="no_such_package",
                            factories={"freelancer": create_extension})
    with pytest.raises(ValueError, match="needs the accounting pack"):
        reg.enable("freelancer")
    assert not reg.is_enabled("freelancer")
    found = {p["id"]: p for p in ExtensionRegistry(None, state_path=str(tmp_path / "e2.json")).available()}
    assert found["freelancer"]["requires"] == ["accounting"] and "accounting" in found


def test_every_tool_level_is_what_it_claims(mgr):
    for name, level in TOOL_LEVELS.items():
        tool, lvl = mgr.tools.get(name), ActionLevel(level)
        assert tool.spec.risk is lvl.risk, name
        assert bool(tool.always_ask) == lvl.always_ask and not tool.sensitive, name
    assert {n for n, lvl in TOOL_LEVELS.items() if lvl == "post"} == {"issue_invoice", "record_payment",
                                                                        "post_expenses"}


# -- the money path -----------------------------------------------------------------------------------------

def test_hours_become_an_invoice_then_a_payment_with_tax_set_aside(mgr):
    assert _run(mgr, "log_time", client="Acme Ltd", hours=3, note="Logo concepts").ok
    assert _run(mgr, "log_time", client="Acme Ltd", hours=2.5, note="Revisions").ok
    assert _run(mgr, "log_time", client="Bloom", hours=1, rate=100).ok
    unbilled = _run(mgr, "list_unbilled_time")
    assert "Acme Ltd: 5.5 h, USD440.00" in unbilled.output and "Bloom: 1 h, USD100.00" in unbilled.output

    draft = _run(mgr, "draft_invoice_from_time", client="acme").data["draft"]
    assert draft["client"] == "Acme Ltd" and draft["total"] == 440.0
    assert [e["client"] for e in _run(mgr, "list_unbilled_time").data["entries"]] == ["Bloom"]

    issued = _post(mgr, "issue_invoice", draft_id=draft["id"])
    assert issued.ok, issued.error
    inv = issued.data["invoice"]
    assert inv["number"] == "INV-0001" and inv["total"] == 440.0 and os.path.isfile(inv["file_path"])
    with open(inv["email"], "rb") as fh:
        msg = BytesParser(policy=policy.default).parse(fh)
    assert msg["X-Unsent"] == "1" and "INV-0001" in msg["Subject"]
    assert [p.get_filename() for p in msg.iter_attachments()] == [os.path.basename(inv["file_path"])]

    paid = _post(mgr, "record_payment", number="inv-0001")
    assert paid.ok, paid.error
    assert paid.data["payment"]["status"] == "paid" and paid.data["payment"]["set_aside"] == 110.0
    o = _run(mgr, "freelance_overview").data["overview"]
    assert (o["unpaid"], o["set_aside"], o["month_income"], o["unbilled_hours"]) == (0.0, 110.0, 440.0, 1.0)


def test_nothing_reaches_the_books_when_the_freelancer_says_no(mgr):
    draft = _run(mgr, "draft_invoice", client="Acme Ltd",
                 items=[{"description": "Website", "quantity": 1, "unit_price": 1200}]).data["draft"]
    refused = _post(mgr, "issue_invoice", approve=False, draft_id=draft["id"])
    assert not refused.ok and "denied" in refused.error.lower()
    b = _biz(mgr)
    assert b.invoices("all") == [] and b.outbox() == [] and b.draft(draft["id"])["status"] == "draft"


def test_a_part_payment_sets_aside_only_what_came_in(mgr):
    b = _biz(mgr)
    number = b.issue(b.draft_invoice("Acme Ltd", [{"description": "Retainer", "unit_price": 1000}])["id"])["number"]
    assert b.record_payment(number, 400)["set_aside"] == 100.0
    last = b.record_payment(number)
    assert (last["received"], last["set_aside"], last["status"]) == (600.0, 150.0, "paid")
    assert b.set_asides()["total"] == 250.0


def test_an_accepted_quote_becomes_an_invoice_draft(mgr):
    q = _run(mgr, "create_quote", client="Bloom", items=[{"description": "Brand kit", "quantity": 1,
                                                          "unit_price": 950}]).data["quote"]
    assert q["number"] == "Q-0001" and os.path.isfile(q["file"])
    d = _run(mgr, "accept_quote", number="q-0001").data["draft"]
    assert (d["client"], d["total"], d["source"]) == ("Bloom", 950.0, "Q-0001")
    assert not _run(mgr, "accept_quote", number="Q-0001").ok, "a quote is accepted once"
    assert _run(mgr, "list_quotes", status="accepted").data["quotes"][0]["draft_id"] == d["id"]


# -- the workflows, offline ---------------------------------------------------------------------------------

def test_weekly_admin_drafts_a_reminder_for_everyone_late_and_flags_the_worst(mgr):
    b = _biz(mgr)
    today = date.today()
    for client, issued_days_ago in (("Acme Ltd", 80), ("Bloom", 20), ("Cedar", 2)):
        d = b.draft_invoice(client, [{"description": "Work", "unit_price": 500}])
        b.issue(d["id"], when=(today - timedelta(days=issued_days_ago)).isoformat())
    task = mgr.wait(mgr.submit("run my weekly admin").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    reminders = [e for e in b.outbox() if e["kind"] == "reminder"]
    assert sorted(e["recipient"] for e in reminders) == ["Acme Ltd", "Bloom"], "Cedar is not late yet"
    tones = {r["client"]: r["tone"] for step in task.steps for res in step.results
             for r in (res.data or {}).get("reminders", [])}
    assert tones == {"Acme Ltd": "final", "Bloom": "friendly"}
    assert [f["title"] for f in task.metadata.get("findings", [])] == ["Acme Ltd: INV-0001 is 66 days late"]

    again = _run(mgr, "chase_late_payers")
    assert again.data["reminders"] == [] and sorted(again.data["skipped"]) == ["INV-0001", "INV-0002"]


def test_receipts_go_on_the_books_and_the_year_end_pack_is_ready_for_the_accountant(mgr, tmp_path):
    b = _biz(mgr)
    receipt = tmp_path / "adobe.pdf"
    receipt.write_bytes(b"%PDF-1.4 receipt")
    today = date.today().isoformat()
    assert _run(mgr, "add_expense", description="Adobe subscription", amount=30, category="Software",
                receipt=str(receipt), date=today).ok
    assert _run(mgr, "add_expense", description="Train to client", amount=45, category="Travel", date=today).ok
    assert not _run(mgr, "add_expense", description="Oops", amount=10, category="Sales").ok, \
        "income is not an expense account"
    assert _post(mgr, "post_expenses").ok
    number = b.issue(b.draft_invoice("Acme Ltd", [{"description": "Work", "unit_price": 1000}])["id"])["number"]
    b.record_payment(number)

    pack = _run(mgr, "prepare_year_end_pack")
    assert pack.ok, pack.error
    p = pack.data["pack"]
    assert (p["income"], p["expenses"], p["net_profit"], p["set_aside"]) == (1000.0, 75.0, 925.0, 250.0)
    assert all(os.path.isfile(f) for f in p["files"].values()) and "summary.pdf" in p["files"]
    with open(p["files"]["expenses.csv"], encoding="utf-8") as fh:
        assert fh.read().count("\n") == 3
    assert [f["title"] for f in pack.data["findings"]] == ["1 expense(s) have no receipt"]


def test_year_end_runs_as_a_workflow_too(mgr):
    task = mgr.wait(mgr.submit("prepare my year-end pack").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    pack = next(r.data["pack"] for s in task.steps for r in s.results if (r.data or {}).get("pack"))
    assert os.path.isfile(os.path.join(pack["folder"], "summary.pdf"))
    assert pack["folder"].startswith(os.path.join(_biz(mgr).root, "Year-end"))


def test_the_weekly_admin_can_run_every_friday(mgr):
    assert not any(j["id"] == "freelancer:weekly_admin" for j in mgr.scheduler.jobs())
    mgr.extensions.configure("freelancer", {"weekly_admin": True})
    job = next(j for j in mgr.scheduler.jobs() if j["id"] == "freelancer:weekly_admin")
    assert job["owner"] == "freelancer"


# -- edges ---------------------------------------------------------------------------------------------------

def test_bad_input_comes_back_as_a_reason_not_a_crash(mgr):
    for tool, args, why in (("log_time", {"client": "Acme", "hours": 0}, "hours"),
                            ("log_time", {"client": "", "hours": 1}, "client"),
                            ("draft_invoice", {"client": "Acme", "items": []}, "at least one line"),
                            ("draft_invoice_from_time", {"client": "Nobody"}, "no unbilled time"),
                            ("draft_reminder", {"number": "INV-0099"}, "no invoice"),
                            ("accept_quote", {"number": "Q-0404"}, "no quote")):
        r = _run(mgr, tool, **args)
        assert not r.ok and why in r.error.lower(), (tool, r.error)
    assert not _post(mgr, "issue_invoice", draft_id=404).ok


def test_a_uk_business_uses_the_april_tax_year(tmp_path):
    b = Business.create(str(tmp_path / "uk"), "Jo Smith", preset="uk", rate=60)
    assert b.currency == "GBP" and b.tax_year(2026) == ("2026-04-06", "2027-04-05")
    b.close()


def test_the_dashboard_shows_the_business(mgr):
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    b = _biz(mgr)
    b.log_time("Acme Ltd", 2)
    b.draft_invoice("Bloom", [{"description": "Logo", "unit_price": 300}])
    module = next(mod for mod in mgr.extensions.dashboard_modules() if mod.id == "freelancer.dashboard")
    dash = module.factory(mgr, mgr.extensions.get("freelancer"))
    try:
        assert dash.title.text() == "Sam Rivera Design"
        assert dash.numbers["unbilled"].text() == "2 h  USD160.00"
        assert dash.drafts.count() == 1 and "Bloom" in dash.drafts.item(0).text()
    finally:
        dash._timer.stop()
        dash.deleteLater()
