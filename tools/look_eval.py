"""The look test bench, as first run on 5 Oct 2026 (docs/SHOW_THE_NEXT_MOVE.md, UD-01).

STARTER for SP-01 (docs/SHOW_DONT_TELL_POLISH.md). It builds 16 everyday screens as
HTML, renders them headless with Edge (nothing opens on screen), asks the fast model
about each and the deep model where the fast one was unsure or missed (up to 10), and
prints right-thing-drawn, time and cost. SP-01 adds the flags (--fast-only,
--max-dollars, --only), the extra screens, and the exact app routing through look().

PAID: about $0.10 a run, on the owner's own key (TORA_ACCESS=own_key) - never the plan.

    python tools/look_eval.py --render     # the screens only, free (writes a contact sheet)
    python tools/look_eval.py              # the comparison
"""
import asyncio
import base64
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image  # noqa: E402

import config  # noqa: E402,F401 - loads .env
os.environ["TORA_ACCESS"] = "own_key"          # straight to Google with the owner's key, not the plan
from ai import gemini, gemini_vision  # noqa: E402
from agents.tools import design_tools  # noqa: E402

assert gemini.own_key() and gemini.api_key(), "no own key"
W, H = 1600, 900
OUT = Path(tempfile.gettempdir()) / "tora-look-eval"
OUT.mkdir(exist_ok=True)
PRICE = {"fast": (0.5, 3.0), "deep": (2.5, 15.0)}   # $ per million in / out (server's table)


def box(x, y, w, h, inner="", style=""):
    return f'<div style="position:absolute;left:{x}px;top:{y}px;width:{w}px;height:{h}px;{style}">{inner}</div>'


BTN = "background:#2563eb;color:#fff;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:600"
GHOST = "border:2px solid #94a3b8;color:#334155;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:20px"
FIELD = "border:2px solid #cbd5e1;border-radius:6px;background:#fff;font-size:18px;color:#94a3b8;display:flex;align-items:center;padding-left:12px"
ROW = "border-bottom:1px solid #e2e8f0;display:flex;align-items:center;font-size:20px;color:#1e293b;padding-left:16px"
BASE = "<body style='margin:0;font-family:Segoe UI;background:#f8fafc;position:relative'>"


