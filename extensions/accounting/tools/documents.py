"""Document tools: find, read, classify, extract, rename, file - and the invoice pipeline.

Text comes from the file itself: PDFs through pypdf, Word through
python-docx, emails through the stdlib (attachments are saved beside
them), images through the OCR engine the pointer already ships
(rapidocr) when it is installed. Extraction is heuristic and every field
carries a confidence, because the next step - coding and drafting a
bill - is gated on how sure we are.

process_invoice_document is the invoice-processing workflow in one call:
extract -> classify -> duplicate check -> supplier check -> PO match ->
suggest coding -> draft bill (confidence-routed) -> attach and file the
original -> mark processed -> raise exceptions only where it is unsure.
"""
from __future__ import annotations

import email
import email.policy
import os
import re
import shutil
from datetime import date
from typing import Any, Optional

from ..books import LedgerError, parse_date, to_minor
from ..knowledge import hint_for
from .bank import account_code
from .common import PATH, ActionLevel, F, Out, accounting_tool, lines_table, money, policy, rel, resolve_in_client
from .invoices import create_draft_bill, duplicate_bill

R, D = ActionLevel.READ, ActionLevel.DRAFT

DOC_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".eml", ".txt", ".docx")

DOCUMENT_TYPES: dict[str, dict[str, Any]] = {
    "invoice": {"keywords": ["tax invoice", "invoice no", "invoice number", "invoice #", "invoice date",
                             "amount due", "total due", "bill to"], "folder": "Invoices"},
    "credit_note": {"keywords": ["credit note", "credit memo"], "folder": "Invoices"},
    "receipt": {"keywords": ["receipt", "paid: card", "cash sale", "thank you for your purchase", "till"],
                "folder": "Receipts"},
    "supplier_statement": {"keywords": ["statement of account", "account statement", "balance due", "aged balance"],
                           "folder": "Statements"},
    "bank_statement": {"keywords": ["bank statement", "opening balance", "closing balance", "available balance"],
                       "folder": "Bank"},
    "remittance": {"keywords": ["remittance advice", "payment advice"], "folder": "Other"},
    "purchase_order": {"keywords": ["purchase order", "po number"], "folder": "Other"},
    "payslip": {"keywords": ["payslip", "pay slip", "net pay", "gross pay"], "folder": "Other"},
    "tax_notice": {"keywords": ["assessment", "notice of", "revenue service", "tax authority"], "folder": "Other"},
}


# -- text -----------------------------------------------------------------------------------

def text_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages[:20])
    if ext == ".docx":
        import docx
        return "\n".join(p.text for p in docx.Document(path).paragraphs)
    if ext == ".eml":
        with open(path, "rb") as fh:
            msg = email.message_from_binary_file(fh, policy=email.policy.default)
        body = msg.get_body(preferencelist=("plain", "html"))
        head = f"From: {msg['from']}\nSubject: {msg['subject']}\nDate: {msg['date']}\n\n"
        return head + (body.get_content() if body else "")
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            raise ValueError("reading images needs OCR (rapidocr-onnxruntime); or ask the vision worker") from None
        result, _ = RapidOCR()(path)
        return "\n".join(r[1] for r in (result or []))
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def save_email_attachments(path: str, into: str) -> list[str]:
    with open(path, "rb") as fh:
        msg = email.message_from_binary_file(fh, policy=email.policy.default)
    saved = []
    for part in msg.iter_attachments():
        name = part.get_filename()
        if not name or not name.lower().endswith(DOC_EXTS):
            continue
        os.makedirs(into, exist_ok=True)
        target = os.path.join(into, os.path.basename(name))
        with open(target, "wb") as out:
            out.write(part.get_payload(decode=True) or b"")
        saved.append(target)
    return saved


def classify_text(text: str) -> tuple[str, float]:
    low = text.lower()
    best, score = "other", 0
    for kind, spec in DOCUMENT_TYPES.items():
        hits = sum(1 for k in spec["keywords"] if k in low)
        if hits > score:
            best, score = kind, hits
    if best == "invoice" and "statement of account" in low:
        best = "supplier_statement"
    conf = 0.0 if score == 0 else min(0.97, 0.6 + 0.12 * score)
    return best, conf


