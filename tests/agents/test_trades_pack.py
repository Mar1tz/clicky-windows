"""The Trades pack on a fresh office folder: no credentials, no network.

Quote priced from the price list to the penny, a diary that refuses a
double booking and knows its free slots, parts lists per supplier, the
job's invoice (issuing and payments ask first), certificate renewals.
"""
from __future__ import annotations

import csv
import os
import time
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, Task, TaskStatus, ToolCall, ToolContext


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("trades")
    m.extensions.configure("trades", {"name": "Hove Heating", "labour_rate": 60, "markup_pct": 20, "callout": 45,
                                      "vat_pct": 20, "terms_days": 14})
    m.start()
    yield m
    m.stop()


def _office(mgr):
    return mgr.extensions.get("trades").ensure_store(mgr.extensions._context("trades"))


def _run(mgr, tool: str, **args):
    ctx = ToolContext(workspace=_office(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def _post(mgr, tool: str, *, approve: bool = True, **args):
    root = _office(mgr).root
    task = mgr.submit_task(Task(title="t", goal="trades", workspace=root, metadata={"extension": "trades"}))
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
def priced(mgr):
    _run(mgr, "set_material", code="COMBI-24", name="Combi boiler 24kW", cost=800, supplier="City Plumbing")
    _run(mgr, "set_material", code="FLUE-KIT", name="Horizontal flue kit", cost=90, supplier="City Plumbing")
    _run(mgr, "set_material", code="TE-2.5", name="2.5mm twin and earth, 100m", cost=45, unit="drum", supplier="CEF")
    return mgr


def test_only_invoicing_asks_and_nothing_is_sensitive(mgr):
    levels = mgr.extensions.get("trades").TOOLS.levels()
    assert {n for n, lvl in levels.items() if lvl == "post"} == {"issue_job_invoice", "record_job_payment"}
    assert "sensitive" not in levels.values()


def test_a_quote_is_priced_from_the_list_to_the_penny(priced):
    q = _run(priced, "create_job_quote", customer="Mrs Patel", job="Boiler swap", address="12 Elm Road, Hove",
             parts=[{"code": "COMBI-24", "qty": 1}, {"code": "flue kit", "qty": 1}], labour_hours=8)
    assert q.ok, q.error
    quote = q.data["quote"]
    assert [ln["amount"] for ln in quote["lines"]] == [960.0, 108.0, 480.0, 45.0]      # +20% on parts, 8 h, call-out
    assert (quote["subtotal"], quote["vat"], quote["total"]) == (1593.0, 318.6, 1911.6)
    assert os.path.isfile(quote["file"]) and quote["number"] == "Q-0001"
    assert "not on the price list" in _run(priced, "price_job", parts=[{"code": "UNOBTAINIUM"}], labour_hours=1).error
    assert _run(priced, "price_job", parts=[{"code": "boiler"}], labour_hours=1).ok, "one match by name is enough"
    _run(priced, "set_material", code="COMBI-30", name="Combi boiler 30kW", cost=950)
    assert "which code" in _run(priced, "price_job", parts=[{"code": "boiler"}], labour_hours=1).error


def test_the_diary_refuses_double_booking_and_finds_free_slots(priced):
    _run(priced, "create_job_quote", customer="Mrs Patel", job="Boiler swap", parts=[{"code": "COMBI-24", "qty": 1}],
         labour_hours=8)
    first = _run(priced, "book_job", quote="Q-0001", start="2026-10-05 09:00")
    assert first.ok and first.data["job"]["hours"] == 8, "the quote's hours fill the booking"
    clash = _run(priced, "book_job", customer="Mr Jones", title="Dripping tap", start="2026-10-05 13:00", hours=1)
    assert "clashes with 'Boiler swap' for Mrs Patel at 09:00" in clash.error
    assert _run(priced, "free_slots", day="2026-10-05").data["slots"] == [{"from": "08:00", "to": "09:00"}]
    _run(priced, "book_job", customer="Mr Jones", title="Dripping tap", start="2026-10-06 10:00", hours=1)
    assert _run(priced, "free_slots", day="2026-10-06", hours=2).data["slots"] == [
        {"from": "08:00", "to": "10:00"}, {"from": "11:00", "to": "17:00"}]
    assert _office(priced).quote("Q-0001")["status"] == "accepted", "booking a quote accepts it"
    _run(priced, "create_job_quote", customer="Mr Lee", job="Rewire", parts=[], labour_hours=2)
    _run(priced, "set_quote_status", number="Q-0002", status="declined")
    assert "declined" in _run(priced, "book_job", quote="Q-0002", start="2026-10-07 09:00").error


def test_parts_lists_per_supplier_for_the_weeks_jobs(priced):
    _run(priced, "create_job_quote", customer="Mrs Patel", job="Boiler swap",
         parts=[{"code": "COMBI-24", "qty": 1}, {"code": "FLUE-KIT", "qty": 1}], labour_hours=8)
    _run(priced, "create_job_quote", customer="Mr Lee", job="Kitchen circuit", parts=[{"code": "TE-2.5", "qty": 2}],
         labour_hours=3)
    _run(priced, "book_job", quote="Q-0001", start="2026-10-05 09:00")
    _run(priced, "book_job", quote="Q-0002", start="2026-10-06 09:00")
    _run(priced, "book_job", quote="Q-0002", start="2026-10-20 09:00", title="Second fix")   # outside the week
    lists = {p["supplier"]: p for p in _run(priced, "parts_lists", start="2026-10-05").data["parts"]}
    assert sorted(lists) == ["CEF", "City Plumbing"]
    assert [(i["code"], i["qty"]) for i in lists["CEF"]["items"]] == [("TE-2.5", 2)]
    with open(lists["City Plumbing"]["path"], encoding="utf-8") as fh:
        assert [r["code"] for r in csv.DictReader(fh)] == ["COMBI-24", "FLUE-KIT"]


def test_a_finished_job_is_invoiced_and_paid_with_approval(priced):
    _run(priced, "create_job_quote", customer="Mrs Patel", job="Boiler swap", email="patel@example.test",
         parts=[{"code": "COMBI-24", "qty": 1}, {"code": "FLUE-KIT", "qty": 1}], labour_hours=8)
    job = _run(priced, "book_job", quote="Q-0001", start="2026-10-05 09:00").data["job"]
    _run(priced, "finish_job", job_id=job["id"], notes="Fitted, commissioned, filter added")
    inv = _run(priced, "draft_job_invoice", job_id=job["id"], hours=9,
               extras=[{"description": "Magnetic filter", "amount": 120}]).data["invoice"]
    assert (inv["subtotal"], inv["vat"], inv["total"]) == (1773.0, 354.6, 2127.6)
    assert "already has an invoice" in _run(priced, "draft_job_invoice", job_id=job["id"]).error

    assert not _post(priced, "issue_job_invoice", approve=False, invoice_id=inv["id"]).ok
    assert _office(priced).invoice(inv["id"])["status"] == "draft", "a refusal changes nothing"
    issued = _post(priced, "issue_job_invoice", invoice_id=inv["id"], date="2026-10-06")
    assert issued.ok, issued.error
    i = issued.data["invoice"]
    assert (i["number"], i["due"]) == ("INV-0001", "2026-10-20") and os.path.isfile(i["file"])
    with open(i["email"], "rb") as fh:
        msg = BytesParser(policy=policy.default).parse(fh)
    assert msg["To"] == "patel@example.test" and len(list(msg.iter_attachments())) == 1

    part = _post(priced, "record_job_payment", number="inv-0001", amount=1000).data["invoice"]
    assert (part["status"], part["outstanding"]) == ("part-paid", 1127.6)
    late = _run(priced, "overdue_invoices", as_of="2026-10-25").data["overdue"]
    assert [(x["number"], x["days_late"]) for x in late] == [("INV-0001", 5)]
    assert "outstanding" in _post(priced, "record_job_payment", number="INV-0001", amount=5000).error


def test_certificates_come_due_including_leap_days(mgr):
    c = _run(mgr, "add_certificate", kind="Gas safety", customer="Mr Lee", issued="2025-10-20", valid_months=12).data
    assert c["certificate"]["due"] == "2026-10-20"
    assert _office(mgr).add_certificate("Boiler service", "Ms Kay", "2024-02-29", 12)["due"] == "2025-02-28"
    due = _run(mgr, "renewals_due", as_of="2026-10-01")
    assert [f["title"] for f in due.data["findings"]] == ["Ms Kay: Boiler service due 2025-02-28 (overdue)",
                                                           "Mr Lee: Gas safety due 2026-10-20"]
    r = _run(mgr, "draft_renewal_reminder", certificate_id=c["certificate"]["id"])
    assert r.ok and os.path.isfile(r.data["reminder"]["email_draft"])


def test_a_suppliers_price_list_imports(mgr, tmp_path):
    f = tmp_path / "cef.csv"
    f.write_text("Product Code,Description,Trade Price,Unit\nMCB-32,32A MCB type B,£6.20,each\n,no code,1,each\n",
                 encoding="utf-8-sig")
    assert _run(mgr, "import_price_list", path=str(f), supplier="CEF").data["import"] == {"added": 1, "skipped": 1}
    assert _office(mgr).material("MCB-32")["cost"] == 6.2


def test_the_evening_office_runs_as_a_workflow(priced):
    today = date.today()
    job = _office(priced).book(customer="Mr Jones", title="Dripping tap", hours=1,
                               start=f"{(today - timedelta(days=1)).isoformat()} 10:00")
    _office(priced).finish(job["id"])
    task = priced.wait(priced.submit("do my paperwork").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert [f["title"] for f in task.metadata.get("findings", [])] == [
        f"Job #{job['id']} for Mr Jones is done but not invoiced"]
