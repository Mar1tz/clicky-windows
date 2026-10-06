"""Design tools: an agent makes a picture - a poster, a social post, an ad, a
banner - by designing it as one HTML page and having it rendered.

The agents think with Claude (through OmniRoute), and Claude does not paint
pixels; it lays things out. That suits most of what people ask for: the
words on a poster spelled right and sharp, a brand's exact colours, the
exact size Instagram wants, and a source file that can be changed later.
render_design turns the page into a PNG, a JPEG or a PDF for print with the
Edge every Windows PC has - headless, in a throwaway profile, no window - and
hands the model a preview of what came out, so it sees its own work and can
fix it (workers show a tool's `image_b64` to the model).

Pillow (already shipped for screenshots) makes the preview, the JPEG and the
blank-page check. Nothing here needs a network; a design that links a web
font or picture gets it if the PC is online.
"""
from __future__ import annotations

import base64
import io
import os
import re
import tempfile
from typing import Any, Optional

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec
from .process import run_process

CATEGORY = "design"
RENDER_TIMEOUT = 60.0
PREVIEW_MAX = 1024                      # px, the long side of what the model is shown
MIN_SIDE, MAX_SIDE = 16, 4096

# Named sizes, in CSS pixels. Print sizes are at 96 dpi, the browser's own,
# and print to PDF on the real paper size.
SIZES: dict[str, tuple[int, int, str]] = {
    "instagram-post": (1080, 1080, "Instagram or Facebook square post"),
    "instagram-portrait": (1080, 1350, "Instagram portrait post, 4:5 - the most room in the feed"),
    "story": (1080, 1920, "Instagram, Facebook or TikTok story, reel or short cover, 9:16"),
    "facebook-post": (1200, 630, "Facebook link or event image, 1.91:1"),
    "linkedin-post": (1200, 627, "LinkedIn post image, 1.91:1"),
    "x-post": (1600, 900, "X (Twitter) image, 16:9"),
    "pinterest-pin": (1000, 1500, "Pinterest pin, 2:3"),
    "youtube-thumbnail": (1280, 720, "YouTube thumbnail, 16:9"),
    "email-header": (1200, 400, "email header banner (shown 600 wide)"),
    "website-banner": (1920, 600, "website hero banner"),
    "display-300x250": (300, 250, "display ad, medium rectangle"),
    "display-728x90": (728, 90, "display ad, leaderboard"),
    "display-160x600": (160, 600, "display ad, wide skyscraper"),
    "a5-flyer": (559, 794, "A5 flyer"),
    "a4-poster": (794, 1123, "A4 poster or flyer"),
    "a3-poster": (1123, 1587, "A3 poster"),
    "letter-flyer": (816, 1056, "US Letter flyer"),
}
# Print sizes laid out in millimetres, so a PDF is exactly one sheet of that
# paper (794 px is 210.08 mm - half a pixel over A4 spills onto a second page).
PAGE_MM = {"a5-flyer": (148, 210), "a4-poster": (210, 297), "a3-poster": (297, 420),
           "letter-flyer": (215.9, 279.4)}
MAX_PIXELS_SIDE = 8192                  # width or height x scale

