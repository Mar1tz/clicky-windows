"""An online shop's back room: products, stock, orders, returns - one folder.

    <root>/store.json   currency, supplier lead time, safety and cover days, margin floor
    <root>/shop.sqlite  products, orders, order lines, returns
    <root>/Imports  Purchase orders  Listings  Reports

The shop owner's daily sums, done with no model: how fast each product
sells, how many days its stock will last, what to reorder and how much
(per supplier, as a purchase order to send), which products earn too
little, which orders were paid and never shipped, which products come
back too often. Products and orders come in from Shopify or WooCommerce
CSV exports until a store connection exists. Nothing is refunded, ordered
or published from here.
"""
from __future__ import annotations

import csv
import math
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from extensions.packkit import Store, slug

# Shopify's own counters for a product's search listing.
SEO_TITLE_MAX = 70
SEO_DESCRIPTION_MAX = 320


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _day(value: Any, default: Optional[date] = None) -> date:
    if not value:
        if default is None:
            raise ValueError("a date is needed (YYYY-MM-DD)")
        return default
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", str(value))
    if not m:
        raise ValueError(f"{value!r} is not a date (YYYY-MM-DD)")
    return date.fromisoformat(m.group(1))


def _num(value: Any, default: float = 0.0) -> float:
    text = re.sub(r"[^\d.\-]", "", str(value or ""))
    try:
        return float(text) if text not in ("", "-", ".") else default
    except ValueError:
        return default