# -- extraction ------------------------------------------------------------------------------

_AMOUNT = r"[-(]?\s*(?:[A-Z]{1,3}\$?|[R$£€])?\s?\d[\d ,]*\.\d{2}\)?"
_GENERIC_TOP = re.compile(r"^(tax invoice|invoice|receipt|statement.*|credit note|original|copy|page \d+)$", re.I)


def _find_amount(text: str, labels: str) -> Optional[float]:
    found = None
    for m in re.finditer(rf"(?:{labels})[^\n\d]{{0,25}}({_AMOUNT})", text, re.I):
        try:
            found = to_minor(m.group(1).replace(" ", "")) / 100
        except LedgerError:
            continue
    return found


def _find_date(text: str, labels: str) -> str:
    m = re.search(rf"(?:{labels})\s*[:#]?\s*(\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}|\d{{4}}-\d{{2}}-\d{{2}}|"
                  rf"\d{{1,2}} [A-Za-z]{{3,9}} \d{{4}})", text, re.I)
    if not m:
        return ""
    try:
        return parse_date(m.group(1))
    except LedgerError:
        return ""


def extract_fields(text: str, suppliers: list[str], *, kind: str = "invoice") -> dict[str, Any]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    low = text.lower()
    fields: dict[str, Any] = {}
    conf: dict[str, float] = {}
    known = next((sup for sup in suppliers if sup.lower() in low), "")
    if not known:
        for sup in suppliers:
            first = sup.lower().split()[0]
            if len(first) >= 5 and re.search(rf"\b{re.escape(first)}\b", low):
                known = sup
                break
    if known:
        fields["supplier"], conf["supplier"] = known, 0.97
    else:
        m = re.search(r"^(?:from|supplier|vendor)\s*[:]\s*(.+)$", text, re.I | re.M)
        cand = m.group(1).strip() if m else next((l for l in lines[:6] if not _GENERIC_TOP.match(l)
                                                  and not re.search(r"\d{3,}", l)), "")
        if cand:
            fields["supplier"], conf["supplier"] = re.sub(r"\s+(cc|pty|ltd|inc)\.?$", "", cand, flags=re.I).strip(), 0.6
    m = re.search(r"(?:invoice[ \t]*(?:no|number|#)?|inv[ \t]*#|document[ \t]*no)[ \t]*[:#.]?[ \t]*"
                  r"([A-Z]{0,5}[-/]?\d[\w/-]*)", text, re.I)
    if m:
        fields["reference"], conf["reference"] = m.group(1).strip(".,"), 0.9
    d = _find_date(text, r"invoice date|date of invoice|tax date|tax point")
    if d:
        fields["date"], conf["date"] = d, 0.95        # an explicitly labelled invoice date
    else:
        d = _find_date(text, r"date")
        if d:
            fields["date"], conf["date"] = d, 0.8
    due = _find_date(text, r"due date|payment due|due")
    if due:
        fields["due"] = due
    total = _find_amount(text, r"total due|amount due|balance due|grand total|total")
    sub = _find_amount(text, r"subtotal|sub-total|amount excl(?:uding)?(?:\s+vat)?|excl\.? vat|net amount")
    tax = _find_amount(text, r"vat\s*(?:\d+%)?|gst|sales tax|tax")
    if total is not None:
        fields["total"], conf["total"] = total, 0.9
    if sub is not None:
        fields["subtotal"] = sub
    if tax is not None:
        fields["tax"] = tax
    if total is not None and sub is not None and tax is not None:
        adds_up = abs(sub + tax - total) < 0.02
        conf["total"] = 0.98 if adds_up else 0.6
        fields["adds_up"] = adds_up
    m = re.search(r"(?:order\s*no|p\.?o\.?\s*(?:no|number|#)?|purchase order)\s*[:#]?\s*(PO-?\d+|\d{3,})", text, re.I)
    if m:
        po = m.group(1).upper()
        fields["po_number"] = po if po.startswith("PO") else f"PO-{po}"
    m = re.search(r"(?:vat|tax)\s*(?:no|number|reg)\.?\s*[:#]?\s*(\d{6,})", text, re.I)
    if m:
        fields["supplier_tax_number"] = m.group(1)
    m = re.search(r"(?:bank|pay into|account(?: no)?)\s*[:]?\s*([A-Za-z ]{0,20}\d{6,})", text, re.I)
    if m:
        fields["bank_details"] = m.group(1).strip()
    items = [l for l in lines if re.search(r"\d+\s*x\s*R?\d", l, re.I)]
    fields["description"] = (items[0] if items else next(
        (l for l in lines if 12 < len(l) < 80 and not re.search(r"invoice|date|total|vat|bill to|from:|order", l, re.I)
         and not re.search(r"\d{5,}", l)), ""))[:80]
    if kind == "receipt":
        m = re.search(r"paid\s*[:]?\s*(card|cash|eft)[^\n]*", text, re.I)
        if m:
            fields["payment_method"] = m.group(0).strip()
    fields["confidence"] = conf
    required = ("supplier", "reference", "date", "total") if kind == "invoice" else ("supplier", "date", "total")
    fields["missing"] = [k for k in required if k not in fields]
    fields["overall_confidence"] = round(min([conf.get(k, 0.0) for k in required]), 2)
    return fields


