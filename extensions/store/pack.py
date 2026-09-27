"""The Online store pack: tools, the shop assistant, its checklists.

Every tool is READ or DRAFT. Refunds, supplier orders and price changes
happen in the store and with the supplier, by the owner; this pack drafts
the purchase order and says what to do. Selling-channel APIs wait for
connected accounts (catalogue gate 2); until then Shopify and WooCommerce
CSV exports import here.

READ   store_overview, stock_report, margin_report, order_problems, returns_report
DRAFT  import_products, import_orders, update_product, draft_purchase_orders, log_return, write_listing
"""
from __future__ import annotations

import os
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Workflow
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .shop import Shop

EXT = "store"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="your shop's records")
DATE = {"type": "string", "description": "YYYY-MM-DD."}


def _path(s: Shop, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(s.root, p)


@TOOLS.tool("store_overview", "The shop at a glance: this month's orders and sales, what to reorder, what is out, "
            "orders stuck, low margins, returns.", Level.READ, {"as_of": DATE})
def store_overview(s: Shop, a: dict[str, Any]) -> Out:
    o = s.overview(a.get("as_of", ""))
    pr = o["problems"]
    text = (f"{o['as_of']}: {o['orders_this_month']} order(s) this month, {s.money(o['sales_this_month'])}. "
            f"{len(o['reorder'])} product(s) to reorder, {len(o['out_of_stock'])} out of stock, "
            f"{len(pr['unshipped'])} paid order(s) not shipped, {len(o['margins']['low'])} below the margin floor.")
    findings = [finding(f"{p['sku']} ({p['title']}) is out of stock and sells {p['per_day']:g} a day",
                        action="reorder now", entity=p["sku"]) for p in o["out_of_stock"]]
    findings += [finding(f"Order {x['number']} was paid {x['days_waiting']} days ago and not shipped",
                         action="ship it or tell the customer", entity=x["number"]) for x in pr["unshipped"]]
    return Out(text, {"overview": o}, findings)


@TOOLS.tool("stock_report", "How fast each product sells, how many days its stock lasts, and what to reorder "
            "(and how many) given the supplier's lead time.", Level.READ, {"as_of": DATE})
def stock_report(s: Shop, a: dict[str, Any]) -> Out:
    r = s.stock_report(a.get("as_of", ""))
    lines = [f"{p['sku']} {p['title']}: {p['stock']} left, {p['per_day']:g}/day, "
             f"{p['days_of_cover']:g} days; order {p['order_qty']}" for p in r["reorder"]]
    return Out(f"{len(r['reorder'])} to reorder" + (":\n" + "\n".join(lines) if lines else "."), {"stock": r})


@TOOLS.tool("margin_report", "Each product's margin, the ones below the floor, and those with no cost set.",
            Level.READ)
def margin_report(s: Shop, a: dict[str, Any]) -> Out:
    m = s.margins()
    findings = [finding(f"{p['sku']} ({p['title']}) earns {p['margin']:g}%, under your {m['floor']:g}% floor",
                        action="raise the price or find a cheaper supplier", entity=p["sku"]) for p in m["low"]]
    if m["no_cost"]:
        findings.append(finding(f"{len(m['no_cost'])} product(s) have no cost, so their margin is unknown",
                                ", ".join(p["sku"] for p in m["no_cost"][:10]), action="add the cost per item"))
    return Out(f"{len(m['products'])} product(s) with a margin; {len(m['low'])} under {m['floor']:g}%.",
               {"margins": m}, findings)


@TOOLS.tool("order_problems", "Paid orders not shipped in time, and orders never paid.", Level.READ, {"as_of": DATE})
def order_problems(s: Shop, a: dict[str, Any]) -> Out:
    p = s.order_problems(a.get("as_of", ""))
    findings = [finding(f"Order {x['number']} was paid {x['days_waiting']} days ago and not shipped",
                        action="ship it or tell the customer", entity=x["number"]) for x in p["unshipped"]]
    return Out(f"{len(p['unshipped'])} paid and not shipped; {len(p['unpaid'])} unpaid for 3+ days.", {"problems": p},
               findings)


@TOOLS.tool("returns_report", "Returns by product: the rate and the reasons; flags products that come back too "
            "often.", Level.READ)
def returns_report(s: Shop, a: dict[str, Any]) -> Out:
    r = s.returns_report()
    findings = [finding(f"{p['sku']} comes back {p['rate']:g}% of the time ({p['returned']} of {p['sold']})",
                        p["reasons"], action="check the listing, sizing or quality", entity=p["sku"]) for p in r["high"]]
    return Out(f"{sum(p['returned'] for p in r['products'])} item(s) returned across {len(r['products'])} product(s).",
               {"returns": r}, findings)


@TOOLS.tool("import_products", "Import products from a Shopify or WooCommerce product CSV export.", Level.DRAFT,
            {"path": {"type": "string"}}, ["path"])
def import_products(s: Shop, a: dict[str, Any]) -> Out:
    r = s.import_products(_path(s, a["path"]))
    return Out(f"{r['added']} product(s) added, {r['updated']} updated, {r['skipped']} row(s) without a SKU skipped.",
               {"import": r})


@TOOLS.tool("import_orders", "Import orders from a Shopify order CSV export (one row per line item).", Level.DRAFT,
            {"path": {"type": "string"}}, ["path"])
def import_orders(s: Shop, a: dict[str, Any]) -> Out:
    r = s.import_orders(_path(s, a["path"]))
    return Out(f"{r['orders']} order(s) read, {r['new']} new.", {"import": r})


@TOOLS.tool("update_product", "Add a product or change its price, cost, stock, supplier or lead time.", Level.DRAFT,
            {"sku": {"type": "string"}, "title": {"type": "string"}, "price": {"type": "number"},
             "cost": {"type": "number"}, "stock": {"type": "integer"}, "supplier": {"type": "string"},
             "lead_days": {"type": "integer", "description": "Days from ordering to having it in stock."}}, ["sku"])
def update_product(s: Shop, a: dict[str, Any]) -> Out:
    p = s.upsert_product(a["sku"], title=a.get("title", ""), price=a.get("price"), cost=a.get("cost"),
                         stock=a.get("stock"), lead_days=a.get("lead_days"), supplier=a.get("supplier"))
    return Out(f"{p['sku']} {p['title']}: {s.money(p['price'])}, {p['stock']} in stock.", {"product": p})


@TOOLS.tool("draft_purchase_orders", "Write a purchase order per supplier for everything that needs reordering, "
            "as CSV files to send. Nothing is ordered.", Level.DRAFT, {"as_of": DATE})
def draft_purchase_orders(s: Shop, a: dict[str, Any]) -> Out:
    pos = s.purchase_orders(a.get("as_of", ""))
    if not pos:
        return Out("Nothing needs reordering.", {"purchase_orders": []})
    findings = [finding(f"{po['supplier']}: {len(po['missing_cost'])} line(s) have no cost", action="add costs")
                for po in pos if po["missing_cost"]]
    findings += [finding(f"{po['lines']} product(s) to reorder have no supplier set", action="set a supplier")
                 for po in pos if po["supplier"] == "No supplier set"]
    return Out("Purchase orders ready to send:\n" + "\n".join(
        f"{po['supplier']}: {po['lines']} line(s), {po['units']} unit(s), {s.money(po['cost'])} - {po['path']}"
        for po in pos), {"purchase_orders": pos}, findings)


@TOOLS.tool("log_return", "Record a returned item and why; restock puts it back into stock.", Level.DRAFT,
            {"order": {"type": "string"}, "sku": {"type": "string"}, "qty": {"type": "integer"},
             "reason": {"type": "string"}, "restock": {"type": "boolean"}}, ["order", "sku", "qty", "reason"])
def log_return(s: Shop, a: dict[str, Any]) -> Out:
    r = s.log_return(a["order"], a["sku"], a["qty"], a["reason"], restock=bool(a.get("restock")))
    return Out(f"Return of {r['qty']} x {r['sku']} from {r['order_number']} recorded"
               + (", back in stock." if r["restocked"] else "."), {"return": r})


@TOOLS.tool("write_listing", "Save a product listing (title, description, search title and description) and check "
            "it against Shopify's search-listing limits. Nothing is published.", Level.DRAFT,
            {"sku": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
             "seo_title": {"type": "string"}, "seo_description": {"type": "string"}}, ["sku", "title", "description"])
def write_listing(s: Shop, a: dict[str, Any]) -> Out:
    r = s.write_listing(a["sku"], title=a["title"], description=a["description"], seo_title=a.get("seo_title", ""),
                        seo_description=a.get("seo_description", ""))
    findings = [finding(f"{r['sku']}: {p}", action="shorten it") for p in r["problems"]]
    return Out(f"Listing for {r['sku']} saved: {r['path']}", {"listing": r}, findings)


TEMPLATE = AgentTemplate(
    id="shop_assistant", name="Shop Assistant", worker_type=EXT,
    mission="Keep an online shop stocked, shipping and earning: reorder before anything runs out, catch paid "
            "orders that never shipped, flag products that earn too little or come back too often, and write "
            "listings that fit.",
    handles=["stock and reorders", "purchase orders", "order problems", "margins", "returns", "listings"],
    tools=TOOLS.names() + ["read_file", "list_dir"], max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="weekdays", at="08:00")],
    memory_schema={"supplier": "lead times, minimum orders", "product": "what customers say about it"},
    default_workflow="morning_orders", artifacts=["purchase orders", "listings", "stock report"],
    setup_questions=["Currency", "Supplier lead time", "Days of stock to order", "Margin floor"], extension_id=EXT)

