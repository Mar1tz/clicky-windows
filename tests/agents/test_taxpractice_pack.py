"""The Tax practice pack on a fresh folder: no credentials, no network.

Deadlines from a date or the practice's own rule, checklists per return
type, chasers that escalate a week apart and then stop, a workpaper that
ties out, filing that needs every document and the tax filed, and the
assessment checked against it.
"""
from __future__ import annotations

from datetime import date, timedelta
from email import policy
from email.parser import BytesParser

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, TaskStatus, ToolCall, ToolContext

D = date(2026, 10, 1)


def day(n: int) -> str:
    return (D + timedelta(days=n)).isoformat()


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("taxpractice")
    m.extensions.configure("taxpractice", {"firm": "Hale & Co", "deadline_rules": {"company": {"months": 9, "days": 1}}})
    m.start()
    yield m
    m.stop()


def _p(mgr):
    return mgr.extensions.get("taxpractice").ensure_store(mgr.extensions._context("taxpractice"))


def _run(mgr, tool: str, **args):
    ctx = ToolContext(workspace=_p(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def test_nothing_is_filed_or_sent(mgr):
    assert set(mgr.extensions.get("taxpractice").TOOLS.levels().values()) == {"read", "draft"}


def test_deadlines_from_a_date_or_the_practices_rule(mgr):
    r = _run(mgr, "add_tax_return", client="Mrs Hale", kind="individual", year="2025-26", period_end="2026-04-05",
             due="2027-01-31", email="hale@example.test").data["return"]
    assert r["due"] == "2027-01-31" and len(r["missing"]) == 6, "the individual checklist"
    c = _run(mgr, "add_tax_return", client="Ode Ltd", kind="company", year="2026", period_end="2026-03-31").data["return"]
    assert c["due"] == "2027-01-01", "nine months and a day after the period end"
    assert "when is a vat return due" in _run(mgr, "add_tax_return", client="Ode Ltd", kind="vat", year="Q3",
                                               period_end="2026-09-30").error.lower()
    assert "already has" in _run(mgr, "add_tax_return", client="mrs hale", kind="individual", year="2025-26",
                                 period_end="2026-04-05", due="2027-01-31").error


def test_documents_in_and_the_return_moves_on_when_all_are_there(mgr):
    _run(mgr, "add_tax_return", client="Mr Ode", kind="vat", year="Q3", period_end="2026-09-30", due="2026-11-07")
    r = _run(mgr, "record_documents", client="Ode", documents=["sales listing", "purchase", "invoices from Mars"])
    assert r.data["return"]["missing"] == ["Bank statements"]
    assert [f["title"] for f in r.data["findings"]] == ["Not on Mr Ode's checklist: invoices from Mars"]
    assert _run(mgr, "record_documents", client="Ode", documents=["bank statements"]).data["return"]["stage"] == "preparing"


def test_chasers_escalate_a_week_apart_then_stop(mgr):
    _run(mgr, "add_tax_return", client="Mrs Hale", kind="individual", year="2025-26", period_end="2026-04-05",
         due="2027-01-31", email="hale@example.test")
    _run(mgr, "record_documents", client="Hale", documents=["bank interest", "dividends", "pension", "gift aid",
                                                            "other income"])
    first = _run(mgr, "chase_documents", as_of=day(0)).data["chase"]
    assert [(x["client"], x["tone"]) for x in first["drafted"]] == [("Mrs Hale", "friendly")]
    with open(first["drafted"][0]["path"], "rb") as fh:
        msg = BytesParser(policy=policy.default).parse(fh)
    assert msg["To"] == "hale@example.test" and "- Employment income summary" in msg.get_content()
    assert _run(mgr, "chase_documents", as_of=day(3)).data["chase"]["waiting"] == ["Mrs Hale"], "not twice a week"
    assert _run(mgr, "chase_documents", as_of=day(7)).data["chase"]["drafted"][0]["tone"] == "firm"
    final = _run(mgr, "chase_documents", as_of=day(14)).data["chase"]["drafted"][0]
    with open(final["path"], "rb") as fh:
        msg = BytesParser(policy=policy.default).parse(fh)
    assert final["tone"] == "final" and "(final reminder)" in msg["Subject"] and "penalties" in msg.get_content()
    after = _run(mgr, "chase_documents", as_of=day(21))
    assert after.data["chase"]["drafted"] == []
    assert [f["title"] for f in after.data["findings"]] == [
        "Mrs Hale: three chasers sent for the individual 2025-26 return and still missing documents"]


def test_a_workpaper_totals_by_category_and_ties_out(mgr, tmp_path):
    _run(mgr, "add_tax_return", client="Mrs Hale", kind="individual", year="2025-26", period_end="2026-04-05",
         due="2027-01-31")
    f = tmp_path / "hale.csv"
    f.write_text("date,description,amount,category\n2025-05-01,Acme Ltd SALARY,2500.00,\n"
                 "2025-06-01,Acme Ltd SALARY,2500.00,\n2025-06-30,Bank interest,12.40,\n"
                 "2025-07-02,Dividend Vodafone,80.00,\n2025-08-15,Transfer from J Hale,300.00,\n"
                 "2025-09-01,Freelance design,\"1,200.00\",Self-employment\n", encoding="utf-8")
    w = _run(mgr, "build_workpaper", client="Hale", path=str(f))
    assert w.ok, w.error
    wp = w.data["workpaper"]
    assert wp["totals"] == {"Salary": 5000.0, "Interest": 12.4, "Dividends": 80.0, "Not yet categorised": 300.0,
                            "Self-employment": 1200.0}
    assert wp["ties_out"] and wp["total"] == 6592.4
    assert w.data["findings"][0]["title"] == "Mrs Hale: 1 transaction(s) need a category"


def test_filing_needs_every_document_and_the_tax_filed_then_the_assessment_is_checked(mgr):
    _run(mgr, "add_tax_return", client="Mr Ode", kind="vat", year="Q3", period_end="2026-09-30", due="2026-11-07")
    assert "still missing" in _run(mgr, "set_return_stage", client="Ode", stage="filed", filed_tax=1840).error
    _run(mgr, "record_documents", client="Ode", documents=["sales listing", "purchase listing", "bank statements"])
    assert "tax on the return" in _run(mgr, "set_return_stage", client="Ode", stage="filed").error
    assert _run(mgr, "set_return_stage", client="Ode", stage="filed", filed_tax=1840).data["return"]["stage"] == "filed"
    off = _run(mgr, "record_assessment", client="Ode", assessed_tax=2040)
    assert [f["title"] for f in off.data["findings"]] == ["Mr Ode: assessed 2,040.00 but filed 1,840.00 (+200.00)"]
    _run(mgr, "add_tax_return", client="Mrs Hale", kind="vat", year="Q3", period_end="2026-09-30", due="2026-11-07",
         documents=[])
    _run(mgr, "set_return_stage", client="Hale", stage="filed", filed_tax=500)
    assert _run(mgr, "record_assessment", client="Hale", assessed_tax=500.5).data["return"]["agrees"]


def test_the_weekly_deadlines_run_flags_late_and_urgent(mgr):
    today = date.today()
    _run(mgr, "add_tax_return", client="Late Ltd", kind="vat", year="Q1", period_end=(today - timedelta(days=60)).isoformat(),
         due=(today - timedelta(days=3)).isoformat(), documents=[])
    _run(mgr, "add_tax_return", client="Soon Ltd", kind="vat", year="Q2", period_end=(today - timedelta(days=20)).isoformat(),
         due=(today + timedelta(days=10)).isoformat())
    task = mgr.wait(mgr.submit("check the tax deadlines").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    titles = [f["title"] for f in task.metadata.get("findings", [])]
    assert any(t.startswith("Late Ltd: vat Q1 was due") for t in titles)
    assert "Soon Ltd: vat Q2 is due in 10 days and 3 document(s) are missing" in titles
    assert len(_p(mgr).rows("SELECT * FROM chases")) == 1, "Soon Ltd was chased; Late Ltd needs nothing"