async def _suppliers(s) -> list[str]:
    return [c["name"] for c in await s.provider.contacts("supplier")]


# -- tools -------------------------------------------------------------------------------------

@accounting_tool("find_invoice_attachments", "List documents waiting in the client's Inbox (PDFs, images, emails - "
                 "email attachments are unpacked), with a type guess and whether each was already processed.", R,
                 {"folder": PATH, "include_processed": {"type": "boolean"}})
async def find_invoice_attachments(s, args, ctx) -> Out:
    folder = resolve_in_client(s, args["folder"]) if args.get("folder") else s.ws.inbox
    found = []
    for dirpath, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in ("processed", "bank", "_attachments")]
        for f in sorted(files):
            if not f.lower().endswith(DOC_EXTS):
                continue
            path = os.path.join(dirpath, f)
            if f.lower().endswith(".eml"):
                for att in save_email_attachments(path, os.path.join(folder, "_attachments")):
                    found.append(att)
            else:
                found.append(path)
    for att in sorted(os.listdir(os.path.join(folder, "_attachments"))) if os.path.isdir(
            os.path.join(folder, "_attachments")) else []:
        p = os.path.join(folder, "_attachments", att)
        if p not in found:
            found.append(p)
    rows = []
    for p in found:
        done = s.drafts.is_processed(p)
        if done and not args.get("include_processed"):
            continue
        try:
            kind, conf = classify_text(text_of(p)[:4000])
        except Exception as exc:  # noqa: BLE001 - one unreadable file must not hide the rest
            kind, conf = f"unreadable ({exc})"[:60], 0.0
        rows.append({"path": rel(s, p), "type": kind, "confidence": f"{conf:.0%}",
                     "processed": done["result"] if done else ""})
    return Out(f"{len(rows)} document(s) in {rel(s, folder)}\n" + lines_table(rows, ["path", "type", "confidence", "processed"]),
               data={"documents": rows})


@accounting_tool("classify_document", "What kind of document is this (invoice, credit note, receipt, supplier "
                 "statement, bank statement, remittance, PO, payslip, tax notice)?", R, {"path": PATH}, ["path"])
async def classify_document(s, args, ctx) -> Out:
    path = resolve_in_client(s, args["path"])
    kind, conf = classify_text(text_of(path))
    return Out(f"{rel(s, path)}: {kind} ({conf:.0%})", data={"type": kind, "confidence": conf, "path": path})


@accounting_tool("extract_invoice_data", "Read an invoice and extract supplier, their invoice number, dates, "
                 "subtotal, tax, total, PO and bank details, each with a confidence.", R, {"path": PATH}, ["path"])
