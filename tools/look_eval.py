"""The look test bench (docs/SHOW_DONT_TELL_POLISH.md, SP-01).

Every change to a screen look - the model, its thinking, the image size, the
prompt, the routing words - is measured here before and after. 23 everyday
screens are built as HTML and rendered headless with Edge, so nothing opens on
screen and no real screen is ever sent. Each goes through gemini_vision.look()
exactly as the app calls it (fast first, deep when needed), and is scored:

    right thing drawn   the drawn box's centre on the target (a move: both ends)
    nothing drawn       for a decoy - the thing asked for is not there
    stays fast          for a question that must not go to the deep model

with the path taken (fast, fast→deep, deep), seconds, tokens and cost. A
report is saved as JSON; --against an earlier report prints what changed.

PAID: on the owner's own key (TORA_ACCESS=own_key), never the plan. About
$0.06 a run in the app's routing, $0.03 with --fast-only. --max-dollars stops
it (default $0.30).

    python tools/look_eval.py --render                 # the screens only, free (a contact sheet)
    python tools/look_eval.py                          # the app's routing
    python tools/look_eval.py --fast-only              # every screen on the fast model
    python tools/look_eval.py --deep-only --only chess,tictactoe
    python tools/look_eval.py --against <report.json>  # and what changed since that run
"""
import argparse
import asyncio
import base64
import hashlib
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

W, H = 1600, 900
OUT = Path(tempfile.gettempdir()) / "tora-look-eval"
OUT.mkdir(exist_ok=True)
# Screens scored by something other than where the drawing landed.
EXPECT = {"decoy": "nothing", "music": "fast"}


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
    return s + more_scenes()


