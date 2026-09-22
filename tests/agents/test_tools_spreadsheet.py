"""Core spreadsheet tools: formulas survive writes, cleaning, reconciling, formula audit."""
from __future__ import annotations

import asyncio

import pytest

from agents.core import PermissionPolicy, RiskLevel, ToolCall, ToolContext
from agents.tools import default_registry

openpyxl = pytest.importorskip("openpyxl")


def run(ws, tool, **args):
    ctx = ToolContext(workspace=str(ws), policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return asyncio.run(default_registry().execute(ToolCall(tool, args), ctx))


def _book(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Rec"
    ws.append(["Item", "Amount", "VAT"])
    for i, amt in enumerate((100, 200, 300, 400), start=2):
        ws.append([f"Line {i}", amt, f"=B{i}*0.15"])
    ws["C4"] = "=B4*0.2"                                   # breaks the column's pattern
    ws["D2"] = "=1/0"
    ws["E2"] = "1,234"                                     # number stored as text
    ws["B2"].number_format = "#,##0.00"
    wb.save(path)


def test_write_keeps_formulas_and_formatting(tmp_path):
    p = tmp_path / "rec.xlsx"
    _book(p)
    r = run(tmp_path, "write_workbook", path="rec.xlsx", sheet="Rec", cells={"B2": 150, "C2": 999})
    assert r.ok and r.data["protected"] == ["C2"], "a formula cell is not overwritten with a number"
    wb = openpyxl.load_workbook(p)
    assert wb["Rec"]["B2"].value == 150 and wb["Rec"]["C2"].value == "=B2*0.15"
    assert wb["Rec"]["B2"].number_format == "#,##0.00", "formatting survives"
    r = run(tmp_path, "write_workbook", path="rec.xlsx", sheet="Rec", cells={"C2": 999}, overwrite_formulas=True)
    assert r.ok and openpyxl.load_workbook(p)["Rec"]["C2"].value == 999


def test_check_formulas_finds_the_classic_mistakes(tmp_path):
    _book(tmp_path / "rec.xlsx")
    r = run(tmp_path, "check_formulas", path="rec.xlsx")
    kinds = {i[2] for i in r.data["issues"]}
    assert "inconsistent with its column" in kinds
    assert "number stored as text" in kinds
    assert "hard-coded number in formula" in kinds


def test_clean_and_reconcile(tmp_path):
    (tmp_path / "ours.csv").write_text("Ref,Amount\nA1,100.00\nA2, 250.00 \nA2, 250.00 \nA3,75\n,\n", encoding="utf-8")
    (tmp_path / "theirs.csv").write_text("Ref,Amount\nA1,100\nA2,260\nA4,10\n", encoding="utf-8")
    c = run(tmp_path, "clean_data", path="ours.csv")
    assert c.ok and c.data["duplicates"] == 1 and c.data["rows_out"] == 3
    r = run(tmp_path, "reconcile_sheets", left="ours cleaned.csv", right="theirs.csv", key=["Ref"], amount="Amount",
            output="rec.xlsx")
    assert r.ok, r.error
    assert (r.data["matched"], r.data["differences"], r.data["only_left"], r.data["only_right"]) == (1, 1, 1, 1)


def test_compare_periods_flags_big_moves(tmp_path):
    (tmp_path / "pl.csv").write_text("Account,Aug,Jul\nSales,426000,296000\nRent,35000,35000\nAds,29800,20000\n",
                                     encoding="utf-8")
    r = run(tmp_path, "compare_periods", path="pl.csv", key="Account", column="Aug", other_column="Jul",
            threshold_pct=25, output="cmp.xlsx")
    assert r.ok and set(r.data["flagged"]) == {"Sales", "Ads"}


def test_paths_stay_inside_the_workspace(tmp_path):
    r = run(tmp_path, "read_workbook", path="../../outside.xlsx")
    assert not r.ok and "outside workspace" in r.error