_FORMATS = (".png", ".jpg", ".jpeg", ".pdf")
_LOCAL = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']|url\(\s*["']?([^"')]+)["']?\s*\)""", re.I)


# -- the browser ---------------------------------------------------------------------------------------------

_BROWSERS = (
    ("msedge.exe", (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
                    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")),
    ("chrome.exe", (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                    r"%LocalAppData%\Google\Chrome\Application\chrome.exe")),
)


def find_browser() -> Optional[str]:
    """Edge, else Chrome: TORA_RENDER_BROWSER, the App Paths registration,
    then where they install. None when neither is there."""
    given = os.environ.get("TORA_RENDER_BROWSER", "").strip()
    if given and os.path.isfile(given):
        return given
    for exe, fallbacks in _BROWSERS:
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                        path = winreg.QueryValueEx(k, "")[0]
                    if path and os.path.isfile(path):
                        return path
                except OSError:
                    continue
        except ImportError:                    # not Windows
            pass
        for p in fallbacks:
            path = os.path.expandvars(p)
            if os.path.isfile(path):
                return path
    return None


def browser_args(browser: str, profile: str) -> list[str]:
    """Headless, in its own profile - so a running Edge is never touched and
    never handed the job - with no extensions, first-run pages or GPU."""
    return [browser, "--headless=new", f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions", "--disable-gpu", "--hide-scrollbars",
            "--mute-audio", "--virtual-time-budget=4000"]


# -- the page ------------------------------------------------------------------------------------------------

def page_html(html: str, width: int, height: int, mm: Optional[tuple[float, float]] = None) -> str:
    """The design as a whole page of exactly width x height (or a sheet of
    paper, in mm): a fragment or an SVG is wrapped; the size, a zero margin,
    the PDF's page size and printed backgrounds go first in <head>, so the
    design's own CSS wins."""
    w, h = (f"{mm[0]:g}mm", f"{mm[1]:g}mm") if mm else (f"{width}px", f"{height}px")
    base = (f"<style>html,body{{margin:0;padding:0;width:{w};height:{h};overflow:hidden}}"
            f"@page{{size:{w} {h};margin:0}}"
            "*{-webkit-print-color-adjust:exact;print-color-adjust:exact;box-sizing:border-box}</style>")
    text = (html or "").strip()
    if not re.search(r"<html[\s>]", text, re.I):
        return f'<!doctype html><html><head><meta charset="utf-8">{base}</head><body>{text}</body></html>'
    if re.search(r"<head[\s>]", text, re.I):
        return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + '<meta charset="utf-8">' + base, text,
                      count=1, flags=re.I)
    return re.sub(r"(<html[^>]*>)", lambda m: m.group(1) + f'<head><meta charset="utf-8">{base}</head>', text,
                  count=1, flags=re.I)


def outside_files(html: str, folder: str, inside) -> list[str]:
    """Local files the page points at that are outside the workspace. Web
    addresses and data: URIs are fine; a relative path is read from the
    design's own folder."""
    bad = []
    for m in _LOCAL.finditer(html or ""):
        ref = (m.group(1) or m.group(2) or "").strip()
        low = ref.lower()
        if not ref or low.startswith(("http:", "https:", "data:", "#", "mailto:", "tel:", "about:")):
            continue
        if low.startswith("file:"):
            ref = re.sub(r"^file:/*", "", ref, flags=re.I).replace("/", os.sep)
        path = ref if os.path.isabs(ref) or re.match(r"^[a-z]:[\\/]", ref, re.I) else os.path.join(folder, ref)
        if not inside(path):
            bad.append(m.group(1) or m.group(2))
    return bad


# -- rendering ------------------------------------------------------------------------------------------------

class RenderError(Exception):
    pass


def _pil():
    try:
        from PIL import Image
        return Image
    except ImportError:
        raise RenderError("this needs the Pillow package") from None


def look(png_path: str) -> dict[str, Any]:
    """Size, a blank-page check and a small JPEG preview of a rendered PNG."""
    image = _pil()
    with image.open(png_path) as im:
        rgb = im.convert("RGB")
        size = rgb.size
        blank = all(lo == hi for lo, hi in rgb.getextrema())
        rgb.thumbnail((PREVIEW_MAX, PREVIEW_MAX))
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=80)
    return {"width": size[0], "height": size[1], "blank": blank,
            "preview_b64": base64.b64encode(buf.getvalue()).decode("ascii")}


async def render(html_path: str, out: str, width: int, height: int, *, scale: int = 1, pdf: bool = False,
                 browser: Optional[str] = None, timeout: float = RENDER_TIMEOUT) -> dict[str, Any]:
    """Render an HTML file to `out` (.png/.jpg/.jpeg/.pdf). A PDF also gets a
    PNG look for the preview. Returns look() of the picture plus the path.
    Raises RenderError with a reason the model can act on."""
    browser = browser or find_browser()
    if not browser:
        raise RenderError("no Edge or Chrome on this PC to render the design with")
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    folder = os.path.dirname(os.path.abspath(out))
    with tempfile.TemporaryDirectory(prefix="tora-render-") as tmp:
        shot = out if out.lower().endswith(".png") else os.path.join(tmp, "shot.png")
        args = browser_args(browser, os.path.join(tmp, "profile")) + [
            f"--window-size={width},{height}", f"--force-device-scale-factor={max(1, min(3, int(scale)))}",
            f"--screenshot={shot}", url]
        done = await run_process(args, cwd=folder, timeout=timeout)
        if not os.path.isfile(shot):
            raise RenderError(f"the browser made no picture ({(done.stderr or done.stdout).strip()[-300:]})")
        seen = look(shot)
        if out.lower().endswith((".jpg", ".jpeg")):
            image = _pil()
            with image.open(shot) as im:
                im.convert("RGB").save(out, "JPEG", quality=92)
        elif pdf:
            done = await run_process(browser_args(browser, os.path.join(tmp, "profile")) + [
                "--no-pdf-header-footer", f"--print-to-pdf={out}", url], cwd=folder, timeout=timeout)
            if not os.path.isfile(out):
                raise RenderError(f"the browser made no PDF ({(done.stderr or done.stdout).strip()[-300:]})")
    return {**seen, "path": out}


# -- the tool -------------------------------------------------------------------------------------------------

class RenderDesign(Tool):
    """HTML in, picture out - with the model shown what it made."""

    def __init__(self) -> None:
        # Kept short: a tool's schema is re-sent on every call the agent makes.
        self.spec = make_spec(
            "render_design",
            "Make a picture - poster, flyer, social post, ad, banner, thumbnail - by designing ONE HTML page "
            "(inline CSS/SVG, no scripts, under ~10 KB) at the exact size, rendered to PNG, JPG or PDF (print). "
            "Big type, strong contrast, margins, every word right. Local files by relative path; fonts and "
            "images by URL. You get a preview: redo only for a real problem (cut off, overlapping, unreadable, a "
            "wrong word or date), at most two more tries.",
            risk=RiskLevel.LOW, category=CATEGORY,
            properties={
                "html": {"type": "string", "description": "A whole page, a <body> fragment or an <svg>."},
                "path": {"type": "string", "description": "Workspace path ending .png, .jpg or .pdf."},
                "size": {"type": "string", "description": ", ".join(f"{k} {w}x{h}" for k, (w, h, _) in SIZES.items())
                         + " (print sizes make a one-sheet PDF)"},
                "width": {"type": "integer"}, "height": {"type": "integer"},
                "scale": {"type": "integer", "description": "1-3 pixels per px; 2 for print."},
            },
            required=["html", "path"],
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = str(args.get("path") or "")
        if not path.lower().endswith(_FORMATS):
            return self.fail("path must end in .png, .jpg or .pdf")
        target, err = self.guard(ctx, path)
        if err:
            return err
        size = str(args.get("size") or "")
        if size and size not in SIZES:
            return self.fail(f"unknown size {size!r}; one of: {', '.join(SIZES)}")
        width, height = (SIZES[size][:2] if size else (args.get("width"), args.get("height")))
        try:
            width, height = int(width), int(height)
        except (TypeError, ValueError):
            return self.fail("give a size, or width and height in px")
        if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
            return self.fail(f"width and height must be {MIN_SIDE}-{MAX_SIDE} px")
        pdf = target.lower().endswith(".pdf")
        scale = int(args.get("scale") or (2 if size in PAGE_MM and not pdf else 1))
        try:
            out = await render_design(str(args.get("html") or ""), target, width, height, scale=scale,
                                      mm=PAGE_MM.get(size), inside=ctx.is_inside_workspace)
        except RenderError as exc:
            return self.fail(str(exc))
        rel = os.path.relpath(out["path"], ctx.workspace)
        return self.ok(f"Rendered {rel} ({out['width']}x{out['height']} px). Source: "
                       f"{os.path.relpath(out['source'], ctx.workspace)}. The preview is attached - check it.",
                       path=out["path"], paths=[out["path"]], source=out["source"], width=out["width"],
                       height=out["height"],
                       image_b64=out["preview_b64"], image_mime="image/jpeg")


async def render_design(html: str, target: str, width: int, height: int, *, scale: int = 1,
                        mm: Optional[tuple[float, float]] = None, inside=None) -> dict[str, Any]:
    """Save the design's HTML beside `target` and render it there. The one
    entry point for anything that makes a picture (render_design).
    `inside(path)` - when given - must accept every local file the page uses."""
    if not (html or "").strip():
        raise RenderError("the design is empty - give the HTML")
    scale = max(1, min(3, int(scale or 1), MAX_PIXELS_SIDE // max(width, height)))
    if re.search(r"<script[\s>]", html, re.I):
        raise RenderError("no scripts in a design: lay it out with HTML, CSS and SVG")
    folder = os.path.dirname(target) or "."
    if inside is not None:
        bad = outside_files(html, folder, inside)
        if bad:
            raise RenderError(f"the design uses files outside the workspace: {', '.join(bad[:3])} - copy them in "
                              "and use a relative path")
    os.makedirs(folder, exist_ok=True)
    source = os.path.splitext(target)[0] + ".html"
    with open(source, "w", encoding="utf-8") as fh:
        fh.write(page_html(html, width, height, mm))
    out = await render(source, target, width, height, scale=scale, pdf=target.lower().endswith(".pdf"))
    if out["blank"]:
        raise RenderError("the design came out as one flat colour - nothing showed. Check the HTML: text the "
                          "same colour as the background, or everything outside the page")
    return {**out, "source": source}


def tools() -> list[Tool]:
    return [RenderDesign()]