async def extract_invoice_data(s, args, ctx) -> Out:
    path = resolve_in_client(s, args["path"])
    f = extract_fields(text_of(path), await _suppliers(s))
    return Out(_describe(s, f, path), data={"fields": f, "path": path})


@accounting_tool("extract_receipt_data", "Read a receipt: merchant, date, total, tax, payment method.", R,
                 {"path": PATH}, ["path"])
async def extract_receipt_data(s, args, ctx) -> Out:
    path = resolve_in_client(s, args["path"])
    f = extract_fields(text_of(path), await _suppliers(s), kind="receipt")
    hint = hint_for(text_of(path))
    if hint:
        f["suggested_account"] = hint[0]
    return Out(_describe(s, f, path), data={"fields": f, "path": path})


def _describe(s, f: dict[str, Any], path: str) -> str:
    keys = ("supplier", "reference", "date", "due", "subtotal", "tax", "total", "po_number", "bank_details",
            "description", "payment_method", "suggested_account")
    out = [f"{rel(s, path)} (overall confidence {f['overall_confidence']:.0%})"]
    for k in keys:
        if k in f:
            v = money(s, f[k]) if k in ("subtotal", "tax", "total") else f[k]
            out.append(f"  {k}: {v}" + (f"  ({f['confidence'][k]:.0%})" if k in f["confidence"] else ""))
    if f.get("adds_up") is False:
        out.append("  WARNING: subtotal + tax does not equal total")
    if f["missing"]:
        out.append(f"  missing: {', '.join(f['missing'])}")
    return "\n".join(out)


def _standard_name(f: dict[str, Any], ext: str, s) -> str:
    bits = [f.get("date") or date.today().isoformat(), f.get("supplier") or "Unknown",
            f.get("reference") or "", money(s, f["total"]) if f.get("total") is not None else ""]
    name = " ".join(b for b in bits if b)
    return re.sub(r'[<>:"/\\|?*]+', " ", name).strip() + ext


@accounting_tool("rename_document", "Rename a document to the standard 'YYYY-MM-DD Supplier Ref Amount' form "
                 "(or a name you give).", D, {"path": PATH, "new_name": {"type": "string"}}, ["path"])
async def rename_document(s, args, ctx) -> Out:
    path = resolve_in_client(s, args["path"])
    ext = os.path.splitext(path)[1]
    name = args.get("new_name") or _standard_name(extract_fields(text_of(path), await _suppliers(s)), ext, s)
    if not name.lower().endswith(ext.lower()):
        name += ext
    target = os.path.join(os.path.dirname(path), name)
    if os.path.exists(target) and os.path.abspath(target) != os.path.abspath(path):
        return Out("", ok=False, error=f"{name} already exists")
    os.replace(path, target)
    return Out(f"Renamed to {name}", data={"path": target, "old_path": path})


@accounting_tool("file_document", "Move a document into Documents/<Type>/<YYYY-MM>/ (its type is detected if not "
                 "given).", D, {"path": PATH, "doc_type": {"type": "string"}}, ["path"])
async def file_document(s, args, ctx) -> Out:
    path = resolve_in_client(s, args["path"])
    kind = args.get("doc_type") or classify_text(text_of(path))[0]
    folder = DOCUMENT_TYPES.get(kind, {"folder": "Other"})["folder"]
    f = extract_fields(text_of(path), await _suppliers(s))
    month = (f.get("date") or date.today().isoformat())[:7]
    target_dir = s.ws.path("Documents", folder, month)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, os.path.basename(path))
    if os.path.abspath(target) != os.path.abspath(path):
        if os.path.exists(target):
            stem, ext = os.path.splitext(target)
            target = f"{stem} ({date.today().isoformat()}){ext}"
        shutil.move(path, target)
    return Out(f"Filed {os.path.basename(path)} under {rel(s, target_dir)}", data={"path": target, "type": kind})


@accounting_tool("process_invoice_document", "The invoice workflow for one document: extract, check duplicate, "
                 "check supplier, match PO, suggest coding, draft the bill (confidence-routed), file the original, "
                 "and flag only what is uncertain.", D, {"path": PATH}, ["path"])