def scenes():
    s = []
    s.append(("signin", "where do I click to sign in?", BASE +
              box(600, 180, 400, 520, "", "background:#fff;border-radius:12px;box-shadow:0 4px 20px #0002") +
              box(640, 210, 320, 50, "<b style='font-size:30px'>Sign in to Acme</b>") +
              box(640, 300, 320, 44, "Email", FIELD) + box(640, 370, 320, 44, "Password", FIELD) +
              box(640, 428, 320, 30, "<span style='color:#2563eb;font-size:16px'>Forgot password?</span>") +
              box(640, 480, 320, 48, "Sign in", BTN) +
              box(700, 560, 200, 30, "<span style='color:#2563eb;font-size:16px'>Create account</span>"),
              (640, 480, 320, 48), None))
    rows = ["Notifications", "Dark mode", "Location services", "Auto-update"]
    html = BASE + box(400, 120, 800, 60, "<b style='font-size:32px'>Settings</b>")
    for i, label in enumerate(rows):
        y = 200 + i * 80
        on = i in (0, 3)
        html += box(400, y, 600, 40, label, "font-size:22px;display:flex;align-items:center")
        html += box(1100, y + 4, 60, 32, box(32 if on else 4, 4, 24, 24, "", "background:#fff;border-radius:50%"),
                    f"background:{'#22c55e' if on else '#cbd5e1'};border-radius:16px")
    s.append(("darkmode", "which switch turns on dark mode?", html, (1100, 284, 60, 32), None))
    html = BASE
    for i, (name, price, stars, rating) in enumerate([("Breeze Fan", "$29", "★★★☆☆", "3.4"), ("Arctic Fan", "$39", "★★★★☆", "4.3"),
                                                      ("Polar Pro Fan", "$59", "★★★★★", "4.8")]):
        x = 200 + i * 450
        html += box(x, 220, 320, 420, "", "background:#fff;border-radius:12px;box-shadow:0 2px 12px #0002")
        html += box(x + 24, 250, 272, 160, "", "background:#e0f2fe;border-radius:8px")
        html += box(x + 24, 430, 272, 40, f"<b style='font-size:24px'>{name}</b>")
        html += box(x + 24, 475, 272, 40, f"<span style='font-size:28px'>{price}</span>")
        html += box(x + 24, 520, 272, 30, f"<span style='color:#f59e0b;font-size:20px'>{stars}</span> {rating}")
        html += box(x + 24, 570, 272, 44, "Add to cart", BTN)
    s.append(("cheapest4star", "which one should I pick? I want the cheapest one with at least 4 stars", html,
              (650, 220, 320, 420), None))
    html = BASE + box(300, 80, 1000, 50, "<b style='font-size:28px'>Sales 2026</b>")
    for i, (m, v) in enumerate(zip(["Jan", "Feb", "Mar", "Apr", "May", "Jun"], [40, 55, 35, 70, 90, 60])):
        x = 300 + i * 160
        html += box(x, 750 - v * 5, 80, v * 5, "", "background:#3b82f6")
        html += box(x, 760, 80, 30, m, "text-align:center;font-size:20px")
    s.append(("chart", "which month had the highest sales?", html, (940, 300, 80, 450), None))
    html = BASE
    data = [("Item", "Qty", "Price", "Total"), ("Pens", "4", "2.50", "10.00"), ("Paper", "2", "6.00", "12.00"),
            ("Stapler", "1", "15.00", "15.00"), ("Folders", "5", "3.00", "18.00"), ("Tape", "3", "1.50", "4.50")]
    for r, row in enumerate(data):
        for c, v in enumerate(row):
            html += box(300 + c * 200, 200 + r * 44, 200, 44, v,
                        "border:1px solid #cbd5e1;background:#fff;font-size:18px;display:flex;align-items:center;"
                        "padding-left:10px" + (";font-weight:700;background:#f1f5f9" if r == 0 else ""))
    s.append(("wrongcell", "which cell is wrong?", html, (900, 376, 200, 44), None))
    s.append(("diskdialog", "what should I click to fix this?", BASE + box(0, 0, W, H, "", "background:#0005") +
              box(550, 300, 500, 260, "", "background:#fff;border-radius:12px") +
              box(580, 320, 440, 40, "<b style='font-size:24px'>Your disk is almost full</b>") +
              box(580, 370, 440, 80, "<span style='font-size:18px'>Only 1.2 GB left on drive C:. Programs may stop working.</span>") +
              box(600, 480, 180, 44, "Remind me later", GHOST) + box(820, 480, 180, 44, "Free up space", BTN),
              (820, 480, 180, 44), None))
    html = BASE + box(200, 110, 1200, 50, "<b style='font-size:28px'>Inbox</b>")
    for i, (who, subj) in enumerate([("Newsletter Weekly", "Ten tips for a tidy desk"), ("Tom Baker", "Football on Saturday?"),
                                     ("Sarah Mitchell", "Q3 budget review - need your input"), ("Amazon", "Your order has shipped"),
                                     ("Mum", "Sunday lunch")]):
        html += box(200, 180 + i * 70, 1200, 64, f"<b style='width:300px'>{who}</b><span>{subj}</span>", ROW)
    s.append(("bossemail", "which email is from my boss Sarah?", html, (200, 320, 1200, 64), None))
    html = BASE + box(300, 120, 1000, 50, "<b style='font-size:28px'>Your basket</b>")
    for i, (item, p) in enumerate([("Wireless mouse", "$24.99"), ("USB-C cable", "$9.99"), ("Laptop stand", "$39.99")]):
        html += box(300, 200 + i * 80, 1000, 70, f"<span style='flex:1'>{item}</span><b style='margin-right:20px'>{p}</b>", ROW)
    html += box(900, 500, 480, 40, "<b style='font-size:24px'>Total: $74.97</b>")
    html += box(900, 700, 220, 52, "Continue shopping", GHOST) + box(1160, 700, 220, 52, "Checkout", BTN)
    s.append(("checkout", "how do I pay for this?", html, (1160, 700, 220, 52), None))
    icon = lambda x, svg: box(x, 735, 40, 32, f"<svg width='40' height='32' viewBox='0 0 40 32'>{svg}</svg>")  # noqa: E731
    html = BASE.replace("#f8fafc", "#111") + box(200, 100, 1200, 620, "", "background:linear-gradient(135deg,#334155,#0f172a)")
    html += box(200, 720, 1200, 60, "", "background:#000")
    html += icon(230, "<polygon points='12,6 30,16 12,26' fill='#fff'/>")
    html += icon(300, "<polygon points='6,12 14,12 22,5 22,27 14,20 6,20' fill='#fff'/><path d='M27 10 Q33 16 27 22' stroke='#fff' fill='none' stroke-width='2'/>")
    html += icon(1180, "<rect x='3' y='5' width='34' height='22' rx='4' fill='none' stroke='#fff' stroke-width='2.5'/><text x='20' y='21' font-size='12' fill='#fff' text-anchor='middle' font-family='Segoe UI' font-weight='700'>CC</text>")
    html += icon(1240, "<circle cx='20' cy='16' r='7' fill='none' stroke='#fff' stroke-width='3'/><circle cx='20' cy='16' r='12' fill='none' stroke='#fff' stroke-width='2' stroke-dasharray='4 3'/>")
    html += icon(1300, "<path d='M6 12 V6 H12 M28 6 H34 V12 M34 20 V26 H28 M12 26 H6 V20' stroke='#fff' fill='none' stroke-width='2.5'/>")
    s.append(("ccicon", "where's the subtitles button?", html, (1180, 735, 40, 32), None))
    html = BASE + box(600, 180, 400, 60, "<b style='font-size:28px'>Create your account</b>")
    html += box(600, 260, 400, 24, "Full name") + box(600, 288, 400, 44, "<span style='color:#1e293b'>Jamie Lee</span>", FIELD)
    html += box(600, 336, 400, 24, "Email") + box(600, 360, 400, 44, "", FIELD.replace("#cbd5e1", "#ef4444"))
    html += box(600, 406, 400, 24, "<span style='color:#ef4444;font-size:14px'>Required</span>")
    html += box(600, 440, 400, 24, "Phone") + box(600, 464, 400, 44, "<span style='color:#1e293b'>07700 900123</span>", FIELD)
    html += box(600, 540, 400, 48, "Create account", BTN)
    s.append(("missingfield", "what's missing in this form?", html, (600, 360, 400, 44), None))
    html = BASE + box(300, 130, 1000, 50, "<b style='font-size:28px'>Downloads</b>")
    for i, (n, sz) in enumerate([("report.pdf", "2.4 MB"), ("photo.jpg", "3.1 MB"), ("notes.txt", "4 KB"),
                                 ("holiday.mp4", "1.2 GB"), ("backup.zip", "860 MB")]):
        html += box(300, 200 + i * 56, 1000, 50, f"<span style='flex:1'>{n}</span><span style='margin-right:30px'>{sz}</span>", ROW)
    s.append(("biggestfile", "which file is the biggest?", html, (300, 368, 1000, 50), None))
    html = BASE + box(600, 120, 400, 40, "<b style='font-size:24px'>Tic-tac-toe - you are X, your turn</b>")
    for r, row in enumerate(["XX ", "OO ", "   "]):
        for c, ch in enumerate(row):
            html += box(650 + c * 100, 200 + r * 100, 100, 100, f"<b style='font-size:60px'>{ch.strip()}</b>",
                        "border:3px solid #1e293b;display:flex;align-items:center;justify-content:center;background:#fff")
    s.append(("tictactoe", "where should I play to win?", html, (850, 200, 100, 100), None))
    html = BASE + box(500, 150, 600, 60, "<b style='font-size:28px'>Homework - check my answers</b>")
    for i, line in enumerate(["12 + 7 = 19", "8 + 5 = 14", "9 + 6 = 15"]):
        html += box(500, 250 + i * 80, 600, 60, f"<span style='font-size:30px'>{line}</span>", "display:flex;align-items:center")
    s.append(("wrongsum", "which answer is wrong?", html, (500, 330, 600, 60), None))
    html = BASE + box(400, 120, 800, 50, "<b style='font-size:30px'>Menu</b>")
    for i, (dish, p) in enumerate([("Beef burger", "$14"), ("Mushroom risotto (V)", "$16"), ("Garden salad (V)", "$11"),
                                   ("Fish and chips", "$15"), ("Veggie lasagne (V)", "$13")]):
        html += box(400, 200 + i * 70, 800, 60, f"<span style='flex:1'>{dish}</span><b style='margin-right:20px'>{p}</b>", ROW)
    s.append(("vegmenu", "what's the cheapest vegetarian dish?", html, (400, 340, 800, 60), None))
    html = BASE + box(600, 160, 400, 60, "<b style='font-size:28px'>Payment</b>")
    for label, (x, y, w) in [("Name on card", (600, 260, 400)), ("Card number", (600, 340, 400)), ("MM / YY", (600, 420, 190)),
                             ("CVC", (810, 420, 190))]:
        html += box(x, y, w, 44, label, FIELD)
    html += box(600, 500, 400, 48, "Pay $49.00", BTN)
    s.append(("cardnumber", "where do I type my card number?", html, (600, 340, 400, 44), None))
    pieces = {(0, 0): "♖", (6, 0): "♔", (5, 1): "♙", (6, 1): "♙", (7, 1): "♙", (6, 7): "♚", (5, 6): "♟", (6, 6): "♟", (7, 6): "♟"}
    html = BASE
    for f in range(8):
        for r in range(8):
            html += box(500 + f * 80, 100 + (7 - r) * 80, 80, 80, f"<span style='font-size:58px'>{pieces.get((f, r), '')}</span>",
                        f"background:{'#EBECD0' if (f + r) % 2 else '#779556'};display:flex;align-items:center;justify-content:center")
        html += box(500 + f * 80, 745, 80, 24, "abcdefgh"[f], "text-align:center;font-size:18px")
    for r in range(8):
        html += box(470, 100 + (7 - r) * 80 + 28, 24, 24, str(r + 1), "font-size:18px")
    s.append(("chess", "what's my best move? I'm white", html, (500, 660, 80, 80), (500, 100, 80, 80)))
    return s