WORKFLOWS = [
    Workflow(id="morning_orders", name="Morning orders", description="Weekdays: orders stuck, out of stock, "
             "what to reorder.", template_id=TEMPLATE.id, trigger=Schedule(every="weekdays", at="08:00"),
             build_steps=lambda meta: [step("The shop", "store_overview"), step("Stock", "stock_report")],
             goal="Check the shop"),
    Workflow(id="weekly_restock", name="Weekly restock", description="Monday: purchase orders per supplier, "
             "margins, returns.", template_id=TEMPLATE.id, trigger=Schedule(every="weekly", at="09:00", weekday=0),
             build_steps=lambda meta: [step("Draft purchase orders", "draft_purchase_orders"),
                                       step("Margins", "margin_report"), step("Returns", "returns_report")],
             goal="Plan the restock"),
]

SKILLS = [Skill("store.rules", "How this pack works", always=True, text=(
    "You look after an online shop's back room. Numbers come from the imported products and orders - never "
    "guess stock or sales. Reorder quantities come from draft_purchase_orders; the owner sends them. You never "
    "refund, order, change a live price or publish: you say what to do and draft it. Listings: lead with what "
    "the product is and who it is for, then the details a buyer checks (size, material, care)."))]

INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:my\s+|the\s+)?(?:shop|store|orders)\b", workflow="morning_orders",
         reply="Checking the shop: stuck orders, stock and reorders.",
         verb=r"\b(?:please\s+)?(?:check|review|go\s+through|look\s+at)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:restock|reorder|purchase\s+orders?)\b", workflow="weekly_restock",
         reply="Drafting purchase orders for what is running low."),
    rule(EXT, TEMPLATE.id, r"\b(?:product\s+)?listing\b", reply="Writing the listing.",
         verb=r"\b(?:please\s+)?(?:write|draft|create|improve|rewrite)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:a\s+)?return\b", reply="Recording the return.",
         verb=r"\b(?:please\s+)?(?:log|record|add|process)\b"),
]


class StoreExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = Shop
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Shop"
    DEFAULTS = {"currency": "USD", "lead_days": 14, "safety_days": 7, "cover_days": 30, "margin_floor": 30,
                "ship_days": 2}
    ROLE = "the assistant who runs an online shop's back room"
    ACTIONS = [("Morning orders", "morning_orders"), ("Restock", "weekly_restock")]
    NOTES = "Every tool is READ or DRAFT: nothing is refunded, ordered, repriced or published from this pack."
    EXAMPLES = [('check my shop', 'morning_orders'), ('please draft the purchase orders', 'weekly_restock'), ('write a product listing for the blue mug', ''), ('log a return for order 1001', '')]

    def context_line(self, s: Shop) -> str:
        return (f"Shop: {s.currency} · supplier lead time {s.setting('lead_days', 14):g} days · order "
                f"{s.setting('cover_days', 30):g} days of stock · margin floor {s.setting('margin_floor', 30):g}%")

    def snapshot(self, s: Shop) -> dict[str, Any]:
        o = s.overview()
        return {"title": "Shop", "meta": f"{s.currency} · lead time {s.setting('lead_days', 14):g} days",
                "numbers": [("Sales this month", s.money(o["sales_this_month"])), ("To reorder", len(o["reorder"])),
                            ("Out of stock", len(o["out_of_stock"])), ("Not shipped", len(o["problems"]["unshipped"]))],
                "lists": [("Reorder", [(f"{p['sku']} · {p['stock']} left · order {p['order_qty']}", "") for p in o["reorder"]]),
                          ("Not shipped", [(f"{x['number']} · {x['days_waiting']} days", "") for x in o["problems"]["unshipped"]]),
                          ("Low margin", [(f"{p['sku']} · {p['margin']:g}%", "") for p in o["margins"]["low"]])]}
