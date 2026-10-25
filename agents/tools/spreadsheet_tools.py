"""Spreadsheet tools: read, write (preserving formulas and formatting), clean,
reconcile, check formulas, compare periods.

Core tools, not an industry pack's: an accountant, a sales team and an HR
team all live in spreadsheets. Every path goes through Tool.guard (the
workspace fence).

Writing edits a workbook in place cell by cell with openpyxl, so the
formulas, formats, column widths and other sheets the agent did not touch
survive. A cell that holds a formula is not overwritten unless the call
says so - replacing a live formula with a pasted number is the classic
spreadsheet error. openpyxl does not calculate: values read from formula
cells are the ones Excel last saved (None if never opened in Excel).

openpyxl is optional; without it .xlsx tools fail with a clear message and
CSV works everywhere a table is accepted.
"""
from __future__ import annotations

import csv
import io
import os
import re
from datetime import date, datetime
from typing import Any, Optional

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec

CATEGORY = "spreadsheet"
MAX_ROWS = 200
_PATH = {"type": "string", "description": "Workbook path (.xlsx/.xlsm/.csv), relative to the workspace."}
_ERRORS = ("#REF!", "#DIV/0!", "#N/A", "#VALUE!", "#NAME?", "#NUM!", "#NULL!", "#SPILL!", "#CALC!")


def _openpyxl():
    try:
        import openpyxl
        return openpyxl
    except ImportError:
        raise ValueError("this needs the openpyxl package for .xlsx files (pip install openpyxl); "
                         "CSV files work without it") from None


def _is_xlsx(path: str) -> bool:
    return path.lower().endswith((".xlsx", ".xlsm"))


