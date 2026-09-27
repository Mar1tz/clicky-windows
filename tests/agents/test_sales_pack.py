"""The Sales CRM pack on a fresh folder: no credentials, no network.

The pipeline's arithmetic (weighted forecast, win rate, days to win),
follow-ups that come due and close when the next thing is logged, deals
that go quiet, a CSV import, and email drafts that are never sent.
"""
from __future__ import annotations

from datetime import date, timedelta
from email import policy
from email.parser import BytesParser

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, TaskStatus, ToolCall, ToolContext

TODAY = date.today()


def ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("sales")
    m.extensions.configure("sales", {"currency": "GBP", "stale_days": 14})
    m.start()
    yield m
    m.stop()


def _crm(mgr):
    return mgr.extensions.get("sales").ensure_store(mgr.extensions._context("sales"))


def _run(mgr, tool: str, **args):
    ctx = ToolContext(workspace=_crm(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def test_nothing_in_the_pack_sends(mgr):
    assert set(mgr.extensions.get("sales").TOOLS.levels().values()) == {"read", "draft"}


def test_the_forecast_is_weighted_by_stage(mgr):
    month = TODAY.isoformat()[:7]
    expected = TODAY.replace(day=28).isoformat()
    _run(mgr, "add_contact", name="Priya Shah", company="Northwind", email="priya@northwind.test")
    _run(mgr, "add_deal", title="Northwind rollout", contact="Priya Shah", value=40000, stage="proposal",
         expected=expected)
    _run(mgr, "add_deal", title="Contoso pilot", company="Contoso", value=10000, stage="negotiation", expected=expected)
    _run(mgr, "add_deal", title="Fabrikam", value=5000, stage="lead")
    f = _run(mgr, "sales_forecast", month=month)
    assert f.data["forecast"]["best_case"] == 50000 and f.data["forecast"]["weighted"] == 40000 * .5 + 10000 * .75
    assert [x["title"] for x in f.data["findings"]] == ["1 open deal(s) have no expected close date"]
    assert "GBP 27,500" in f.output

    s = _crm(mgr)
    won = s.add_deal("Old win", value=8000)
    s.db.execute("UPDATE deals SET created = ? WHERE id = ?", (ago(30) + "T09:00:00", won["id"]))
    s.db.commit()
    s.move(won["id"], "won")
    lost = s.add_deal("Old loss", value=3000)
    assert not _run(mgr, "move_deal", deal=str(lost["id"]), stage="lost").ok, "lost needs a reason"
    s.move(lost["id"], "lost", reason="went with a cheaper supplier")
    p = s.pipeline()
    assert (p["win_rate"], p["avg_days_to_win"], p["open_value"]) == (50, 30, 55000)
    assert s.forecast(month)["won"] == 8000


def test_follow_ups_come_due_and_close_when_the_next_thing_is_logged(mgr):
    _run(mgr, "add_contact", name="Priya Shah", company="Northwind", email="priya@northwind.test")
    _run(mgr, "add_deal", title="Northwind rollout", contact="Priya Shah", value=40000, stage="qualified")
    _run(mgr, "log_activity", deal="Northwind", kind="call", note="Wants a demo for finance", next_date=ago(1))
    due = _run(mgr, "follow_ups_due").data["follow_ups"]
    assert [(r["deal"], r["days_late"]) for r in due] == [("Northwind rollout", 1)]
    _run(mgr, "log_activity", deal="Northwind", kind="meeting", note="Demo done",
         next_date=(TODAY + timedelta(days=3)).isoformat())
    assert _run(mgr, "follow_ups_due").data["follow_ups"] == [], "the old follow-up is done"
    assert not _run(mgr, "log_activity", kind="call", note="to whom?").ok
    assert not _run(mgr, "log_activity", deal="Northwind", kind="fax").ok


def test_quiet_deals_are_caught_unless_something_is_planned(mgr):
    s = _crm(mgr)
    for title, value, touched in (("Big quiet", 90000, 30), ("Small quiet", 1000, 20), ("Fresh", 5000, 2)):
        d = s.add_deal(title, value=value)
        s.log(d["id"], "note", "hello", when=ago(touched))
    planned = s.add_deal("Quiet but planned", value=7000)
    s.log(planned["id"], "call", "left a message", when=ago(40), next_date=(TODAY + timedelta(days=2)).isoformat())
    r = _run(mgr, "stale_deals")
    assert [d["title"] for d in r.data["stale"]] == ["Big quiet", "Small quiet"], "biggest first"
    assert r.data["findings"][0]["title"] == "Big quiet: no contact for 30 days (GBP 90,000)"


def test_contacts_import_from_a_crm_export(mgr, tmp_path):
    export = tmp_path / "hubspot.csv"
    export.write_text("First Name,Last Name,Company Name,Email,Phone Number\n"
                      "Priya,Shah,Northwind,priya@northwind.test,+44 20 7946 0000\n"
                      "Tom,Reed,Contoso,not-an-email,\n"
                      ",,Nobody Ltd,x@y.test,\n", encoding="utf-8-sig")
    r = _run(mgr, "import_contacts", path=str(export)).data["import"]
    assert r == {"added": 2, "updated": 0, "skipped": 1}
    assert _run(mgr, "import_contacts", path=str(export)).data["import"]["updated"] == 2, "a second import updates"
    c = _run(mgr, "find_contact", query="priya@northwind.test").data["contact"]
    assert (c["name"], c["company"], c["phone"]) == ("Priya Shah", "Northwind", "+44 20 7946 0000")
    assert _run(mgr, "find_contact", query="Tom Reed").data["contact"]["email"] == "", "a bad email is dropped"


def test_a_follow_up_email_is_a_draft_in_outbox(mgr):
    _run(mgr, "add_contact", name="Priya Shah", company="Northwind", email="priya@northwind.test")
    _run(mgr, "add_deal", title="Northwind rollout", contact="Priya", value=40000)
    r = _run(mgr, "draft_follow_up_email", deal="Northwind", subject="Next steps",
             body="Hi Priya, here is the pricing we discussed.")
    assert r.ok and r.data["findings"] == []
    with open(r.data["email"]["path"], "rb") as fh:
        msg = BytesParser(policy=policy.default).parse(fh)
    assert (msg["To"], msg["Subject"], msg["X-Unsent"]) == ("priya@northwind.test", "Next steps", "1")
    _run(mgr, "add_deal", title="Walk-in", value=100)
    assert "no contact" in _run(mgr, "draft_follow_up_email", deal="Walk-in", body="Hi").error


def test_the_morning_pipeline_runs_as_a_workflow(mgr):
    s = _crm(mgr)
    d = s.add_deal("Big quiet", value=90000)
    s.log(d["id"], "note", "hello", when=ago(30))
    task = mgr.wait(mgr.submit("check my pipeline").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert [f["title"] for f in task.metadata.get("findings", [])] == ["Big quiet: no contact for 30 days (GBP 90,000)"]


def test_requests_route_to_the_sales_assistant(mgr):
    for text, workflow in (("check my pipeline", "morning_pipeline"), ("run the sales forecast", "weekly_forecast"),
                           ("log a call with Priya", ""), ("add a new deal for Contoso", ""),
                           ("draft a follow-up email to Priya", "")):
        intent = mgr.extensions.match_intent(text)
        assert intent is not None and intent.metadata["template"] == "sales_assistant", text
        assert intent.metadata.get("workflow", "") == workflow, text