def more_scenes():
    """The cases that break things (SP-01): a dark theme, look-alike icons, a
    tiny target, a dialog over a busy page, another language, a decoy where
    the thing is NOT there, and a question that must stay on the fast model."""
    s = []
    dark = "<body style='margin:0;font-family:Segoe UI;background:#0f172a;color:#e2e8f0;position:relative'>"
    html = dark + box(80, 40, 600, 50, "<b style='font-size:28px'>Projects</b>")
    for i in range(6):
        html += box(80, 140 + i * 110, 1000, 90, f"<span style='font-size:22px'>Project {i + 1}</span>",
                    "background:#1e293b;border-radius:10px;display:flex;align-items:center;padding-left:24px")
    html += box(1460, 40, 56, 56, "TM", "background:#334155;border-radius:50%;display:flex;align-items:center;"
                "justify-content:center;font-weight:700")
    html += box(1300, 110, 260, 156, "", "background:#1e293b;border:1px solid #334155;border-radius:10px")
    for i, item in enumerate(["Profile", "Settings", "Log out"]):
        html += box(1310, 120 + i * 48, 240, 44, item, "font-size:20px;display:flex;align-items:center;padding-left:14px")
    s.append(("darktheme", "where do I log out?", html, (1310, 216, 240, 44), None))

    icon = lambda x, svg: box(x, 120, 44, 44, f"<svg width='44' height='44' viewBox='0 0 44 44'>{svg}</svg>",  # noqa: E731
                              "border-radius:6px;background:#fff;border:1px solid #e2e8f0")
    html = BASE + box(150, 100, 1300, 84, "", "background:#f1f5f9;border-radius:10px")
    html += icon(200, "<text x='22' y='31' font-size='26' font-weight='900' text-anchor='middle' font-family='Georgia'>B</text>")
    html += icon(256, "<text x='22' y='31' font-size='26' font-style='italic' text-anchor='middle' font-family='Georgia'>I</text>")
    html += icon(312, "<text x='22' y='29' font-size='24' text-anchor='middle' font-family='Georgia'>U</text><line x1='12' y1='34' x2='32' y2='34' stroke='#000' stroke-width='2'/>")
    html += icon(368, "<text x='22' y='30' font-size='24' text-anchor='middle' font-family='Georgia'>S</text><line x1='10' y1='22' x2='34' y2='22' stroke='#000' stroke-width='2'/>")
    html += icon(424, "<circle cx='12' cy='14' r='2.5'/><line x1='18' y1='14' x2='34' y2='14' stroke='#000' stroke-width='2'/><circle cx='12' cy='22' r='2.5'/><line x1='18' y1='22' x2='34' y2='22' stroke='#000' stroke-width='2'/><circle cx='12' cy='30' r='2.5'/><line x1='18' y1='30' x2='34' y2='30' stroke='#000' stroke-width='2'/>")
    html += icon(480, "<path d='M18 26 L26 18 M15 22 l-3 3 a5 5 0 0 0 7 7 l3 -3 M29 22 l3 -3 a5 5 0 0 0 -7 -7 l-3 3' stroke='#000' fill='none' stroke-width='2'/>")
    html += box(150, 230, 1300, 560, "<p style='font-size:20px;line-height:1.6'>" + "Meeting notes. " * 60 + "</p>",
                "background:#fff;padding:30px;overflow:hidden")
    s.append(("toolbar", "which button makes text bold?", html, (200, 120, 44, 44), None))

    html = BASE + box(300, 120, 1000, 60, "<b style='font-size:30px'>Dashboard</b>")
    html += box(1170, 760, 400, 96, "<b>Update ready</b><br><span style='font-size:16px'>Restart to finish installing.</span>",
                "background:#1e293b;color:#fff;border-radius:10px;padding:16px 20px;font-size:18px")
    html += box(1540, 770, 16, 16, "<svg width='16' height='16'><path d='M3 3 L13 13 M13 3 L3 13' stroke='#cbd5e1' stroke-width='2'/></svg>")
    s.append(("tinyclose", "how do I close this notification?", html, (1540, 770, 16, 16), None))

    html = BASE + box(100, 60, 1400, 780, "<p style='font-size:18px;line-height:1.7;color:#334155'>"
                      + "Quarterly report draft - revenue, costs, outlook. " * 70 + "</p>", "overflow:hidden")
    html += box(0, 0, W, H, "", "background:#0004")
    html += box(500, 330, 600, 260, "", "background:#fff;border-radius:12px")
    html += box(540, 360, 520, 40, "<b style='font-size:24px'>Save changes to report.docx?</b>")
    html += box(540, 410, 520, 60, "<span style='font-size:18px'>Your changes will be lost if you don't save them.</span>")
    html += box(560, 520, 160, 44, "Save", BTN) + box(740, 520, 160, 44, "Don't save", GHOST)
    html += box(920, 520, 160, 44, "Cancel", GHOST)
    s.append(("savedialog", "what should I click to keep my changes?", html, (560, 520, 160, 44), None))

    s.append(("german", "where do I sign in?", BASE +
              box(600, 180, 400, 520, "", "background:#fff;border-radius:12px;box-shadow:0 4px 20px #0002") +
              box(640, 210, 320, 50, "<b style='font-size:30px'>Bei Acme anmelden</b>") +
              box(640, 300, 320, 44, "E-Mail", FIELD) + box(640, 370, 320, 44, "Passwort", FIELD) +
              box(640, 428, 320, 30, "<span style='color:#2563eb;font-size:16px'>Passwort vergessen?</span>") +
              box(640, 480, 320, 48, "Anmelden", BTN) +
              box(680, 560, 240, 30, "<span style='color:#2563eb;font-size:16px'>Konto erstellen</span>"),
              (640, 480, 320, 48), None))

    html = BASE + box(400, 120, 800, 60, "<b style='font-size:32px'>Settings</b>")
    for i, label in enumerate(["Language", "Time zone", "Notifications", "Privacy"]):
        html += box(400, 200 + i * 80, 800, 64, label, ROW)
    s.append(("decoy", "where's the export button?", html, None, None))

    html = BASE.replace("#f8fafc", "#121212") + box(60, 40, 400, 50, "<b style='font-size:28px;color:#fff'>Up next</b>")
    for i, (song, artist) in enumerate([("Blinding Lights", "The Weeknd"), ("Levitating", "Dua Lipa"),
                                        ("As It Was", "Harry Styles"), ("Flowers", "Miley Cyrus")]):
        html += box(60, 120 + i * 80, 900, 64, f"<b style='color:#fff;width:360px'>{song}</b><span style='color:#aaa'>{artist}</span>",
                    "display:flex;align-items:center;font-size:20px;border-bottom:1px solid #333")
    html += box(0, 800, W, 100, "", "background:#181818")
    html += box(760, 820, 60, 60, "<svg width='60' height='60'><circle cx='30' cy='30' r='28' fill='#fff'/><polygon points='24,18 44,30 24,42' fill='#000'/></svg>")
    s.append(("music", "what should I play next?", html, None, None))
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


def score(scene, look, path=""):
    """Right or wrong, the way the app would draw it."""
    name, q, _, target, to_target = scene
    expect = EXPECT.get(name)
    if expect == "nothing":                       # a decoy: say so, draw nothing
        return bool(look) and not look.actions
    if expect == "fast":                          # routing only
        return path.startswith("fast")
    if not look or not look.actions:
        return False
    acts = look.actions
    if gemini_vision.is_next_action(q):
        acts = ([a for a in acts if a.is_move] or acts)[:1]
    a = acts[0]
    if to_target:
        return a.is_move and inside(a.box, target) and inside(a.to_box, to_target)
    return inside(a.box, target) or (a.to_box is not None and inside(a.to_box, target))


