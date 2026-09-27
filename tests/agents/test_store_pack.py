"""The Online store pack on a fresh folder: no credentials, no network.

Shopify exports in, then the sums a shop owner needs: sales speed, days of
stock, how many to reorder and from whom, margins, paid orders never
shipped, returns that come back too often, listings that fit.
"""
from __future__ import annotations

import csv

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, TaskStatus, ToolCall, ToolContext

AS_OF = "2026-09-20"

PRODUCTS = ("Handle,Title,Option1 Value,Variant SKU,Variant Price,Variant Inventory Qty,Cost per item\n"
            "mug,Mug,Blue,MUG-BLU,12.00,10,4.00\n"
            "mug,,Red,MUG-RED,12.00,50,4.00\n"
            "tote,Tote,Default Title,TOTE,20.00,0,\n"
            "gift,Gift card,Default Title,,25.00,,\n")

ORDERS = ("Name,Email,Financial Status,Fulfillment Status,Created at,Lineitem quantity,Lineitem name,Lineitem price,"
          "Lineitem sku,Total\n"
          "#1001,a@x.test,paid,fulfilled,2026-09-05 10:00:00 +0100,40,Mug - Blue,12.00,MUG-BLU,480.00\n"
          "#1002,b@x.test,paid,,2026-09-15 09:00:00 +0100,20,Mug - Blue,12.00,MUG-BLU,340.00\n"
          "#1002,,,,,5,Tote,20.00,TOTE,\n"
          "#1003,c@x.test,pending,,2026-09-10 12:00:00 +0100,1,Tote,20.00,TOTE,20.00\n"
          "#1004,d@x.test,paid,fulfilled,2026-08-01 12:00:00 +0100,100,Mug - Blue,12.00,MUG-BLU,1200.00\n")


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("store")
    m.extensions.configure("store", {"currency": "GBP"})
    m.start()
    yield m
    m.stop()


@pytest.fixture
def shop(mgr, tmp_path):
    (tmp_path / "products.csv").write_text(PRODUCTS, encoding="utf-8-sig")
    (tmp_path / "orders.csv").write_text(ORDERS, encoding="utf-8-sig")
    assert _run(mgr, "import_products", path=str(tmp_path / "products.csv")).data["import"] == \
        {"added": 3, "updated": 0, "skipped": 1}
    assert _run(mgr, "import_orders", path=str(tmp_path / "orders.csv")).data["import"] == {"orders": 4, "new": 4}
    _run(mgr, "update_product", sku="MUG-BLU", supplier="Clayworks")
    return mgr.extensions.get("store").ensure_store(mgr.extensions._context("store"))


