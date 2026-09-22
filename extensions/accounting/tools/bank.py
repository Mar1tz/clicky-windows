"""Bank tools: import statements, suggest reconciliations, draft categorisations and matches."""
from __future__ import annotations

import os
import shutil
from datetime import date
from typing import Any, Optional

from ..knowledge import hint_for
from ..tables import read_table
from .common import (
    CONFIDENCE, PATH, ActionLevel, F, Out, accounting_tool, lines_table, money, policy, rel,
    resolve_in_client,
)

R, D = ActionLevel.READ, ActionLevel.DRAFT


async def account_code(s, ref: str) -> Optional[str]:
    """Code for a code or (fragment of) an account name in this client's chart."""
    ref = (ref or "").strip()
    if not ref:
        return None
    accts = await s.provider.accounts()
    for a in accts:
        if a.code == ref or a.name.lower() == ref.lower():
            return a.code
    hits = [a for a in accts if ref.lower() in a.name.lower()]
    return hits[0].code if len(hits) == 1 else (hits[0].code if hits else None)


async def account_names(s) -> dict[str, str]:
    return {a.code: a.name for a in await s.provider.accounts()}


@accounting_tool("import_bank_statement", "Stage a bank statement file (CSV or XLSX) into the client's bank "
                 "lines. Stages only - nothing is matched or posted. Duplicates are skipped.", D,
                 {"path": PATH, "account": {"type": "string", "description": "Bank account code (default 1000)."}},
                 ["path"])
async def import_bank_statement(s, args, ctx) -> Out:
    path = resolve_in_client(s, args["path"])
    if s.provider.supports("import_bank_statement"):
        rows = read_table(path)
        rep = await s.provider.import_bank_statement(rows, account=args.get("account") or "1000",
                                                     source_file=os.path.basename(path))
        text = (f"Imported {rep['imported']} line(s) from {os.path.basename(path)} "
                f"({rep['first_date']} to {rep['last_date']}); {rep['duplicates']} duplicate(s) skipped, "
                f"{rep['skipped']} unreadable. {rep['unresolved']} line(s) now unreconciled - run "
                "suggest_reconciliation.")
        findings = [] if not rep["skipped"] else [F(f"{rep['skipped']} statement row(s) could not be read",
                                                    f"File {os.path.basename(path)}; columns detected {rep['columns']}",
                                                    entity=os.path.basename(path))]
        _file_statement(s, path)
        return Out(text, data={"report": rep, "path": path}, findings=findings, checks=1)
    # A file-tier provider reads statements from its data folder: put it there.
    folder = s.ws.provider_settings.get("folder") or s.ws.path("Data")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, "bank_" + os.path.basename(path))
    shutil.copy2(path, target)
    return Out(f"Copied the statement to {rel(s, target)} for {s.provider.display_name}.",
               data={"path": target})


def _file_statement(s, path: str) -> None:
    """Move a processed statement out of the Inbox so the watch does not fire again."""
    inbox = os.path.normcase(os.path.abspath(s.ws.inbox))
    if os.path.normcase(os.path.abspath(path)).startswith(inbox):
        dest = s.ws.path("Documents", "Bank", os.path.basename(path))
        if not os.path.exists(dest):
            shutil.move(path, dest)


@accounting_tool("suggest_reconciliation", "Examine every unreconciled bank line and suggest a match (open "
                 "invoice/bill with the same amount) or an account (the client's rules, then keywords), with "
                 "a confidence. Lines nothing explains are raised as exceptions. Suggests only.", R,
                 {"account": {"type": "string"}, "limit": {"type": "integer"}})