async def render_all(s) -> dict:
    """Every screen as the JPEG the app would send; cached by its HTML."""
    imgs = {}
    for name, q, html, *_ in s:
        png = OUT / f"{name}-{hashlib.sha1(html.encode()).hexdigest()[:8]}.png"
        if not png.exists():
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / "p.html"
                src.write_text(design_tools.page_html(html, W, H), encoding="utf-8")
                await design_tools.render(str(src), str(png), W, H)
        imgs[name] = (png, jpeg_b64(png))
    return imgs


def contact_sheet(s, imgs) -> Path:
    cols, cw, chh = 4, 400, 245
    sheet = Image.new("RGB", (cols * cw, ((len(s) + cols - 1) // cols) * chh), "white")
    for i, (name, *_r) in enumerate(s):
        im = Image.open(imgs[name][0]).convert("RGB")
        im.thumbnail((cw - 4, chh - 20))
        sheet.paste(im, ((i % cols) * cw, (i // cols) * chh))
    path = OUT / "sheet.png"
    sheet.save(path)
    return path


def compare(before: dict, after: dict) -> None:
    """What changed between two reports, screen by screen and in sum."""
    b = {r["name"]: r for r in before["rows"]}
    for r in after["rows"]:
        o = b.get(r["name"])
        if not o:
            continue
        marks = []
        if o["ok"] != r["ok"]:
            marks.append("now RIGHT" if r["ok"] else "now WRONG")
        if o["path"] != r["path"]:
            marks.append(f"{o['path']} -> {r['path']}")
        if marks:
            print(f"  {r['name']:12} {', '.join(marks)}")
    for k in ("right", "p50_s", "avg_cost", "thinking"):
        print(f"  {k:9} {before['summary'].get(k)} -> {after['summary'].get(k)}")


async def main():
    ap = argparse.ArgumentParser(description="The look test bench (SP-01).")
    ap.add_argument("--render", action="store_true", help="build the screens only, free")
    ap.add_argument("--fast-only", action="store_true")
    ap.add_argument("--deep-only", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated screen names")
    ap.add_argument("--max-dollars", type=float, default=0.30)
    ap.add_argument("--against", default="", help="an earlier report.json to compare with")
    args = ap.parse_args()

    s = scenes()
    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        s = [sc for sc in s if sc[0] in wanted]
    imgs = await render_all(s)
    if args.render:
        print("rendered", len(s), "-", contact_sheet(s, imgs))
        return
    assert gemini.own_key() and gemini.api_key(), "no own key in .env (GOOGLE_API_KEY)"
    deep = False if args.fast_only else True if args.deep_only else None
    mode = "fast-only" if args.fast_only else "deep-only" if args.deep_only else "app routing"
    rows, spent = [], 0.0
    for sc in s:
        if spent >= args.max_dollars:
            print(f"stopped: ${spent:.3f} reached the ${args.max_dollars:.2f} cap")
            break
        name, q = sc[0], sc[1]
        t0 = time.monotonic()
        look = await gemini_vision.look(q, imgs[name][1], deep=deep)
        took = time.monotonic() - t0
        u = look.usage or {}
        cost = float(look.cost or 0.0)
        spent += cost
        ok = score(sc, look, look.path or "")
        rows.append({"name": name, "ok": bool(ok), "path": look.path or "-", "s": round(took, 2),
                     "cost": round(cost, 5), "in": u.get("promptTokenCount", 0),
                     "out": u.get("candidatesTokenCount", 0), "thinking": u.get("thoughtsTokenCount", 0),
                     "answer": look.answer})
        r = rows[-1]
        print(f"{name:12} {'RIGHT' if ok else 'WRONG'} {r['path']:18} {took:4.1f}s ${cost:.4f} "
              f"{r['in']:5} in {r['out']:4} out {r['thinking']:4} think | {look.answer[:60]!r}")
    if not rows:
        return
    times = sorted(r["s"] for r in rows)
    paths: dict = {}
    for r in rows:
        paths[r["path"]] = paths.get(r["path"], 0) + 1
    summary = {"right": f"{sum(r['ok'] for r in rows)}/{len(rows)}", "p50_s": times[len(times) // 2],
               "avg_cost": round(spent / len(rows), 5), "total_cost": round(spent, 4),
               "thinking": sum(r["thinking"] for r in rows), "paths": paths, "mode": mode}
    print(f"\n{mode}: right {summary['right']}, p50 {summary['p50_s']}s, avg ${summary['avg_cost']:.4f}, "
          f"total ${spent:.3f}, thinking tokens {summary['thinking']}, paths {paths}")
    report = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "fast": gemini.Models.VISION_FAST,
              "deep": gemini.Models.VISION_DEEP, "rows": rows, "summary": summary}
    path = OUT / f"report-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("report:", path)
    if args.against:
        print("\nsince", args.against)
        compare(json.loads(Path(args.against).read_text(encoding="utf-8")), report)


if __name__ == "__main__":
    asyncio.run(main())