def _run(mgr, tool: str, **args):
    s = mgr.extensions.get("store").ensure_store(mgr.extensions._context("store"))
    ctx = ToolContext(workspace=s.root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def test_nothing_is_refunded_ordered_or_published(mgr):
    assert set(mgr.extensions.get("store").TOOLS.levels().values()) == {"read", "draft"}


def test_the_exports_come_in_with_variants_and_line_items(shop, mgr, tmp_path):
    assert [(p["sku"], p["title"], p["stock"]) for p in shop.products()] == [
        ("MUG-BLU", "Mug - Blue", 10), ("MUG-RED", "Mug - Red", 50), ("TOTE", "Tote", 0)]
    assert shop.product("TOTE")["cost"] is None
    lines = shop.rows("SELECT sku, qty FROM order_lines WHERE order_number = '#1002' ORDER BY id")
    assert [(r["sku"], r["qty"]) for r in lines] == [("MUG-BLU", 20), ("TOTE", 5)]
    assert _run(mgr, "import_orders", path=str(tmp_path / "orders.csv")).data["import"]["new"] == 0, \
        "the same export twice counts nothing twice"


def test_reorders_come_from_sales_speed_and_lead_time(shop, mgr):
    r = _run(mgr, "stock_report", as_of=AS_OF).data["stock"]
    speed = {p["sku"]: (p["per_day"], p["days_of_cover"]) for p in r["products"]}
    assert speed["MUG-BLU"] == (2.0, 5.0), "60 sold in 30 days; the August order is outside the window"
    assert speed["MUG-RED"] == (0.0, None)
    # lead 14 + cover 30 days at 2 a day = 88, less the 10 on hand
    assert [(p["sku"], p["order_qty"]) for p in r["reorder"]] == [("TOTE", 9), ("MUG-BLU", 78)]
    assert [p["sku"] for p in r["out_of_stock"]] == ["TOTE"]

    po = _run(mgr, "draft_purchase_orders", as_of=AS_OF)
    by = {x["supplier"]: x for x in po.data["purchase_orders"]}
    assert by["Clayworks"]["units"] == 78 and by["Clayworks"]["cost"] == 312.0
    with open(by["Clayworks"]["path"], encoding="utf-8") as fh:
        assert list(csv.reader(fh))[1] == ["MUG-BLU", "Mug - Blue", "78", "4.0", "312.0"]
    assert sorted(f["title"] for f in po.data["findings"]) == [
        "1 product(s) to reorder have no supplier set", "No supplier set: 1 line(s) have no cost"]


def test_margins_below_the_floor_and_unknown_costs(shop, mgr):
    _run(mgr, "update_product", sku="CARD-01", title="Greeting card", price=10, cost=8, stock=100)
    m = _run(mgr, "margin_report")
    assert [(p["sku"], p["margin"]) for p in m.data["margins"]["low"]] == [("CARD-01", 20.0)]
    assert [f["title"] for f in m.data["findings"]] == [
        "CARD-01 (Greeting card) earns 20%, under your 30% floor",
        "1 product(s) have no cost, so their margin is unknown"]
    assert not _run(mgr, "update_product", sku="NEW-1").ok, "a new product needs a title"


def test_paid_orders_not_shipped_are_caught(shop, mgr):
    p = _run(mgr, "order_problems", as_of=AS_OF)
    assert [(o["number"], o["days_waiting"]) for o in p.data["problems"]["unshipped"]] == [("#1002", 5)]
    assert [o["number"] for o in p.data["problems"]["unpaid"]] == ["#1003"]
    assert p.data["findings"][0]["title"] == "Order #1002 was paid 5 days ago and not shipped"


def test_returns_restock_and_the_ones_that_come_back_too_often(shop, mgr):
    assert _run(mgr, "log_return", order="#1001", sku="MUG-BLU", qty=5, reason="chipped").ok
    assert _run(mgr, "log_return", order="#1002", sku="MUG-BLU", qty=15, reason="wrong colour", restock=True).ok
    assert shop.product("MUG-BLU")["stock"] == 25
    assert "already returned" in _run(mgr, "log_return", order="#1001", sku="MUG-BLU", qty=36, reason="x").error
    assert not _run(mgr, "log_return", order="#1001", sku="TOTE", qty=1, reason="x").ok
    assert not _run(mgr, "log_return", order="#1001", sku="MUG-BLU", qty=1, reason="").ok
    r = _run(mgr, "returns_report")
    assert r.data["findings"][0]["title"] == "MUG-BLU comes back 12.5% of the time (20 of 160)"


def test_a_listing_is_checked_against_shopify(shop, mgr):
    ok = _run(mgr, "write_listing", sku="MUG-BLU", title="Blue stoneware mug", description="Hand-thrown, 350 ml.")
    assert ok.ok and ok.data["findings"] == []
    long = _run(mgr, "write_listing", sku="MUG-BLU", title="x" * 80, description="Hand-thrown.")
    assert [f["title"] for f in long.data["findings"]] == ["MUG-BLU: the search title is 80 characters; Shopify shows 70"]


def test_the_morning_check_runs_as_a_workflow(shop, mgr):
    task = mgr.wait(mgr.submit("check my shop").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    titles = [f["title"] for f in task.metadata.get("findings", [])]
    assert any(t.startswith("Order #1002 was paid") for t in titles)
    assert any(t.startswith("TOTE (Tote) is out of stock") for t in titles)


def test_requests_route_to_the_shop_assistant(mgr):
    for text, workflow in (("check my shop", "morning_orders"), ("please draft the purchase orders", "weekly_restock"),
                           ("write a product listing for the blue mug", ""), ("log a return for order 1001", "")):
        intent = mgr.extensions.match_intent(text)
        assert intent is not None and intent.metadata["template"] == "shop_assistant", text
        assert intent.metadata.get("workflow", "") == workflow, text