async def suggest_reconciliation(s, args, ctx) -> Out:
    lines = await s.provider.bank_transactions(args.get("account") or None, unreconciled_only=True)
    lines = lines[: int(args.get("limit") or 200)]
    sales = [i for i in await s.provider.invoices("sales", "open") if i.outstanding > 0]
    bills = [i for i in await s.provider.invoices("purchase", "open") if i.outstanding > 0]
    names = await account_names(s)
    suggestions, findings = [], []
    for l in lines:
        sug: dict[str, Any] = {"line": l.id, "date": l.date, "description": l.description, "amount": l.amount}
        pool = sales if l.amount > 0 else bills
        same = [i for i in pool if abs(i.outstanding - abs(l.amount)) < 0.005]
        desc = l.description.lower()
        named = [i for i in same if i.number.lower() in desc or i.party.lower().split()[0] in desc]
        if named or len(same) == 1:
            target = (named or same)[0]
            sug.update(action="match", target=target.number, party=target.party,
                       confidence=0.98 if named else 0.85,
                       why=f"{'reference/party in description and ' if named else ''}same amount as "
                           f"{target.number} ({target.party})")
        elif l.suggested_account:
            sug.update(action="categorise", account=l.suggested_account,
                       account_name=names.get(l.suggested_account, ""), confidence=0.95,
                       why="matches one of the client's bank rules")
        else:
            hint = hint_for(l.description)
            code = await account_code(s, hint[0]) if hint else None
            if code:
                sug.update(action="categorise", account=code, account_name=names.get(code, ""),
                           confidence=hint[1], why=f"description suggests {names.get(code, code)}")
            elif len(same) > 1:
                sug.update(action="ask", confidence=0.5,
                           why=f"{len(same)} open items for this amount: " + ", ".join(i.number for i in same))
            else:
                sug.update(action="ask", confidence=0.0, why="nothing in the books explains it")
                findings.append(F(f"Bank line {l.date} {l.description[:40]} {money(s, l.amount)} is unexplained",
                                  "No open invoice or bill for this amount and no rule or keyword matches.",
                                  entity=l.id, action="ask the client what it is, or request the document"))
        suggestions.append(sug)
    rows = [{"line": x["line"], "date": x["date"], "amount": money(s, x["amount"]),
             "suggest": (f"match {x.get('target')}" if x.get("action") == "match"
                         else f"{x.get('account')} {x.get('account_name', '')}" if x.get("action") == "categorise"
                         else "ASK"), "conf": f"{x['confidence']:.0%}", "why": x["why"][:50]} for x in suggestions]
    return Out(f"{len(lines)} unreconciled line(s) examined\n"
               + lines_table(rows, ["line", "date", "amount", "suggest", "conf", "why"], 60),
               data={"suggestions": suggestions}, findings=findings, checks=len(lines))


def _route(s, confidence: Any) -> str:
    return policy(s).route(confidence)


@accounting_tool("categorize_transaction", "Draft the coding of one bank line to an account. The client's "
                 "confidence policy decides: draft, draft + review flag, or refuse and ask.", D,
                 {"line_id": {"type": "string", "description": "Bank line id, e.g. bl-12."},
                  "account": {"type": "string", "description": "Account code or name."},
                  "confidence": CONFIDENCE, "tax_rate": {"type": "number", "description": "Override tax %."},
                  "reason": {"type": "string"},
                  "learn_pattern": {"type": "string", "description": "Text that identifies future lines like this "
                                                                     "(becomes a rule when the draft is posted)."}},
                 ["line_id", "account", "confidence"])
async def categorize_transaction(s, args, ctx) -> Out:
    line = next((l for l in await s.provider.bank_transactions() if l.id == args["line_id"]), None)
    if line is None:
        return Out("", ok=False, error=f"no bank line {args['line_id']}")
    if line.reconciled:
        return Out("", ok=False, error=f"{line.id} is already reconciled ({line.journal_ref})")
    code = await account_code(s, args["account"])
    if code is None:
        return Out("", ok=False, error=f"no account matches {args['account']!r}")
    names = await account_names(s)
    conf = float(args["confidence"])
    route = _route(s, conf)
    what = f"{line.date} {line.description[:40]} {money(s, line.amount)} -> {code} {names.get(code, '')}"
    if route == "ask":
        f = F(f"Coding needed: {line.description[:40]} {money(s, line.amount)}",
              f"Best guess {code} {names.get(code, '')} at {conf:.0%} - below the automation threshold. "
              + (args.get("reason") or ""), entity=line.id, action="tell the agent which account")
        return Out(f"Not drafted (confidence {conf:.0%} is below the threshold): {what}", findings=[f],
                   data={"route": "ask"})
    for d in s.drafts.list(status="draft,review", kind="bank_line"):
        if d["payload"].get("line_id") == line.id:
            return Out(f"Already drafted as {d['id']}: {d['title']}", data={"draft": d, "route": "exists"})
    d = s.drafts.add("bank_line", what, {"line_id": line.id, "account": code, "amount": line.amount,
                                         "date": line.date, "description": line.description,
                                         "tax_rate": args.get("tax_rate"),
                                         "learn_pattern": args.get("learn_pattern") or ""},
                     confidence=conf, status="draft" if route == "draft" else "review",
                     reason=args.get("reason") or "", task_id=ctx.task_id or "")
    findings = [] if route == "draft" else [F(f"Check coding: {line.description[:40]} -> {names.get(code, code)}",
                                              f"Drafted at {conf:.0%} confidence ({d['id']}). {args.get('reason') or ''}",
                                              entity=d["id"], action="approve or recode the draft")]
    return Out(f"Drafted {d['id']} ({route}): {what}", data={"draft": d, "route": route}, findings=findings)