def _cell(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()[:10] if isinstance(v, date) and not isinstance(v, datetime) or \
            (isinstance(v, datetime) and not v.time()) else v.isoformat()
    return v


def load_rows(path: str, sheet: str = "") -> tuple[list[str], list[list[Any]]]:
    """(headers, rows) of the first non-empty table in a CSV or sheet."""
    if _is_xlsx(path):
        wb = _openpyxl().load_workbook(path, data_only=True, read_only=True)
        try:
            ws = wb[sheet] if sheet else wb.worksheets[0]
            raw = [[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)]
        finally:
            wb.close()
    else:
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            text = fh.read()
        try:
            dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        raw = [r for r in csv.reader(io.StringIO(text), dialect)]
    raw = [r for r in raw if any(c not in (None, "") for c in r)]
    if not raw:
        return [], []
    hdr = 0
    for i, r in enumerate(raw[:15]):
        if sum(1 for c in r if isinstance(c, str) and c.strip() and not _num(c)) >= max(2, len([c for c in r if c]) // 2):
            hdr = i
            break
    headers = [str(c).strip() if c not in (None, "") else f"col{j + 1}" for j, c in enumerate(raw[hdr])]
    rows = [list(r) + [None] * (len(headers) - len(r)) for r in raw[hdr + 1:]]
    return headers, rows


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    text = str(v or "").strip()
    if not text:
        return None
    neg = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^\d.,\-]", "", text)
    if cleaned.count(",") and cleaned.count("."):
        cleaned = cleaned.replace(",", "") if cleaned.rfind(".") > cleaned.rfind(",") else \
            cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        n = float(cleaned)
    except ValueError:
        return None
    return -abs(n) if neg else n


def _write_rows(path: str, headers: list[str], rows: list[list[Any]], sheet: str = "Sheet1") -> str:
    from .base import replacing
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    if not _is_xlsx(path):
        with replacing(path) as tmp:               # OR-16: never half a file
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(headers)
                w.writerows(rows)
        return path
    op = _openpyxl()
    if os.path.isfile(path):
        wb = op.load_workbook(path)
        if sheet in wb.sheetnames:
            del wb[sheet]
        ws = wb.create_sheet(sheet[:31])
    else:
        wb = op.Workbook()
        ws = wb.active
        ws.title = sheet[:31]
    ws.append(headers)
    for r in rows:
        ws.append(r)
    _save_checked(wb, path)
    return path


def _save_checked(wb, path: str) -> None:
    """OR-16: saved beside, renamed into place, then opened again - a
    workbook that does not open is a failure, not a result."""
    from .base import replacing
    with replacing(path) as tmp:
        wb.save(tmp)
        _openpyxl().load_workbook(tmp, read_only=True).close()


def _table(headers: list[str], rows: list[list[Any]], limit: int = 40) -> str:
    out = [" | ".join(headers)]
    out += [" | ".join("" if c is None else str(c) for c in r) for r in rows[:limit]]
    if len(rows) > limit:
        out.append(f"... {len(rows) - limit} more row(s)")
    return "\n".join(out)


class ReadWorkbook(Tool):
    spec = make_spec("read_workbook", "Read a spreadsheet: list its sheets, and show a sheet's rows (values, or "
                     "formulas with formulas=true).", risk=RiskLevel.SAFE, category=CATEGORY,
                     properties={"path": _PATH, "sheet": {"type": "string"},
                                 "formulas": {"type": "boolean"}, "max_rows": {"type": "integer"}},
                     required=["path"])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path, err = self.guard(ctx, args["path"])
        if err:
            return err
        if not os.path.isfile(path):
            return self.fail(f"no such file: {args['path']}")
        limit = int(args.get("max_rows") or MAX_ROWS)
        try:
            if not _is_xlsx(path):
                headers, rows = load_rows(path)
                return self.ok(f"{os.path.basename(path)}: {len(rows)} row(s)\n" + _table(headers, rows, limit),
                               path=path, headers=headers, rows=rows[:limit], sheets=["(csv)"])
            op = _openpyxl()
            wb = op.load_workbook(path, data_only=not args.get("formulas"))
            ws = wb[args["sheet"]] if args.get("sheet") else wb.worksheets[0]
            rows = [[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)]
            rows = [r for r in rows if any(c not in (None, "") for c in r)]
            text = (f"Sheets: {', '.join(wb.sheetnames)}\n{ws.title} ({ws.max_row} rows x {ws.max_column} cols, "
                    f"{'formulas' if args.get('formulas') else 'values'}):\n")
            text += "\n".join(" | ".join("" if c is None else str(c) for c in r) for r in rows[:limit])
            return self.ok(text, path=path, sheets=wb.sheetnames, rows=rows[:limit])
        except (ValueError, KeyError, OSError) as exc:
            return self.fail(str(exc))


_NUMBER = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")


def _number(value: Any) -> Any:
    """A cell that came as text but IS a number ("1200", "-3.5") is written as
    one - some models send every cell as text. "007" or "1,200" stay text."""
    if isinstance(value, str) and _NUMBER.fullmatch(value.strip()):
        text = value.strip()
        return float(text) if "." in text else int(text)
    return value


class WriteWorkbook(Tool):
    spec = make_spec("write_workbook", "Write cells into a workbook, keeping its formulas and formatting. Give "
                     "`cells` {\"B7\": 1200, \"C7\": \"=B7*0.15\"} and/or `rows` appended at `start_cell`. Formula "
                     "cells are protected unless overwrite_formulas is true. Creates the file/sheet if missing.",
                     risk=RiskLevel.LOW, category=CATEGORY,
                     properties={"path": _PATH, "sheet": {"type": "string"},
                                 "cells": {"type": "object", "description": "A1-style address -> value or =formula."},
                                 "rows": {"type": "array", "items": {"type": "array"}},
                                 "start_cell": {"type": "string", "description": "Where rows begin (default: after "
                                                                                 "the last used row, column A)."},
                                 "overwrite_formulas": {"type": "boolean"}},
                     required=["path"])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path, err = self.guard(ctx, args["path"])
        if err:
            return err
        cells = dict(args.get("cells") or {})
        rows = [[_number(v) for v in row] if isinstance(row, (list, tuple)) else row
                for row in (args.get("rows") or [])]
        if not cells and not rows:
            return self.fail("give cells and/or rows")
        if not _is_xlsx(path):
            if cells:
                return self.fail("cell addresses need an .xlsx workbook; for CSV give rows")
            with open(path, "a", encoding="utf-8", newline="") as fh:
                csv.writer(fh).writerows(rows)
            return self.ok(f"appended {len(rows)} row(s) to {os.path.basename(path)}", path=path)
        try:
            op = _openpyxl()
            from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
            keep_vba = path.lower().endswith(".xlsm")
            wb = op.load_workbook(path, keep_vba=keep_vba) if os.path.isfile(path) else op.Workbook()
            name = args.get("sheet") or wb.worksheets[0].title
            ws = wb[name] if name in wb.sheetnames else wb.create_sheet(name[:31])
            protected, written = [], 0
            for addr, value in cells.items():
                cur = ws[addr].value
                if isinstance(cur, str) and cur.startswith("=") and not args.get("overwrite_formulas") \
                        and not (isinstance(value, str) and value.startswith("=")):
                    protected.append(addr)
                    continue
                ws[addr] = value
                written += 1
            if rows:
                if args.get("start_cell"):
                    col, row = coordinate_from_string(args["start_cell"])
                    c0, r0 = column_index_from_string(col), row
                else:
                    c0, r0 = 1, ws.max_row + 1 if ws.max_row > 1 or ws["A1"].value is not None else 1
                for i, r in enumerate(rows):
                    for j, v in enumerate(r):
                        ws.cell(row=r0 + i, column=c0 + j, value=v)
                written += sum(len(r) for r in rows)
            _save_checked(wb, path)
        except (ValueError, KeyError, OSError) as exc:
            return self.fail(f"could not write {os.path.basename(path)}: {exc}")
        msg = f"wrote {written} cell(s) to {os.path.basename(path)}!{name}"
        if protected:
            msg += f"; left {len(protected)} formula cell(s) untouched: {', '.join(protected[:10])}"
        return self.ok(msg, path=path, protected=protected, written=written)


class CleanData(Tool):
    spec = make_spec("clean_data", "Clean a table: trim text, drop empty and exact-duplicate rows, turn "
                     "number-like text into numbers and dates into YYYY-MM-DD. Writes a cleaned copy (never "
                     "overwrites the original).", risk=RiskLevel.LOW, category=CATEGORY,
                     properties={"path": _PATH, "sheet": {"type": "string"},
                                 "output": {"type": "string", "description": "Output path (default <name> cleaned.xlsx/csv)."}},
                     required=["path"])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path, err = self.guard(ctx, args["path"])
        if err:
            return err
        try:
            headers, rows = load_rows(path, args.get("sheet") or "")
        except (ValueError, KeyError, OSError) as exc:
            return self.fail(str(exc))
        seen, out, dups, empties, fixed = set(), [], 0, 0, 0
        for r in rows:
            clean = []
            for v in r:
                if isinstance(v, str):
                    t = re.sub(r"\s+", " ", v).strip()
                    n = _num(t) if re.fullmatch(r"[\s(]*[-+]?(?:[$£€R]|[A-Z]{3}\s)?\s?[\d.,]*\d\)?", t) else None
                    d = _date(t)
                    if n is not None and t:
                        clean.append(n); fixed += 1
                    elif d:
                        clean.append(d); fixed += 1
                    else:
                        clean.append(t)
                else:
                    clean.append(_cell(v))
            if all(c in (None, "") for c in clean):
                empties += 1
                continue
            key = tuple(clean)
            if key in seen:
                dups += 1
                continue
            seen.add(key)
            out.append(clean)
        stem, ext = os.path.splitext(path)
        target = args.get("output") or f"{stem} cleaned{ext if ext.lower() != '.xlsm' else '.xlsx'}"
        target, err = self.guard(ctx, target)
        if err:
            return err
        try:
            _write_rows(target, headers, out, "Cleaned")
        except (ValueError, OSError) as exc:
            return self.fail(str(exc))
        return self.ok(f"{len(rows)} row(s) in, {len(out)} out: {dups} duplicate(s) and {empties} empty row(s) "
                       f"removed, {fixed} value(s) normalised. Saved {os.path.basename(target)}",
                       path=target, rows_in=len(rows), rows_out=len(out), duplicates=dups)


def _date(t: str) -> str:
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(t, f).date().isoformat()
        except ValueError:
            continue
    return ""


def _col(headers: list[str], name: str) -> int:
    low = [h.lower().strip() for h in headers]
    n = name.lower().strip()
    if n in low:
        return low.index(n)
    hits = [i for i, h in enumerate(low) if n in h]
    if hits:
        return hits[0]
    raise ValueError(f"no column {name!r}; columns are {', '.join(headers)}")


class ReconcileSheets(Tool):
    spec = make_spec("reconcile_sheets", "Reconcile two tables (CSV/XLSX) on key column(s) and an amount column: "
                     "matched, differing, only-in-left, only-in-right. Writes a reconciliation workbook.",
                     risk=RiskLevel.LOW, category=CATEGORY,
                     properties={"left": _PATH, "right": _PATH,
                                 "key": {"type": "array", "items": {"type": "string"}, "description": "Key column(s)."},
                                 "amount": {"type": "string", "description": "Amount column name (both sides)."},
                                 "right_amount": {"type": "string"}, "right_key": {"type": "array", "items": {"type": "string"}},
                                 "tolerance": {"type": "number"}, "output": {"type": "string"}},
                     required=["left", "right", "key", "amount"])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        lp, err = self.guard(ctx, args["left"])
        if err:
            return err
        rp, err = self.guard(ctx, args["right"])
        if err:
            return err
        try:
            lh, lr = load_rows(lp)
            rh, rr = load_rows(rp)
            lk = [_col(lh, k) for k in args["key"]]
            rk = [_col(rh, k) for k in (args.get("right_key") or args["key"])]
            la, ra = _col(lh, args["amount"]), _col(rh, args.get("right_amount") or args["amount"])
        except (ValueError, KeyError, OSError) as exc:
            return self.fail(str(exc))
        tol = float(args.get("tolerance") or 0.005)

        def index(rows, keys, amt):
            out: dict[tuple, float] = {}
            for r in rows:
                k = tuple(str(r[i] or "").strip().lower() for i in keys)
                out[k] = out.get(k, 0.0) + (_num(r[amt]) or 0.0)
            return out
        left, right = index(lr, lk, la), index(rr, rk, ra)
        matched, diff, only_l, only_r = [], [], [], []
        for k, v in left.items():
            if k in right:
                (matched if abs(v - right[k]) <= tol else diff).append([" / ".join(k), v, right[k], round(v - right[k], 2)])
            else:
                only_l.append([" / ".join(k), v, None, v])
        for k, v in right.items():
            if k not in left:
                only_r.append([" / ".join(k), None, v, -v])
        target = args.get("output") or os.path.join(os.path.dirname(lp), "Reconciliation.xlsx")
        target, err = self.guard(ctx, target)
        if err:
            return err
        hdr = ["Key", os.path.basename(lp), os.path.basename(rp), "Difference"]
        try:
            for name, rows in (("Differences", diff), ("Only in left", only_l), ("Only in right", only_r),
                               ("Matched", matched)):
                _write_rows(target if _is_xlsx(target) else target.replace(".csv", f" {name}.csv"), hdr, rows, name)
        except (ValueError, OSError) as exc:
            return self.fail(str(exc))
        net = round(sum(r[3] for r in diff + only_l + only_r), 2)
        text = (f"{len(matched)} matched, {len(diff)} differ, {len(only_l)} only in {os.path.basename(lp)}, "
                f"{len(only_r)} only in {os.path.basename(rp)}; net difference {net:,.2f}. Saved {os.path.basename(target)}")
        if diff or only_l or only_r:
            text += "\n" + _table(hdr, (diff + only_l + only_r)[:25])
        return self.ok(text, path=target, matched=len(matched), differences=len(diff),
                       only_left=len(only_l), only_right=len(only_r), net=net)


class CheckFormulas(Tool):
    spec = make_spec("check_formulas", "Audit a workbook's formulas: error values (#REF!, #DIV/0!...), links to "
                     "other workbooks, hard-coded numbers inside formulas, a formula that breaks its column's "
                     "pattern, and numbers stored as text.", risk=RiskLevel.SAFE, category=CATEGORY,
                     properties={"path": _PATH}, required=["path"])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path, err = self.guard(ctx, args["path"])
        if err:
            return err
        if not _is_xlsx(path):
            return self.fail("check_formulas needs an .xlsx/.xlsm workbook")
        try:
            op = _openpyxl()
            wf = op.load_workbook(path)
            wv = op.load_workbook(path, data_only=True)
        except (ValueError, OSError, KeyError) as exc:
            return self.fail(str(exc))
        issues: list[list[Any]] = []
        n_formulas = 0
        for ws in wf.worksheets:
            vs = wv[ws.title]
            by_col: dict[int, list[tuple[int, str]]] = {}
            for row in ws.iter_rows():
                for c in row:
                    v = c.value
                    cached = vs[c.coordinate].value
                    if isinstance(cached, str) and cached.strip() in _ERRORS:
                        issues.append([ws.title, c.coordinate, "error value", cached])
                    if isinstance(v, str) and v.startswith("="):
                        n_formulas += 1
                        if re.search(r"\[[^\]]+\.xls", v, re.I):
                            issues.append([ws.title, c.coordinate, "link to another workbook", v[:80]])
                        consts = [x for x in re.findall(r"(?<![A-Z$\d.])(\d+\.?\d*)(?![\d:])", v.split("!")[-1])
                                  if x not in ("0", "1", "100", "12")]
                        if consts and not re.match(r"=\s*(ROUND|TEXT|DATE|IF|INDEX|OFFSET|MID|LEFT|RIGHT)", v, re.I):
                            issues.append([ws.title, c.coordinate, "hard-coded number in formula", v[:80]])
                        by_col.setdefault(c.column, []).append((c.row, re.sub(r"(?<=[A-Z])\$?\d+", "#", v)))
                    elif isinstance(v, str) and re.fullmatch(r"\s*-?[\d,]+\.?\d*\s*", v) and len(v.strip()) > 1:
                        issues.append([ws.title, c.coordinate, "number stored as text", v])
            for col, fs in by_col.items():
                if len(fs) < 4:
                    continue
                common = max(set(p for _, p in fs), key=lambda p: sum(1 for _, q in fs if q == p))
                share = sum(1 for _, q in fs if q == common) / len(fs)
                if share >= 0.75:
                    for r, p in fs:
                        if p != common:
                            issues.append([ws.title, f"{ws.cell(row=r, column=col).column_letter}{r}",
                                           "inconsistent with its column", ws.cell(row=r, column=col).value[:80]])
        text = f"{n_formulas} formula(s) checked in {len(wf.worksheets)} sheet(s); {len(issues)} issue(s)"
        if issues:
            text += "\n" + _table(["Sheet", "Cell", "Issue", "Detail"], issues, 60)
        return self.ok(text, path=path, issues=issues, formulas=n_formulas)


class ComparePeriods(Tool):
    spec = make_spec("compare_periods", "Compare two periods' figures by key (e.g. account): value A, value B, "
                     "change and % change. From two files, or two columns of one file. Writes a comparison.",
                     risk=RiskLevel.LOW, category=CATEGORY,
                     properties={"path": _PATH, "other_path": _PATH, "key": {"type": "string"},
                                 "column": {"type": "string", "description": "Value column (period A)."},
                                 "other_column": {"type": "string", "description": "Value column for period B."},
                                 "threshold_pct": {"type": "number"}, "output": {"type": "string"}},
                     required=["path", "key", "column"])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pa, err = self.guard(ctx, args["path"])
        if err:
            return err
        pb = pa
        if args.get("other_path"):
            pb, err = self.guard(ctx, args["other_path"])
            if err:
                return err
        try:
            ha, ra = load_rows(pa)
            hb, rb = (ha, ra) if pb == pa else load_rows(pb)
            ka, kb = _col(ha, args["key"]), _col(hb, args["key"])
            va = _col(ha, args["column"])
            vb = _col(hb, args.get("other_column") or args["column"])
        except (ValueError, KeyError, OSError) as exc:
            return self.fail(str(exc))
        a = {str(r[ka]).strip(): _num(r[va]) or 0.0 for r in ra if r[ka] not in (None, "")}
        b = {str(r[kb]).strip(): _num(r[vb]) or 0.0 for r in rb if r[kb] not in (None, "")}
        thr = float(args.get("threshold_pct") or 20)
        rows, flagged = [], []
        for k in sorted(set(a) | set(b)):
            x, y = a.get(k, 0.0), b.get(k, 0.0)
            pct = round((x - y) / abs(y) * 100, 1) if y else None
            rows.append([k, x, y, round(x - y, 2), pct])
            if pct is None and x or pct is not None and abs(pct) >= thr:
                flagged.append(k)
        target = args.get("output") or os.path.join(os.path.dirname(pa), "Period comparison.xlsx")
        target, err = self.guard(ctx, target)
        if err:
            return err
        try:
            _write_rows(target, [args["key"], "A", "B", "Change", "%"], rows, "Comparison")
        except (ValueError, OSError) as exc:
            return self.fail(str(exc))
        return self.ok(f"{len(rows)} key(s) compared; {len(flagged)} moved {thr:g}% or more: "
                       f"{', '.join(flagged[:15]) or 'none'}. Saved {os.path.basename(target)}\n"
                       + _table([args["key"], "A", "B", "Change", "%"], rows),
                       path=target, flagged=flagged)


def tools() -> list[Tool]:
    return [ReadWorkbook(), WriteWorkbook(), CleanData(), ReconcileSheets(), CheckFormulas(), ComparePeriods()]
