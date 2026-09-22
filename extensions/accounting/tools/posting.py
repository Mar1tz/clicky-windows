"""POST and SENSITIVE tools: the only way anything reaches the books, a bank or an authority.

post_drafts            POST       always asks; one approval can cover a batch of drafts
release_payment_batch  SENSITIVE  asks every time, cannot be granted for a task, needs "RELEASE" typed
submit_tax_return      SENSITIVE  same, needs "SUBMIT"

The registry enforces the asking (see agents/tools/registry.py); these
functions only ever run after a person said yes.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..books import to_minor
from .common import ActionLevel, F, Out, accounting_tool, lines_table, money, rel

P, S = ActionLevel.POST, ActionLevel.SENSITIVE


async def _post_one(s, d: dict[str, Any]) -> str:
    p, kind, prov = d["payload"], d["kind"], s.provider
    if kind == "journal":
        ref = await prov.post_journal(p["date"], p["description"], p["lines"], reference=d["id"])
        if p.get("reverse_on"):
            rev = [{"account": l["account"], "debit": l["credit"], "credit": l["debit"], "memo": "reversal"}
                   for l in p["lines"]]
            ref += " + " + await prov.post_journal(p["reverse_on"], "Reversal: " + p["description"], rev,
                                                   reference=d["id"] + "-R")
        return ref
    if kind == "bill":
        ref = await prov.post_bill(p["supplier"], p["date"], p["items"], reference=p["reference"],
                                   po_number=p.get("po_number", ""), document=p.get("document", ""),
                                   due_days=int(p.get("due_days") or 30))
        return ref
    if kind == "invoice":
        return await prov.post_invoice(p["customer"], p["date"], p["items"], due_days=int(p.get("due_days") or 30),
                                       reference=p.get("reference", ""))
    if kind == "bank_line":
        if prov.supports("post_bank_line"):
            ref = await prov.post_bank_line(p["line_id"], p["account"], tax_rate=p.get("tax_rate"))
            if p.get("learn_pattern") and hasattr(prov, "ledger"):
                prov.ledger.learn(p["learn_pattern"], p["account"], tax_rate=p.get("tax_rate"))
            return ref
        amt = abs(float(p["amount"]))
        bank = s.ws.bank_accounts[0] if s.ws.bank_accounts else "1000"
        lines = ([{"account": p["account"], "debit": amt}, {"account": bank, "credit": amt}] if p["amount"] < 0
                 else [{"account": bank, "debit": amt}, {"account": p["account"], "credit": amt}])
        return await prov.post_journal(p["date"], p["description"], lines, reference=d["id"])
    if kind == "match":
        return await prov.apply_bank_match(p["line_id"], p["target"])
    raise ValueError(f"{d['id']} is a {kind}; it is not posted with post_drafts")


@accounting_tool("post_drafts", "Post approved drafts (journals, bills, invoices, bank categorisations, "
                 "matches) into the books. Needs the accountant's approval.", P,
                 {"draft_ids": {"type": "array", "items": {"type": "string"}}}, ["draft_ids"])
async def post_drafts(s, args, ctx) -> Out:
    done, failed = [], []
    for did in args["draft_ids"]:
        try:
            d = s.drafts.get(did)
        except KeyError as exc:
            failed.append({"id": did, "error": str(exc.args[0])})
            continue
        if d["status"] not in ("draft", "review", "approved"):
            failed.append({"id": d["id"], "error": f"status is {d['status']}"})
            continue
        try:
            ref = await _post_one(s, d)
        except Exception as exc:  # noqa: BLE001 - one bad draft must not stop the batch
            failed.append({"id": d["id"], "error": str(exc)})
            continue
        s.drafts.set_status(d["id"], "posted", posted_ref=ref)
        done.append({"id": d["id"], "title": d["title"], "ref": ref})
    text = f"Posted {len(done)} draft(s)" + (f", {len(failed)} failed" if failed else "") + "\n" + \
        lines_table(done, ["id", "ref", "title"]) + \
        ("\nFailed:\n" + lines_table(failed, ["id", "error"]) if failed else "")
    findings = [F(f"Could not post {f['id']}", f["error"], entity=f["id"]) for f in failed]
    return Out(text, data={"posted": done, "failed": failed}, findings=findings, ok=bool(done) or not failed,
               error="nothing was posted" if failed and not done else "")


@accounting_tool("release_payment_batch", "Release a reviewed payment batch to the bank. SENSITIVE: the "
                 "accountant must type RELEASE. Never call this unless the accountant asked for it.", S,
                 {"draft_id": {"type": "string"}}, ["draft_id"])
async def release_payment_batch(s, args, ctx) -> Out:
    d = s.drafts.get(args["draft_id"])
    if d["kind"] != "payment_batch":
        return Out("", ok=False, error=f"{d['id']} is not a payment batch")
    if d["status"] not in ("review", "approved", "draft"):
        return Out("", ok=False, error=f"{d['id']} is {d['status']}")
    payments = d["payload"]["payments"]
    if not payments:
        return Out("", ok=False, error="the batch has no payments")
    ref = await s.provider.release_payment_batch(payments, reference=d["id"])
    s.drafts.set_status(d["id"], "released", posted_ref=ref)
    return Out(f"Released {d['id']}: {len(payments)} payment(s), {money(s, d['payload']['total'])}. Ref: {ref}",
               data={"reference": ref, "draft": s.drafts.get(d["id"])})


@accounting_tool("submit_tax_return", "Submit a prepared tax return. SENSITIVE: the accountant must type "
                 "SUBMIT. Never call this unless the accountant asked for it.", S,
                 {"draft_id": {"type": "string"}}, ["draft_id"])
async def submit_tax_return(s, args, ctx) -> Out:
    d = s.drafts.get(args["draft_id"])
    if d["kind"] != "tax_return":
        return Out("", ok=False, error=f"{d['id']} is not a tax return")
    if d["status"] in ("submitted", "rejected"):
        return Out("", ok=False, error=f"{d['id']} is {d['status']}")
    ref = await s.provider.submit_tax_return(d["payload"], reference=d["id"])
    s.drafts.set_status(d["id"], "submitted", posted_ref=ref)
    return Out(f"Submitted {d['id']}. Reference: {ref}", data={"reference": ref})
