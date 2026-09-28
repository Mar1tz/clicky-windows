"""The Household finance pack on a fresh folder: no credentials, no network.

Statements from two banks' formats imported once, categories by words and
by what the household teaches, a budget overrun, subscriptions found (with
a price rise; a stopped one left out), bills due this week, warranties,
the month's report.
"""
from __future__ import annotations

import os

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, TaskStatus, ToolCall, ToolContext

AS_OF = "2026-09-28"
ROWS = [("2026-03-10", "SPOTIFY P1234", -11.99), ("2026-04-10", "SPOTIFY P1235", -11.99),
        ("2026-05-10", "SPOTIFY P1236", -11.99)]
for m, netflix in (("06", 10.99), ("07", 10.99), ("08", 10.99), ("09", 12.99)):
    ROWS += [(f"2026-{m}-01", "DD HOVE LETTINGS RENT", -950.0), (f"2026-{m}-05", "NETFLIX.COM 866-579", -netflix),
             (f"2026-{m}-25", "ACME LTD SALARY", 2400.0)]
ROWS += [("2026-09-12", "TESCO STORES 2231", -62.40), ("2026-09-19", "TESCO STORES 2231", -38.10),
         ("2026-09-20", "CAFE NERO", -4.20), ("2026-09-20", "CAFE NERO", -4.20),
         ("2026-09-21", "JOHN SMITH TRANSFER", -50.0)]


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("household")
    m.start()
    yield m
    m.stop()


@pytest.fixture
def imported(mgr, tmp_path):
    f = tmp_path / "current.csv"
    f.write_text("Date,Description,Amount\n" + "".join(f"{d},{desc},{amt:.2f}\n" for d, desc, amt in ROWS),
                 encoding="utf-8-sig")
    r = _run(mgr, "import_bank_statement_csv", path=str(f), account="current").data["import"]
    assert r == {"added": len(ROWS), "already_there": 0, "uncategorised": 1}
    return f


def _h(mgr):
    return mgr.extensions.get("household").ensure_store(mgr.extensions._context("household"))


def _run(mgr, tool: str, **args):
    ctx = ToolContext(workspace=_h(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def test_nothing_is_paid_or_cancelled(mgr):
    assert set(mgr.extensions.get("household").TOOLS.levels().values()) == {"read", "draft"}


def test_a_statement_is_imported_once_and_two_coffees_stay_two(imported, mgr):
    again = _run(mgr, "import_bank_statement_csv", path=str(imported), account="current").data["import"]
    assert (again["added"], again["already_there"]) == (0, len(ROWS))
    assert len(_h(mgr).rows("SELECT * FROM txns WHERE description = 'CAFE NERO'")) == 2


def test_money_in_and_money_out_columns_and_other_date_styles(mgr, tmp_path):
    f = tmp_path / "savings.csv"
    f.write_text("Transaction Date,Details,Money In,Money Out\n05/09/2026,Interest,1.20,\n"
                 "06 Sep 2026,ALDI 45,,23.50\n", encoding="utf-8-sig")
    assert _run(mgr, "import_bank_statement_csv", path=str(f)).data["import"]["added"] == 2
    rows = {r["description"]: r for r in _h(mgr).rows("SELECT * FROM txns")}
    assert (rows["ALDI 45"]["date"], rows["ALDI 45"]["amount"], rows["ALDI 45"]["category"]) == (
        "2026-09-06", -23.5, "Groceries")
    assert rows["Interest"]["amount"] == 1.2 and rows["Interest"]["category"] == "Income"
    bad = tmp_path / "bad.csv"
    bad.write_text("When,What\n2026-09-01,x\n", encoding="utf-8")
    assert "needs a date" in _run(mgr, "import_bank_statement_csv", path=str(bad)).error


def test_categories_by_words_and_by_what_is_taught(imported, mgr):
    cats = {r["description"]: r["category"] for r in _h(mgr).rows("SELECT description, category FROM txns")}
    assert (cats["TESCO STORES 2231"], cats["NETFLIX.COM 866-579"], cats["DD HOVE LETTINGS RENT"],
            cats["ACME LTD SALARY"], cats["CAFE NERO"]) == ("Groceries", "Subscriptions", "Housing", "Income", "Eating out")
    assert [r["description"] for r in _run(mgr, "uncategorised_payments").data["payments"]] == ["JOHN SMITH TRANSFER"]
    assert _run(mgr, "teach_category", words="john smith", category="Family").data["rule"]["recategorised"] == 1
    assert _h(mgr).categorise("JOHN SMITH TRANSFER 2", -20) == "Family", "the rule sticks for new payments"


def test_the_budget_and_the_month(imported, mgr):
    _run(mgr, "set_budget", category="Groceries", monthly=80)
    m = _run(mgr, "month_budget", month="2026-09").data["month"]
    assert (m["income"], m["spent"]) == (2400.0, 950 + 12.99 + 62.40 + 38.10 + 8.40 + 50)
    assert [(x["category"], x["spent"], x["left"]) for x in m["over"]] == [("Groceries", 100.5, -20.5)]
    titles = [f["title"] for f in _run(mgr, "household_overview", as_of=AS_OF).data["findings"]]
    assert "Groceries: GBP 100.50 spent against a GBP 80.00 budget" in titles


def test_subscriptions_and_bills_are_found_with_price_rises(imported, mgr):
    regular = {r["payee"]: r for r in _run(mgr, "regular_payments", as_of=AS_OF).data["regular"]}
    assert sorted(regular) == ["hove lettings", "netflix com"], "Spotify stopped in May, so it is not live"
    n = regular["netflix com"]
    assert (n["amount"], n["yearly"], n["price_rise"], n["next"]) == (12.99, 155.88, 2.0, "2026-10-06")
    o = _run(mgr, "household_overview", as_of=AS_OF)
    assert [s["payee"] for s in o.data["overview"]["due_soon"]] == ["hove lettings"], "rent in 4 days"
    assert "NETFLIX.COM 866-579 went up by GBP 2.00 to GBP 12.99 a month" in [f["title"] for f in o.data["findings"]]


def test_warranties_and_the_report(imported, mgr):
    _run(mgr, "add_warranty", item="Fridge freezer", bought="2024-10-15", months=24, shop="Currys")
    _run(mgr, "add_warranty", item="Kettle", bought="2026-01-10", months=12)
    titles = [f["title"] for f in _run(mgr, "household_overview", as_of=AS_OF).data["findings"]]
    assert "The warranty on Fridge freezer runs out on 2026-10-15" in titles
    assert not any("Kettle" in t for t in titles)
    r = _run(mgr, "where_the_money_went", month="2026-09").data["report"]
    with open(r["path"], encoding="utf-8") as fh:
        text = fh.read()
    assert "In GBP 2,400.00 · out GBP 1,121.89 · saved GBP 1,278.11" in text
    assert "| Housing | 950.00 |" in text and "NETFLIX.COM 866-579: 12.99 a month (up 2.00)" in text


def test_the_monthly_report_runs_as_a_workflow(imported, mgr):
    task = mgr.wait(mgr.submit("show me where the money went").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert os.listdir(os.path.join(_h(mgr).root, "Reports")), "the report was written"