class Shop(Store):
    SETTINGS_FILE = "store.json"
    DB = "shop.sqlite"
    FOLDERS = ("Imports", "Purchase orders", "Listings", "Reports")
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY COLLATE NOCASE, title TEXT NOT NULL, price REAL NOT NULL
        DEFAULT 0, cost REAL, stock INTEGER NOT NULL DEFAULT 0, lead_days INTEGER, supplier TEXT NOT NULL DEFAULT '',
        updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS orders (number TEXT PRIMARY KEY, created TEXT NOT NULL, email TEXT NOT NULL DEFAULT '',
        paid INTEGER NOT NULL DEFAULT 0, fulfilled INTEGER NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0,
        imported TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS order_lines (id INTEGER PRIMARY KEY, order_number TEXT NOT NULL, sku TEXT NOT NULL
        COLLATE NOCASE, title TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL, price REAL NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS returns (id INTEGER PRIMARY KEY, order_number TEXT NOT NULL, sku TEXT NOT NULL
        COLLATE NOCASE, qty INTEGER NOT NULL, reason TEXT NOT NULL, restocked INTEGER NOT NULL DEFAULT 0,
        at TEXT NOT NULL);
    """

    def setting(self, key: str, default: float) -> float:
        return float(self.cfg.get(key) or default)

    @property
    def currency(self) -> str:
        return self.cfg.get("currency") or "USD"

    def money(self, v: float) -> str:
        return f"{self.currency} {v:,.2f}"

    # -- products ----------------------------------------------------------------------------------------------

    def upsert_product(self, sku: str, *, title: str = "", price: Any = None, cost: Any = None, stock: Any = None,
                       lead_days: Any = None, supplier: Optional[str] = None) -> dict[str, Any]:
        sku = (sku or "").strip()
        if not sku:
            raise ValueError("a product needs its SKU")
        old = self.one("SELECT * FROM products WHERE sku = ?", (sku,))
        if old is None and not (title or "").strip():
            raise ValueError(f"new product {sku} needs a title")
        row = dict(old or {"sku": sku, "title": "", "price": 0.0, "cost": None, "stock": 0, "lead_days": None,
                           "supplier": ""})
        for key, val in (("title", title), ("price", price), ("cost", cost), ("stock", stock),
                         ("lead_days", lead_days), ("supplier", supplier)):
            if val not in (None, ""):
                row[key] = val.strip() if isinstance(val, str) and key in ("title", "supplier") else val
        if float(row["price"] or 0) < 0 or (row["cost"] is not None and float(row["cost"]) < 0):
            raise ValueError("prices and costs cannot be negative")
        self.write("INSERT INTO products(sku, title, price, cost, stock, lead_days, supplier, updated) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(sku) DO UPDATE SET title = excluded.title, "
                   "price = excluded.price, cost = excluded.cost, stock = excluded.stock, lead_days = excluded.lead_days, "
                   "supplier = excluded.supplier, updated = excluded.updated",
                   (row["sku"], row["title"], float(row["price"] or 0),
                    float(row["cost"]) if row["cost"] not in (None, "") else None, int(row["stock"] or 0),
                    int(row["lead_days"]) if row["lead_days"] not in (None, "") else None, row["supplier"] or "", _now()))
        return self.product(sku)

    def product(self, sku: str) -> dict[str, Any]:
        r = self.one("SELECT * FROM products WHERE sku = ?", ((sku or "").strip(),))
        if r is None:
            found = self.rows("SELECT * FROM products WHERE title LIKE ?", (f"%{(sku or '').strip()}%",))
            if len(found) > 1:
                raise ValueError(f"'{sku}' matches {', '.join(p['sku'] for p in found[:6])} - which SKU?")
            r = found[0] if found else None
        if r is None:
            raise KeyError(f"no product {sku!r}")
        return r

    def products(self) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM products ORDER BY title, sku")

    def import_products(self, path: str) -> dict[str, int]:
        """Shopify's product export (Handle, Title, Variant SKU, Variant Price, Variant Inventory Qty,
        Cost per item) or WooCommerce's (SKU, Name, Regular price, Stock)."""
        rows = _read_csv(path)
        added = updated = skipped = 0
        titles: dict[str, str] = {}
        for r in rows:
            handle = r.get("handle", "")
            if handle and r.get("title"):
                titles[handle] = r["title"]
            sku = r.get("variant sku") or r.get("sku") or ""
            if not sku.strip():
                skipped += 1
                continue
            title = r.get("title") or r.get("name") or titles.get(handle, "")
            option = r.get("option1 value", "")
            if option and option.lower() != "default title":
                title = f"{titles.get(handle, title)} - {option}"
            stock = r.get("variant inventory qty") or r.get("stock") or r.get("inventory") or r.get("quantity")
            price = r.get("variant price") or r.get("regular price") or r.get("price")
            cost = r.get("cost per item") or r.get("cost")
            exists = self.one("SELECT 1 FROM products WHERE sku = ?", (sku.strip(),))
            self.upsert_product(sku, title=title, price=_num(price) if price else None,
                                cost=_num(cost) if cost else None,
                                stock=int(_num(stock)) if stock not in (None, "") else None)
            added, updated = (added, updated + 1) if exists else (added + 1, updated)
        return {"added": added, "updated": updated, "skipped": skipped}

    # -- orders --------------------------------------------------------------------------------------------------

    def import_orders(self, path: str) -> dict[str, int]:
        """Shopify's order export: one row per line item, the order's fields on its first row."""
        rows = _read_csv(path)
        orders: dict[str, dict[str, Any]] = {}
        for r in rows:
            number = (r.get("name") or r.get("order") or r.get("order number") or r.get("order id") or "").strip()
            if not number:
                continue
            o = orders.setdefault(number, {"lines": []})
            if r.get("created at") or r.get("date"):
                o.setdefault("created", _day(r.get("created at") or r.get("date")).isoformat())
            for key, src in (("email", "email"), ("financial", "financial status"),
                             ("fulfillment", "fulfillment status"), ("total", "total")):
                if r.get(src) and key not in o:
                    o[key] = r[src]
            sku = r.get("lineitem sku") or r.get("sku") or ""
            qty = int(_num(r.get("lineitem quantity") or r.get("quantity"), 0))
            if sku and qty:
                o["lines"].append((sku.strip(), r.get("lineitem name") or r.get("product") or "", qty,
                                   _num(r.get("lineitem price") or r.get("price"))))
        new = 0
        with self.lock, self.db:
            for number, o in orders.items():
                if "created" not in o:
                    continue
                known = self.db.execute("SELECT 1 FROM orders WHERE number = ?", (number,)).fetchone()
                paid = (o.get("financial") or "").lower() in ("paid", "partially_refunded", "refunded", "partially paid")
                fulfilled = (o.get("fulfillment") or "").lower() in ("fulfilled", "shipped", "completed")
                self.db.execute("INSERT INTO orders(number, created, email, paid, fulfilled, total, imported) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(number) DO UPDATE SET paid = excluded.paid, "
                                "fulfilled = excluded.fulfilled",
                                (number, o["created"], o.get("email", ""), int(paid), int(fulfilled),
                                 _num(o.get("total")), _now()))
                if not known:
                    new += 1
                    self.db.executemany("INSERT INTO order_lines(order_number, sku, title, qty, price) "
                                        "VALUES (?, ?, ?, ?, ?)", [(number, *line) for line in o["lines"]])
        return {"orders": len(orders), "new": new}

    def velocity(self, as_of: str = "") -> dict[str, float]:
        """Units sold a day per SKU over the last `window_days`."""
        today = _day(as_of, date.today())
        window = int(self.setting("window_days", 30))
        since = (today - timedelta(days=window)).isoformat()
        rows = self.rows("SELECT l.sku, sum(l.qty) AS units FROM order_lines l JOIN orders o ON o.number = l.order_number "
                         "WHERE o.created > ? AND o.created <= ? GROUP BY l.sku COLLATE NOCASE",
                         (since, today.isoformat()))
        return {r["sku"].lower(): r["units"] / window for r in rows}

    # -- the sums ----------------------------------------------------------------------------------------------------

    def stock_report(self, as_of: str = "") -> dict[str, Any]:
        speed = self.velocity(as_of)
        safety, cover = self.setting("safety_days", 7), self.setting("cover_days", 30)
        default_lead = self.setting("lead_days", 14)
        rows, reorder, out = [], [], []
        for p in self.products():
            v = speed.get(p["sku"].lower(), 0.0)
            lead = p["lead_days"] if p["lead_days"] is not None else default_lead
            days = round(p["stock"] / v, 1) if v else None
            row = {**p, "per_day": round(v, 2), "days_of_cover": days, "lead_days": lead}
            if v and p["stock"] <= 0:
                out.append(row)
            if v and (days or 0) < lead + safety:
                row["order_qty"] = max(1, math.ceil(v * (lead + cover)) - max(0, p["stock"]))
                reorder.append(row)
            rows.append(row)
        reorder.sort(key=lambda r: r["days_of_cover"] if r["days_of_cover"] is not None else -1)
        return {"products": rows, "reorder": reorder, "out_of_stock": out}

    def purchase_orders(self, as_of: str = "") -> list[dict[str, Any]]:
        by: dict[str, list[dict[str, Any]]] = {}
        for r in self.stock_report(as_of)["reorder"]:
            by.setdefault(r["supplier"] or "No supplier set", []).append(r)
        folder = self.folder("Purchase orders")
        stamp = _day(as_of, date.today()).isoformat()
        out = []
        for supplier, lines in sorted(by.items()):
            path = os.path.join(folder, f"{stamp} {slug(supplier)}.csv")
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["sku", "title", "quantity", "unit_cost", "line_cost"])
                for r in lines:
                    unit = r["cost"] if r["cost"] is not None else ""
                    w.writerow([r["sku"], r["title"], r["order_qty"], unit,
                                round(unit * r["order_qty"], 2) if unit != "" else ""])
            total = sum((r["cost"] or 0) * r["order_qty"] for r in lines)
            out.append({"supplier": supplier, "path": path, "lines": len(lines), "units": sum(r["order_qty"] for r in lines),
                        "cost": round(total, 2), "missing_cost": [r["sku"] for r in lines if r["cost"] is None]})
        return out

    def margins(self) -> dict[str, Any]:
        floor = self.setting("margin_floor", 30)
        rows, low, unknown = [], [], []
        for p in self.products():
            if p["cost"] is None or not p["price"]:
                unknown.append(p)
                continue
            m = round((p["price"] - p["cost"]) / p["price"] * 100, 1)
            r = {**p, "margin": m}
            rows.append(r)
            if m < floor:
                low.append(r)
        return {"floor": floor, "products": sorted(rows, key=lambda r: r["margin"]), "low": sorted(low, key=lambda r: r["margin"]),
                "no_cost": unknown}

    def order_problems(self, as_of: str = "") -> dict[str, list[dict[str, Any]]]:
        today = _day(as_of, date.today())
        ship_by = (today - timedelta(days=int(self.setting("ship_days", 2)))).isoformat()
        unpaid_by = (today - timedelta(days=3)).isoformat()
        late = self.rows("SELECT * FROM orders WHERE paid = 1 AND fulfilled = 0 AND created < ? ORDER BY created", (ship_by,))
        for o in late:
            o["days_waiting"] = (today - date.fromisoformat(o["created"])).days
        unpaid = self.rows("SELECT * FROM orders WHERE paid = 0 AND created < ? ORDER BY created", (unpaid_by,))
        return {"unshipped": late, "unpaid": unpaid}

    # -- returns -----------------------------------------------------------------------------------------------------

    def log_return(self, order: str, sku: str, qty: int, reason: str, *, restock: bool = False) -> dict[str, Any]:
        if not (reason or "").strip():
            raise ValueError("say why it came back - that is what the returns report is for")
        qty = int(qty)
        line = self.one("SELECT sum(qty) AS n FROM order_lines WHERE order_number = ? AND sku = ?", (order.strip(), sku.strip()))
        if not line or not line["n"]:
            raise KeyError(f"order {order} has no {sku}")
        back = self.one("SELECT coalesce(sum(qty), 0) AS n FROM returns WHERE order_number = ? AND sku = ?",
                        (order.strip(), sku.strip()))["n"]
        if qty <= 0 or qty + back > line["n"]:
            raise ValueError(f"order {order} had {line['n']} of {sku}; {back} already returned")
        with self.lock, self.db:
            rid = self.db.execute("INSERT INTO returns(order_number, sku, qty, reason, restocked, at) VALUES (?, ?, ?, ?, ?, ?)",
                                  (order.strip(), sku.strip(), qty, reason.strip(), int(restock), _now())).lastrowid
            if restock:
                self.db.execute("UPDATE products SET stock = stock + ? WHERE sku = ?", (qty, sku.strip()))
        return self.one("SELECT * FROM returns WHERE id = ?", (rid,)) or {}

    def returns_report(self) -> dict[str, Any]:
        sold = {r["sku"].lower(): r["n"] for r in self.rows("SELECT sku, sum(qty) AS n FROM order_lines GROUP BY sku COLLATE NOCASE")}
        rows = []
        for r in self.rows("SELECT sku, sum(qty) AS n, group_concat(reason, '; ') AS reasons FROM returns "
                           "GROUP BY sku COLLATE NOCASE"):
            s = sold.get(r["sku"].lower(), 0)
            rows.append({"sku": r["sku"], "returned": r["n"], "sold": s,
                         "rate": round(r["n"] / s * 100, 1) if s else None, "reasons": r["reasons"]})
        high = [r for r in rows if r["rate"] is not None and r["sold"] >= 10 and r["rate"] >= self.setting("return_alarm", 10)]
        return {"products": sorted(rows, key=lambda r: -(r["rate"] or 0)), "high": high}

    # -- listings ----------------------------------------------------------------------------------------------------

    def write_listing(self, sku: str, *, title: str, description: str, seo_title: str = "",
                      seo_description: str = "") -> dict[str, Any]:
        p = self.product(sku)
        if not (title or "").strip() or not (description or "").strip():
            raise ValueError("a listing needs a title and a description")
        seo_title = (seo_title or title).strip()
        seo_description = (seo_description or re.sub(r"\s+", " ", description)).strip()
        problems = []
        if len(seo_title) > SEO_TITLE_MAX:
            problems.append(f"the search title is {len(seo_title)} characters; Shopify shows {SEO_TITLE_MAX}")
        if len(seo_description) > SEO_DESCRIPTION_MAX:
            problems.append(f"the search description is {len(seo_description)} characters; Shopify shows "
                            f"{SEO_DESCRIPTION_MAX}")
        path = os.path.join(self.folder("Listings"), f"{slug(p['sku'])}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# {title.strip()}\n\n{description.strip()}\n\n---\nSearch title: {seo_title}\n"
                     f"Search description: {seo_description}\nSKU: {p['sku']}  Price: {self.money(p['price'])}\n")
        return {"sku": p["sku"], "path": path, "problems": problems}

    def overview(self, as_of: str = "") -> dict[str, Any]:
        today = _day(as_of, date.today())
        month = today.isoformat()[:7]
        sales = self.one("SELECT count(*) AS n, coalesce(sum(total), 0) AS v FROM orders WHERE created LIKE ?", (f"{month}%",))
        stock = self.stock_report(today.isoformat())
        return {"as_of": today.isoformat(), "orders_this_month": sales["n"], "sales_this_month": round(sales["v"], 2),
                "products": len(stock["products"]), "reorder": stock["reorder"], "out_of_stock": stock["out_of_stock"],
                "problems": self.order_problems(today.isoformat()), "margins": self.margins(),
                "returns": self.returns_report()}


def _read_csv(path: str) -> list[dict[str, str]]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no file {path}")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        return [{(k or "").lower().strip(): (v or "").strip() for k, v in row.items()} for row in reader]
