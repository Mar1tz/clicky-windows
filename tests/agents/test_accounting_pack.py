"""The Accounting extension on the demo books: no credentials, no network.

Covers the spec's test list: extension loading/unloading, provider
abstraction, permissions, confidence thresholds, duplicate invoices,
reconciliation workflow, month-end workflow, crash recovery and restart,
missing documents, user approval, and sensitive-action blocking.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import (
    PermissionPolicy, RiskLevel, Task, TaskStatus, ToolCall, ToolContext,
)
from extensions.accounting.books import Ledger
from extensions.accounting.demo import seed_demo, seed_inbox
from extensions.accounting.permissions import ActionLevel, ConfidencePolicy
from extensions.accounting.providers import CSVExcelProvider, LocalBooksProvider, MockAccountingProvider
from extensions.accounting.tools import TOOL_LEVELS
from extensions.accounting.workspace import ClientWorkspace


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("accounting")
    m.start()
    yield m
    m.stop()


def _ext(mgr):
    return mgr.extensions.get("accounting")


def _demo(mgr) -> ClientWorkspace:
    ext = _ext(mgr)
    return ext.create_demo_client(mgr.extensions._context("accounting"))


def _run(mgr, ws: ClientWorkspace, tool: str, **args):
    """Run one accounting tool against a client, as the agent would, with a
    policy that auto-approves up to LOW (READ and DRAFT)."""
    ctx = ToolContext(workspace=ws.root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


# -- loading / unloading -------------------------------------------------------------------------------

def test_enable_registers_the_pack_and_disable_removes_it(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    core = set(m.tools.names())
    m.extensions.enable("accounting")
    added = set(m.tools.names()) - core
    assert {"get_trial_balance", "create_draft_bill", "post_drafts", "release_payment_batch"} <= added
    assert all(m.tools.get(n).spec.category == "accounting" for n in added)
    assert "accounting" in m.workers.types()
    assert m.understand("please prepare month-end for ABC Manufacturing").metadata.get("template") == "month_end"
    m.extensions.disable("accounting")
    assert set(m.tools.names()) == core
    assert "accounting" not in m.workers.types()
    assert m.extensions.match_intent("prepare month-end for ABC") is None


def test_every_tool_declares_a_level_that_matches_its_risk(mgr):
    for name, level in TOOL_LEVELS.items():
        tool = mgr.tools.get(name)
        lvl = ActionLevel(level)
        assert tool.spec.risk is lvl.risk, name
        assert bool(getattr(tool, "always_ask", False)) == lvl.always_ask, name
        assert bool(getattr(tool, "sensitive", False)) == lvl.sensitive, name
    assert TOOL_LEVELS["post_drafts"] == "post"
    assert TOOL_LEVELS["release_payment_batch"] == TOOL_LEVELS["submit_tax_return"] == "sensitive"
    assert TOOL_LEVELS["create_draft_journal"] == "draft" and TOOL_LEVELS["get_trial_balance"] == "read"


def test_questions_stay_with_the_conversation(mgr):
    for q in ("what is a month-end?", "how do accruals work", "explain the trial balance"):
        intent = mgr.understand(q)
        assert intent.metadata.get("extension") != "accounting", q


# -- provider abstraction ----------------------------------------------------------------------------------

def test_the_same_questions_work_against_three_providers(tmp_path):
    mock = MockAccountingProvider(str(tmp_path / "m" / "books.sqlite"))
    local = LocalBooksProvider(str(tmp_path / "l" / "books.sqlite"))
    with Ledger(local.path) as L:
        L.setup("Local Co", tax_rate=15)
        L.expense("2026-08-03", "Office chairs", 1150, "Office Supplies")
    data = tmp_path / "csv"
    data.mkdir()
    (data / "trial_balance.csv").write_text("Code,Name,Debit,Credit\n1000,Bank,500.00,\n4000,Sales,,500.00\n",
                                            encoding="utf-8")
    (data / "bank_aug.csv").write_text("Date,Description,Amount\n2026-08-01,RENT,-100.00\n", encoding="utf-8")
    csvp = CSVExcelProvider(str(data))

    async def ask(p):
        tb = await p.trial_balance("2026-08-31")
        lines = await p.bank_transactions()
        await p.invoices("sales", "all")
        return tb["balanced"], len(tb["rows"]) > 0, isinstance(lines, list)
    for p in (mock, local, csvp):
        assert asyncio.run(ask(p)) == (True, True, True), p.name
    assert mock.supports("submit_tax_return") and not local.supports("submit_tax_return")
    assert not csvp.supports("release_payment_batch")
    ref = asyncio.run(csvp.post_journal("2026-08-31", "Accrual", [{"account": "6800", "debit": 10},
                                                                  {"account": "2400", "credit": 10}]))
    assert ref.startswith("exported:") and os.path.isfile(os.path.join(csvp.exports_dir, "journals_for_import.csv"))
    for p in (mock, local):
        p.close()


def test_unconnected_cloud_providers_say_so(mgr, tmp_path):
    ext = _ext(mgr)
    ctx = mgr.extensions._context("accounting")
    ws = ClientWorkspace.create(ext.clients_root(ctx), "Cloud Co", provider="xero")
    r = _run(mgr, ws, "get_trial_balance")
    assert not r.ok and "not connected" in r.error


# -- confidence thresholds ------------------------------------------------------------------------------------

def test_confidence_policy_routes():
    p = ConfidencePolicy(auto=0.95, flag=0.70)
    assert p.route(0.97) == "draft" and p.route(0.74) == "review" and p.route(0.42) == "ask"
    assert p.route(97) == "draft", "percentages are understood"
    assert ConfidencePolicy(auto=0.9, flag=0.99).flag == 0.9, "flag can never exceed auto"


def test_categorisation_obeys_the_client_threshold(mgr):
    ws = _demo(mgr)
    lines = _run(mgr, ws, "get_bank_transactions", unreconciled_only=True).data["lines"]
    deposit = next(l for l in lines if "CASH DEPOSIT" in l["description"])
    low = _run(mgr, ws, "categorize_transaction", line_id=deposit["id"], account="Other Income", confidence=0.42)
    assert low.ok and low.data["route"] == "ask" and not low.data.get("draft")
    assert low.data["findings"], "a refused categorisation asks the accountant"
    mid = _run(mgr, ws, "categorize_transaction", line_id=deposit["id"], account="Other Income", confidence=0.74)
    assert mid.data["route"] == "review" and mid.data["draft"]["status"] == "review"
    ws.confidence = {"auto": 0.7, "flag": 0.5}
    ws.save()
    debit = next(l for l in lines if "DEBIT ORDER" in l["description"])
    hi = _run(mgr, ws, "categorize_transaction", line_id=debit["id"], account="Bank Charges", confidence=0.74)
    assert hi.data["route"] == "draft", "the client's own thresholds apply"


# -- duplicates ---------------------------------------------------------------------------------------------------

def test_duplicate_invoices_are_found_and_never_drafted_again(mgr):
    ws = _demo(mgr)
    r = _run(mgr, ws, "detect_duplicate_invoice")
    assert r.ok and any("AP-7731" in f["title"] for f in r.data["findings"])
    again = _run(mgr, ws, "create_draft_bill", supplier="FastFreight Couriers", date="2026-08-06",
                 reference="FF-551", total=8050, confidence=0.99)
    assert again.ok and again.data["route"] == "duplicate" and not again.data.get("draft")
    pay = _run(mgr, ws, "detect_duplicate_payment", period="2026-08")
    titles = [f["title"] for f in pay.data["findings"]]
    assert any("FastFreight" in t and "duplicate" in t for t in titles)
    assert any("84,200" in t and "no matching invoice" in t for t in titles)


# -- the invoice workflow on real PDFs -------------------------------------------------------------------------------

def test_invoice_processing_drafts_the_clean_one_and_flags_the_rest(mgr):
    ws = _demo(mgr)
    r = _run(mgr, ws, "process_inbox")
    assert r.ok, r.error
    by_file = {x["file"]: x for x in r.data["results"]}
    assert by_file["Acme_Packaging_AP-7790.pdf"]["status"] == "draft", by_file
    assert by_file["FastFreight_FF-551.pdf"]["result"] == "duplicate"
    assert by_file["invoice_BrightSpark_Electrical_BSE-104.pdf"]["result"] in ("review", "ask")
    assert by_file["receipt_Seattle_Coffee.pdf"]["result"] == "skipped"
    titles = " | ".join(f["title"] for f in r.data["findings"])
    assert "New supplier BrightSpark Electrical" in titles
    again = _run(mgr, ws, "process_inbox")
    assert all(x["result"] in ("already", "skipped") for x in again.data["results"]), "nothing is booked twice"
    assert os.listdir(os.path.join(ws.root, "Documents", "Invoices"))


def test_supplier_statement_explains_the_unmatched_payment(mgr):
    ws = _demo(mgr)
    r = _run(mgr, ws, "reconcile_inbox_statements")
    assert r.ok and r.data["reconciled"] == 1
    assert any("SS-2250" in f["title"] and "not in our books" in f["title"] for f in r.data["findings"])


# -- reconciliation workflow ------------------------------------------------------------------------------------------

def test_bookkeeping_imports_the_statement_and_drafts_from_suggestions(mgr):
    ws = _demo(mgr)
    r = _run(mgr, ws, "import_inbox_statements")
    assert r.ok and r.data["imported"] == 5
    assert not os.listdir(os.path.join(ws.root, "Inbox", "Bank")), "the statement is filed after import"
    before = _run(mgr, ws, "get_trial_balance").data["trial_balance"]
    d = _run(mgr, ws, "draft_from_suggestions")
    assert d.ok and len(d.data["drafted"]) >= 3, d.text
    after = _run(mgr, ws, "get_trial_balance").data["trial_balance"]
    assert before == after, "drafting never touches the books"
    s = _ext(mgr).sessions.get(ws.root)
    drafts = s.drafts.list(status="draft,review")
    assert {x["kind"] for x in drafts} >= {"bank_line"}


# -- month-end workflow ----------------------------------------------------------------------------------------------

PLANTED = ["Receivables control differs", "Payables control differs", "Highveld Mining", "AP-7731 entered 2 times",
           "FastFreight Couriers looks like a duplicate", "R84,200.00 to Steelco Supplies has no matching invoice",
           "same bank account", "Wages in the ledger differ", "Unusual journal", "CASH DEPOSIT",
           "Suspense holds R3,150", "Santam", "City Power", "Sipho Dlamini", "UBER", "MICROSOFT",
           "3 VAT transactions missing support", "Advertising & Marketing"]


def test_month_end_runs_offline_and_surfaces_every_planted_exception(mgr):
    task = mgr.submit("Please prepare month-end for ABC Manufacturing")
    assert task.metadata["template"] == "month_end" and task.worker_type == "accounting"
    done = mgr.wait(task.id, 120)
    assert done.status is TaskStatus.COMPLETED, done.error
    titles = " | ".join(f["title"] for f in done.metadata["findings"])
    for needle in PLANTED:
        assert needle in titles, f"month-end missed: {needle}"
    assert "checks completed automatically" in done.result and "Needs review" in done.result
    ws = ClientWorkspace.load(done.workspace)
    assert ws.month_end["status"] == 1.0 and ws.month_end["period"] == "2026-08"
    for f in ("2026-08 Management pack", "2026-08 Exceptions.md", "2026-08 Variance", "2026-08 Bank rec 1000"):
        assert any(f in name for _d, _s, files in os.walk(ws.root) for name in files), f


def test_the_books_are_unchanged_by_a_month_end(mgr):
    ws = _demo(mgr)
    before = _run(mgr, ws, "get_trial_balance").data["trial_balance"]
    done = mgr.wait(mgr.submit("prepare month-end for ABC").id, 120)
    assert done.status is TaskStatus.COMPLETED
    assert _run(mgr, ws, "get_trial_balance").data["trial_balance"] == before


def test_missing_documents(mgr):
    ws = _demo(mgr)
    r = _run(mgr, ws, "find_missing_document", period="2026-08")
    assert r.ok
    assert len(r.data["taxed"]) == 3
    assert {x["description"][:10] for x in r.data["taxed"]} >= {"GOOGLE ADS", "UBER - TRI"}


# -- approvals: POST asks, SENSITIVE asks with a word and can never be granted -----------------------------------------

def _wait_for_approval(mgr, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = mgr.pending_approvals()
        if pending:
            return pending[0]
        time.sleep(0.02)
    raise AssertionError("no approval was requested")


def _as_task(mgr, ws, tool, args, *, policy=RiskLevel.HIGH):
    task = mgr.submit_task(Task(title="t", goal="accounting", workspace=ws.root,
                                metadata={"extension": "accounting"}))
    mgr.wait(task.id, 30)
    ctx = ToolContext(workspace=ws.root, task_id=task.id, policy=PermissionPolicy(max_auto_risk=policy),
                      approver=mgr._approver_for(task))
    return task, mgr.runtime.submit(mgr.tools.execute(ToolCall(tool, args), ctx))


def test_posting_needs_approval_and_denial_changes_nothing(mgr):
    ws = _demo(mgr)
    d = _run(mgr, ws, "create_draft_journal", date="2026-08-31", description="Accrue August electricity",
             lines=[{"account": "Utilities", "debit": 6150}, {"account": "Accruals", "credit": 6150}],
             confidence=0.97, reverse_on="2026-09-01").data["draft"]
    before = _run(mgr, ws, "get_trial_balance").data["trial_balance"]
    _task, fut = _as_task(mgr, ws, "post_drafts", {"draft_ids": [d["id"]]})
    req = _wait_for_approval(mgr)
    assert not req.sensitive and "changes the books" in req.reason
    mgr.approve(req.id, False)
    r = fut.result(10)
    assert not r.ok and "denied" in r.error.lower()
    assert _run(mgr, ws, "get_trial_balance").data["trial_balance"] == before

    _task, fut = _as_task(mgr, ws, "post_drafts", {"draft_ids": [d["id"]]})
    mgr.approve(_wait_for_approval(mgr).id, True)
    r = fut.result(10)
    assert r.ok, r.error
    s = _ext(mgr).sessions.get(ws.root)
    assert s.drafts.get(d["id"])["status"] == "posted"
    assert _run(mgr, ws, "get_trial_balance").data["trial_balance"] != before


def test_even_a_permissive_policy_cannot_skip_the_post_approval(mgr):
    ws = _demo(mgr)
    d = _run(mgr, ws, "create_draft_journal", date="2026-08-31", description="Reclass",
             lines=[{"account": "6600", "debit": 1600}, {"account": "7200", "credit": 1600}],
             confidence=0.99).data["draft"]
    task = mgr.submit_task(Task(title="t", goal="x", workspace=ws.root, metadata={"extension": "accounting"}))
    mgr.wait(task.id, 30)
    ctx = ToolContext(workspace=ws.root, task_id=task.id,
                      policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH, always_allow=["post_drafts"]),
                      approver=mgr._approver_for(task))
    fut = mgr.runtime.submit(mgr.tools.execute(ToolCall("post_drafts", {"draft_ids": [d["id"]]}), ctx))
    req = _wait_for_approval(mgr)
    assert req.call.tool_name == "post_drafts", "always_ask beats always_allow"
    mgr.approve(req.id, False)
    fut.result(10)


def test_sensitive_actions_are_blocked_without_the_typed_word(mgr):
    ws = _demo(mgr)
    batch = _run(mgr, ws, "create_draft_payment_batch", due_by="2026-09-30")
    assert batch.ok
    d = batch.data["draft"]
    held = " | ".join(h["held"] for h in batch.data["held"])
    assert "duplicate" in held, "the second AP-7731 is held back"
    task, fut = _as_task(mgr, ws, "release_payment_batch", {"draft_id": d["id"]})
    mgr.grant_for_task(task.id, "release_payment_batch")
    assert "release_payment_batch" not in mgr.granted_for_task(task.id)
    req = _wait_for_approval(mgr)
    assert req.sensitive and req.confirm_word == "RELEASE"
    assert mgr.approve(req.id, True) is False, "voice / bare approve refused"
    assert mgr.approve(req.id, True, confirmation="nope") is False
    assert mgr.approve(req.id, False) is True
    r = fut.result(10)
    assert not r.ok
    s = _ext(mgr).sessions.get(ws.root)
    assert s.drafts.get(d["id"])["status"] == "review", "nothing was released"

    task, fut = _as_task(mgr, ws, "release_payment_batch", {"draft_id": d["id"]})
    req = _wait_for_approval(mgr)
    assert mgr.approve(req.id, True, confirmation="RELEASE") is True
    r = fut.result(10)
    assert r.ok and "MOCK-PAY" in r.output
    assert s.drafts.get(d["id"])["status"] == "released"


def test_submitting_a_tax_return_is_sensitive_and_local_books_refuse(mgr):
    ws = _demo(mgr)
    pack = _run(mgr, ws, "prepare_tax_pack", period="2026-08")
    assert pack.ok and pack.data["draft"]["kind"] == "tax_return"
    tool = mgr.tools.get("submit_tax_return")
    assert tool.sensitive and tool.confirm_word == "SUBMIT"


# -- crash recovery / restart ----------------------------------------------------------------------------------------------

def test_restart_restores_the_pack_the_clients_and_the_drafts(tmp_path):
    from agents.bootstrap import attach_extensions
    from agents.manager import AgentManager

    def build():
        m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
        # simulate a persistent profile inside tmp_path
        from agents.extensions import ExtensionRegistry
        from agents.manager.scheduler import Scheduler
        m.scheduler = Scheduler(m, state_path=str(tmp_path / "state" / "schedules.json"))
        m.extensions = ExtensionRegistry(m, state_path=str(tmp_path / "state" / "extensions.json"),
                                         data_root=str(tmp_path / "state"))
        m.extensions.discover()
        m.extensions.restore()
        return m
    m1 = build()
    m1.extensions.enable("accounting")
    m1.start()
    ws = _demo(m1)
    d = _run(m1, ws, "create_draft_journal", date="2026-08-31", description="Accrual",
             lines=[{"account": "6800", "debit": 100}, {"account": "2400", "credit": 100}], confidence=0.99)
    m1.extensions.configure("accounting", {"agents": [{"id": "ar1", "template": "accounts_receivable",
                                                       "client": ws.client, "schedule": {"every": "weekdays",
                                                                                         "at": "08:00"}}]})
    assert any(j["id"] == "accounting:ar1" for j in m1.scheduler.jobs())
    m1.stop()

    m2 = build()                                  # a new process
    assert m2.extensions.is_enabled("accounting"), "enabled state survives a restart"
    assert "accounting" in m2.workers.types()
    assert any(j["id"] == "accounting:ar1" for j in m2.scheduler.jobs()), "configured agents come back"
    m2.start()
    s = m2.extensions.get("accounting").sessions.get(ws.root)
    assert s.drafts.get(d.data["draft"]["id"])["status"] == "draft", "drafts survive"
    m2.stop()


def test_an_interrupted_month_end_resumes_where_it_stopped(tmp_path):
    """A crash cannot be staged inside one process (see test_resume), so the
    store is written as a crash would leave it: five checks done, the sixth
    RUNNING, the rest PENDING. A new process brings it back PAUSED; resume
    runs only what is left and the close completes."""
    from agents.core import StepStatus
    db = tmp_path / "agents.db"
    m1 = build_agent_manager(workspace_root=str(tmp_path), db_path=str(db), offline=True)
    m1.extensions.enable("accounting")
    m1.start()
    done = m1.wait(m1.submit("prepare month-end for ABC").id, 120)
    assert done.status is TaskStatus.COMPLETED
    crashed = Task(title="Month-end (crashed)", goal=done.goal, workspace=done.workspace, kind=done.kind,
                   worker_type=done.worker_type, metadata={k: v for k, v in done.metadata.items()
                                                           if k not in ("findings", "checks_run")})
    crashed.status = TaskStatus.RUNNING
    for i, st in enumerate(done.steps):
        from agents.core import Step
        copy = Step(title=st.title, description=st.description, tool_calls=list(st.tool_calls))
        copy.status = StepStatus.DONE if i < 5 else StepStatus.RUNNING if i == 5 else StepStatus.PENDING
        if i < 5:
            copy.summary = "done before the crash"
            copy.results = list(st.results)          # persisted with the step, as a real crash leaves them
        crashed.steps.append(copy)
    m1.store.save_task(crashed)
    m1.stop()

    m2 = build_agent_manager(workspace_root=str(tmp_path), db_path=str(db), offline=True)
    assert m2.extensions.is_enabled("accounting"), "the pack comes back on with the process"
    m2.start()
    recovered = m2.get_task(crashed.id)
    assert recovered is not None and recovered.status is TaskStatus.PAUSED
    assert m2.resume(crashed.id)
    finished = m2.wait(crashed.id, 120)
    assert finished.status is TaskStatus.COMPLETED, finished.error
    assert [st.summary for st in finished.steps[:5]] == ["done before the crash"] * 5, "done steps are not re-run"
    assert "Month-End Complete" in (finished.steps[-1].summary or "")
    m2.stop()