async def process_invoice_document(s, args, ctx) -> Out:
    path = resolve_in_client(s, args["path"])
    done = s.drafts.is_processed(path)
    if done:
        return Out(f"Already processed ({done['result']}{', ' + done['draft_id'] if done['draft_id'] else ''}).",
                   data={"result": "already", "path": path})
    text = text_of(path)
    kind, _ = classify_text(text)
    if kind not in ("invoice", "credit_note"):
        return Out(f"{os.path.basename(path)} is a {kind}, not an invoice - left for the right workflow.",
                   data={"result": "skipped", "type": kind, "path": path})
    f = extract_fields(text, await _suppliers(s))
    findings = []
    if f["missing"]:
        s.drafts.mark_processed(path, "needs input")
        return Out(f"Could not read {', '.join(f['missing'])} from {os.path.basename(path)}.",
                   data={"result": "ask", "fields": f},
                   findings=[F(f"Could not read invoice {os.path.basename(path)}",
                               f"Missing {', '.join(f['missing'])}", entity=os.path.basename(path),
                               action="key it in, or ask the supplier for a clearer copy")])
    dups = await duplicate_bill(s, f["supplier"], f.get("reference", ""), f["total"], f["date"])
    if dups:
        dest = _move(s, path, "Duplicates")
        s.drafts.mark_processed(dest, "duplicate")
        return Out(f"DUPLICATE: {f['supplier']} {f['reference']} {money(s, f['total'])} is already {', '.join(dups)}. "
                   f"Not drafted; moved to {rel(s, dest)}.", data={"result": "duplicate", "fields": f, "path": dest},
                   findings=[F(f"Duplicate supplier invoice {f['supplier']} {f['reference']} received again",
                               f"Already {', '.join(dups)}", severity="review",
                               entity=f"{f['supplier']} {f['reference']}", action="no action unless it differs")])
    contacts = {c["name"]: c for c in await s.provider.contacts("supplier")}
    known = contacts.get(f["supplier"])
    history = [b for b in await s.provider.invoices("purchase", "all") if b.party == f["supplier"]]
    code, conf, why = None, 0.4, "no history"
    if known and known.get("default_account"):
        code = known["default_account"]
        conf = 0.97 if history else 0.9
        why = f"supplier's usual account ({len(history)} previous bill(s))"
    else:
        hint = hint_for(text)
        if hint:
            code = await account_code(s, hint[0])
            conf, why = hint[1], f"description suggests {hint[0]}"
    if code is None:
        code, conf, why = "1900", 0.3, "no idea - suspense"
    if not known:
        conf = min(conf, 0.8)
    if f.get("adds_up") is False:
        conf -= 0.2
    # How well the page was read scales how sure we are of the coding. The
    # supplier field is left out: an unknown supplier is already capped above.
    read = min(f["confidence"].get(k, 0.0) for k in ("reference", "date", "total"))
    conf = max(0.0, min(0.99, conf * read / 0.9 if read < 0.9 else conf))
    net = f.get("subtotal") if f.get("subtotal") is not None else None
    args2 = {"supplier": f["supplier"], "date": f["date"], "reference": f["reference"], "confidence": round(conf, 2),
             "po_number": f.get("po_number", ""), "document": "", "account": code,
             "reason": f"auto-coded: {why}", "description": f.get("description") or "Per invoice"}
    if net is not None:
        args2["items"] = [{"description": f.get("description") or "Per invoice", "quantity": 1,
                           "unit_price": net, "account": code}]
    else:
        args2["total"] = f["total"]
    dest = _move(s, path, "")
    args2["document"] = dest
    out = await create_draft_bill(s, args2, ctx)
    draft = out.data.get("draft")
    s.drafts.mark_processed(dest, out.data.get("route", "?"), draft["id"] if draft else "")
    return Out(f"{os.path.basename(path)}: {f['supplier']} {f['reference']} {money(s, f['total'])} -> "
               f"{code} ({why}, confidence {conf:.0%}). {out.text}",
               data={"result": out.data.get("route"), "fields": f, "draft": draft, "path": dest},
               findings=findings + out.findings, checks=5)


