# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for HeyTora Windows.

Build:
    pyinstaller heytora.spec --clean --noconfirm

Output:
    dist/HeyTora/HeyTora.exe           ← distribute this whole folder

We use --onedir (not --onefile) because:
  • onnxruntime and OpenCV ship large native DLLs that onefile extracts to
    %TEMP% on every launch (slow, antivirus-triggering).
  • onedir launches in ~1s vs ~8s for onefile.
  • Inno Setup bundles the folder into a single Setup.exe anyway.
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# ── Modules that are lazy-imported by CompanionManager — PyInstaller's
#    static analysis misses them, so we list them explicitly.
hidden = [
    # The Gemini stack. Every one of these is imported inside a function so
    # the app starts fast, which means PyInstaller's static analysis never
    # sees them.
    "ai.gemini",
    "ai.gemini_live",
    "ai.gemini_provider",
    "ai.gemini_realtime",
    "ai.gemini_vision",
    "ai.gemini_planner",
    "ai.gemini_memory",
    "ai.model_registry",
    "ai.screen_summary",
    "ai.web_search",
    "ai.hybrid_pointer",
    "ai.figure_detector",

    # First-run setup wizard + the API key screen it opens (both imported
    # lazily inside functions, so they need to be named explicitly)
    "ui.setup_wizard",
    "ui.api_keys_dialog",

    # Speech, both directions
    "audio.stt.gemini_stt",
    "audio.tts.gemini_tts",

    # Log hygiene, imported inside _setup_logging
    "logsafe",

    # HeyTora accounts (sign-in, the server function's client) and the
    # per-monitor pixel maths - both imported inside functions.
    "account",
    "account.loopback",
    "account.service",
    "account.settings",
    "account.store",
    "screen.monitors",

    # Keeping itself up to date (imported inside main()).
    "updates",
    "updates.service",
    "version",
]

# ── The agent workforce. CompanionManager imports agents.bootstrap lazily
#    inside _start_agents(), and bootstrap imports every package lazily in
#    turn, so static analysis sees none of it. collect_submodules walks the
#    whole tree; a missing sub-package would surface as "agent workforce
#    failed to start" in tora.log with the app otherwise fine — which is
#    the designed failure mode, but not one to ship.
hidden += collect_submodules("agents")
# Industry extensions are discovered at runtime (pkgutil over the
# `extensions` package), so the same applies - plus each pack's
# manifest.json, which is data, not code.
hidden += collect_submodules("extensions")

# ── Heavy packages that ship non-Python assets (DLLs, JSON, voices).
#    collect_all grabs submodules + data files + binaries + metadata.
datas, binaries, hiddenimports = [], [], []
for pkg in (
    # Transport. There is no vendor SDK — Gemini is reached over plain REST
    # and websockets — so these are the whole networking stack.
    "httpx",
    "httpcore",
    "h2",                       # HTTP/2, which keeps the connection warm
    "websockets",               # the Live API
    "certifi",
    "tavily",
    # Pointing + drawing accuracy stack
    "uiautomation",             # UIA tree walker (needs its bundled DLL)
    "rapidocr_onnxruntime",     # offline OCR — ships ONNX models as data
    "onnxruntime",
    "cv2",                      # figure detection for teaching drawings
    # Web search + misc runtime deps
    "ddgs",
    "imageio",
    "imageio_ffmpeg",
    "pypdf",
    "docx",
    "openpyxl",                 # spreadsheet tools (optional; .xlsx read/write keeping formulas)
    "langdetect",
    "pynput",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass  # package not installed — that path is optional anyway

hiddenimports += hidden
hiddenimports += collect_submodules("PyQt6")

import glob as _glob
for _manifest in _glob.glob(os.path.join("extensions", "*", "manifest.json")):
    datas.append((_manifest, os.path.dirname(_manifest)))

# The site's typeface and the wordmark (ui/tokens.py reads them from here).
datas.append((os.path.join("assets", "fonts"), os.path.join("assets", "fonts")))
datas.append((os.path.join("assets", "brand"), os.path.join("assets", "brand")))
datas.append((os.path.join("assets", "icons"), os.path.join("assets", "icons")))
# Tora's voices saying hello, for Make Tora yours (assets/voices/make_voices.py).
datas.append((os.path.join("assets", "voices"), os.path.join("assets", "voices")))


a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Shave size: HeyTora never uses these heavy libs.
    excludes=[
        "matplotlib", "scipy", "pandas", "tkinter",
        "notebook", "jupyter", "IPython",
        # collect_all("onnxruntime") takes its model-conversion tools too
        # (onnxruntime.transformers / .quantization), and those import the
        # whole Hugging Face stack: torch, transformers, accelerate, librosa
        # (numba, llvmlite, scikit-learn), faiss, nltk. RapidOCR only runs
        # models. Left in, the 30 Sep 2026 build was 1.34 GB instead of ~0.6.
        "torch", "transformers", "accelerate", "librosa", "numba", "llvmlite",
        "sklearn", "faiss", "nltk", "tensorflow", "keras",
        # Optional, as BUILD.md says: the browser tools tell the user to
        # `pip install playwright` when it is missing. It is imported inside
        # functions, which PyInstaller still follows - so it got bundled.
        "playwright",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HeyTora",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                    # UPX often triggers Windows Defender
    console=False,                # no terminal window for released builds
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico" if __import__("os").path.exists("assets/icon.ico") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HeyTora",
)