def jpeg_b64(png: Path, max_width=1024) -> str:
    im = Image.open(png).convert("RGB")
    im = im.resize((max_width, int(im.height * max_width / im.width)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=75, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def inside(nbox, rect, pad=12):
    if nbox is None:
        return False
    ymin, xmin, ymax, xmax = nbox
    cx, cy = (xmin + xmax) / 2000 * W, (ymin + ymax) / 2000 * H
    x, y, w, h = rect
    return x - pad <= cx <= x + w + pad and y - pad <= cy <= y + h + pad


def score(scene, look):
    name, q, _, target, to_target = scene
    if not look or not look.actions:
        return False
    acts = look.actions
    if gemini_vision.is_next_action(q):
        acts = ([a for a in acts if a.is_move] or acts)[:1]
    a = acts[0]
    if to_target:
        return a.is_move and inside(a.box, target) and inside(a.to_box, to_target)
    return inside(a.box, target) or (a.to_box is not None and inside(a.to_box, target))


def cost(kind, usage):
    pin, pout = PRICE[kind]
    return (usage.get("promptTokenCount", 0) * pin + (usage.get("candidatesTokenCount", 0)
                                                      + usage.get("thoughtsTokenCount", 0)) * pout) / 1e6


async def main():
    s = scenes()
    imgs = {}
    for name, q, html, *_ in s:
        png = OUT / f"{name}.png"
        if not png.exists():
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / "p.html"
                src.write_text(design_tools.page_html(html, W, H), encoding="utf-8")
                await design_tools.render(str(src), str(png), W, H)
        imgs[name] = jpeg_b64(png)
    if "--render" in sys.argv:
        sheet = Image.new("RGB", (4 * 400, 4 * 245), "white")
        for i, (name, *_r) in enumerate(s):
            im = Image.open(OUT / f"{name}.png").convert("RGB")
            im.thumbnail((396, 225))
            sheet.paste(im, ((i % 4) * 400, (i // 4) * 245))
        sheet.save(OUT / "sheet.png")
        print("rendered", len(s))
        return
    results = {}
    for sc in s:
        name, q = sc[0], sc[1]
        t = time.monotonic()
        look = await gemini_vision._ask(q, imgs[name], deep=False, context="", timeout=60)
        results[name] = {"fast": look, "fast_s": time.monotonic() - t}
    need = [sc for sc in s if not results[sc[0]]["fast"] or not results[sc[0]]["fast"].sure
            or not score(sc, results[sc[0]]["fast"])]
    rest = [sc for sc in s if sc not in need]
    deep_set = (need + rest)[:10]
    for sc in deep_set:
        name, q = sc[0], sc[1]
        t = time.monotonic()
        results[name]["deep"] = await gemini_vision._ask(q, imgs[name], deep=True, context="", timeout=60)
        results[name]["deep_s"] = time.monotonic() - t
    rows, totals = [], {"fast": 0.0, "deep": 0.0}
    old_ok = new_ok = 0
    old_cost = new_cost = 0.0
    old_t, new_t = [], []
    for sc in s:
        name, q = sc[0], sc[1]
        r = results[name]
        f, d = r["fast"], r.get("deep")
        f_ok, d_ok = score(sc, f), (score(sc, d) if d is not None else None)
        f_cost = cost("fast", f.usage) if f else 0
        d_cost = cost("deep", d.usage) if d else None
        totals["fast"] += f_cost
        totals["deep"] += d_cost or 0
        escalate = (not f) or (not f.sure) or (gemini_vision.is_next_action(q) and not f.actions)
        new_this_ok = (d_ok if escalate and d is not None else f_ok)
        new_this_cost = f_cost + ((d_cost or 0) if escalate else 0)
        new_this_t = r["fast_s"] + (r.get("deep_s", 0) if escalate else 0)
        went_deep_before = gemini_vision.wants_deep(q)
        if went_deep_before and d is not None:
            old_this_ok, old_this_cost, old_this_t = d_ok, d_cost, r["deep_s"]
        else:
            old_this_ok, old_this_cost, old_this_t = f_ok, f_cost, r["fast_s"]
        old_ok += bool(old_this_ok)
        new_ok += bool(new_this_ok)
        old_cost += old_this_cost or 0
        new_cost += new_this_cost
        old_t.append(old_this_t)
        new_t.append(new_this_t)
        rows.append(f"{name:13} fast {'HIT ' if f_ok else 'miss'} sure={f.sure if f else '-'!s:5} {r['fast_s']:4.1f}s "
                    f"${f_cost:.4f} | deep {('HIT ' if d_ok else 'miss') if d is not None else ' -  '} "
                    f"{r.get('deep_s', 0):4.1f}s ${(d_cost or 0):.4f} | now {'deep' if escalate else 'fast'} "
                    f"| before {'deep' if went_deep_before else 'fast'} | {(f.answer if f else '')[:60]!r}")
    print("\n".join(rows))
    med = lambda xs: sorted(xs)[len(xs) // 2]  # noqa: E731
    print(f"\nright thing drawn: before {old_ok}/16, now {new_ok}/16")
    print(f"cost per answer: before ${old_cost / 16:.4f}, now ${new_cost / 16:.4f}")
    print(f"median time to the answer: before {med(old_t):.1f}s, now {med(new_t):.1f}s")
    print(f"this run cost: fast ${totals['fast']:.3f} + deep ${totals['deep']:.3f} = ${totals['fast'] + totals['deep']:.3f}")
    (OUT / "results.json").write_text(json.dumps({k: {"fast": (v["fast"].answer if v["fast"] else None),
                                                       "deep": (v["deep"].answer if v.get("deep") else None)}
                                                   for k, v in results.items()}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