def _move(s, path: str, sub: str) -> str:
    """File a processed original under Documents/Invoices/<YYYY-MM>/ (or a sub-folder)."""
    month = date.today().strftime("%Y-%m")
    dest_dir = s.ws.path("Documents", "Invoices", sub or month)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    if os.path.abspath(dest) == os.path.abspath(path):
        return path
    if os.path.exists(dest):
        stem, ext = os.path.splitext(dest)
        dest = f"{stem} (2){ext}"
    shutil.move(path, dest)
    return dest


@accounting_tool("process_inbox", "Run the invoice workflow over every unprocessed invoice in the Inbox (or the "
                 "files given); statements and receipts are reported for their own workflows.", D,
                 {"files": {"type": "array", "items": {"type": "string"}}})
async def process_inbox(s, args, ctx) -> Out:
    paths = [resolve_in_client(s, p) for p in (args.get("files") or [])]
    if not paths:
        for dirpath, dirs, files in os.walk(s.ws.inbox):
            dirs[:] = [d for d in dirs if d.lower() not in ("processed", "bank")]
            paths += [os.path.join(dirpath, f) for f in sorted(files) if f.lower().endswith(DOC_EXTS)]
    results, findings, checks = [], [], 0
    for p in paths:
        if not os.path.isfile(p):
            continue
        if p.lower().endswith(".eml"):
            for att in save_email_attachments(p, os.path.join(s.ws.inbox, "_attachments")):
                paths.append(att)
            continue
        try:
            out = await process_invoice_document(s, {"path": p}, ctx)
        except Exception as exc:  # noqa: BLE001 - one bad document must not stop the inbox
            results.append({"file": os.path.basename(p), "result": f"error: {exc}"[:60]})
            findings.append(F(f"Could not process {os.path.basename(p)}", str(exc), entity=os.path.basename(p)))
            continue
        findings += out.findings
        checks += out.checks
        d = out.data.get("draft") or {}
        results.append({"file": os.path.basename(p), "result": out.data.get("result") or out.data.get("type", ""),
                        "draft": d.get("id", ""), "status": d.get("status", "")})
    return Out(f"Processed {len(results)} document(s)\n" + lines_table(results, ["file", "result", "draft", "status"]),
               data={"results": results}, findings=findings, checks=checks)


@accounting_tool("reconcile_inbox_statements", "Find supplier statements in the Inbox, work out whose they are, "
                 "and reconcile each one to payables.", R)
async def reconcile_inbox_statements(s, args, ctx) -> Out:
    from .checks import supplier_statement_reconcile
    suppliers = await _suppliers(s)
    texts, findings, checks, done = [], [], 0, 0
    for dirpath, dirs, files in os.walk(s.ws.inbox):
        dirs[:] = [d for d in dirs if d.lower() not in ("processed", "bank")]
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            if not f.lower().endswith((".pdf", ".csv", ".xlsx", ".txt")):
                continue
            try:
                text = text_of(p) if not f.lower().endswith((".csv", ".xlsx")) else ""
            except Exception:  # noqa: BLE001
                continue
            if text and classify_text(text)[0] != "supplier_statement":
                continue
            who = next((sup for sup in suppliers if sup.lower() in (text or f).lower()
                        or sup.lower().split()[0] in (text or f).lower()), "")
            if not who:
                findings.append(F(f"Supplier statement {f}: supplier not recognised", entity=f,
                                  action="tell the agent whose statement it is"))
                continue
            out = await supplier_statement_reconcile(s, {"supplier": who, "path": p}, ctx)
            texts.append(out.text)
            findings += out.findings
            checks += out.checks
            done += 1
            dest_dir = s.ws.path("Documents", "Statements")
            os.makedirs(dest_dir, exist_ok=True)
            shutil.move(p, os.path.join(dest_dir, f))
    return Out("\n\n".join(texts) or "No supplier statements in the Inbox.", data={"reconciled": done},
               findings=findings, checks=checks)