@accounting_tool("match_transaction", "Draft a match between a bank line and an open invoice/bill (by number) "
                 "or a posted journal (J12).", D,
                 {"line_id": {"type": "string"}, "target": {"type": "string",
                                                            "description": "Invoice/bill number or journal id."},
                  "confidence": CONFIDENCE, "reason": {"type": "string"}},
                 ["line_id", "target", "confidence"])
async def match_transaction(s, args, ctx) -> Out:
    line = next((l for l in await s.provider.bank_transactions() if l.id == args["line_id"]), None)
    if line is None:
        return Out("", ok=False, error=f"no bank line {args['line_id']}")
    if line.reconciled:
        return Out("", ok=False, error=f"{line.id} is already reconciled ({line.journal_ref})")
    target = args["target"].strip()
    conf = float(args["confidence"])
    route = _route(s, conf)
    if not target.upper().startswith("J"):
        docs = {i.number: i for i in await s.provider.invoices("sales" if line.amount > 0 else "purchase", "open")}
        doc = docs.get(target)
        if doc is None:
            return Out("", ok=False, error=f"no open {'invoice' if line.amount > 0 else 'bill'} {target}")
        if abs(doc.outstanding - abs(line.amount)) >= 0.005:
            route = "review" if route == "draft" else route
    what = f"{line.date} {line.description[:40]} {money(s, line.amount)} = {target}"
    if route == "ask":
        return Out(f"Not drafted (confidence {conf:.0%}): {what}", data={"route": "ask"},
                   findings=[F(f"Confirm match: {line.description[:40]} -> {target}", args.get("reason") or "",
                               entity=line.id, action="confirm or reject the match")])
    d = s.drafts.add("match", what, {"line_id": line.id, "target": target, "amount": line.amount},
                     confidence=conf, status="draft" if route == "draft" else "review",
                     reason=args.get("reason") or "", task_id=ctx.task_id or "")
    findings = [] if route == "draft" else [F(f"Check match: {line.description[:40]} -> {target}",
                                              f"{d['id']} at {conf:.0%}", entity=d["id"])]
    return Out(f"Drafted {d['id']} ({route}): {what}", data={"draft": d, "route": route}, findings=findings)


@accounting_tool("import_inbox_statements", "Import every bank statement file (CSV/XLSX) waiting in Inbox/Bank.", D)
async def import_inbox_statements(s, args, ctx) -> Out:
    folder = s.ws.path("Inbox", "Bank")
    files = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
             if f.lower().endswith((".csv", ".xlsx", ".xlsm"))] if os.path.isdir(folder) else []
    if not files:
        return Out("No statements waiting in Inbox/Bank.", data={"imported": 0})
    texts, findings, total = [], [], 0
    for p in files:
        out = await import_bank_statement(s, {"path": p}, ctx)
        texts.append(out.text)
        findings += out.findings
        total += (out.data.get("report") or {}).get("imported", 0)
    return Out("\n".join(texts), data={"imported": total, "files": [os.path.basename(p) for p in files]},
               findings=findings, checks=len(files))


@accounting_tool("draft_from_suggestions", "Run suggest_reconciliation and draft every match and categorisation "
                 "it proposes, each at its suggested confidence (the client's policy then drafts, flags or asks). "
                 "Unexplained lines stay as exceptions.", D, {"account": {"type": "string"}})
async def draft_from_suggestions(s, args, ctx) -> Out:
    sug = await suggest_reconciliation(s, {"account": args.get("account")}, ctx)
    drafted, review, asked = [], [], []
    findings = list(sug.findings)
    for x in sug.data["suggestions"]:
        if x.get("action") == "match":
            out = await match_transaction(s, {"line_id": x["line"], "target": x["target"],
                                              "confidence": x["confidence"], "reason": x["why"]}, ctx)
        elif x.get("action") == "categorise":
            out = await categorize_transaction(s, {"line_id": x["line"], "account": x["account"],
                                                   "confidence": x["confidence"], "reason": x["why"]}, ctx)
        else:
            continue
        findings += out.findings
        route = out.data.get("route")
        (drafted if route == "draft" else review if route == "review" else asked).append(x["line"])
    return Out(f"{len(sug.data['suggestions'])} line(s): {len(drafted)} drafted, {len(review)} drafted for review, "
               f"{len(asked)} need the accountant; the rest are exceptions.",
               data={"drafted": drafted, "review": review, "asked": asked}, findings=findings, checks=sug.checks)
