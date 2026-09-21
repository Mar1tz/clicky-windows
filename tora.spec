# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Tora Windows.

Build:
    pyinstaller tora.spec --clean --noconfirm

Output:
    dist/Tora/Tora.exe           ← distribute this whole folder

We use --onedir (not --onefile) because:
  • onnxruntime and OpenCV ship large native DLLs that onefile extracts to
    %TEMP% on every launch (slow, antivirus-triggering).
  • onedir launches in ~1s vs ~8s for onefile.
  • Inno Setup bundles the folder into a single Setup.exe anyway.
"""

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
]

# ── The agent workforce. CompanionManager imports agents.bootstrap lazily
#    inside _start_agents(), and bootstrap imports every package lazily in
#    turn, so static analysis sees none of it. collect_submodules walks the
#    whole tree; a missing sub-package would surface as "agent workforce
#    failed to start" in tora.log with the app otherwise fine — which is
#    the designed failure mode, but not one to ship.
hidden += collect_submodules("agents")

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


a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Shave size: Tora never uses these heavy libs.
    excludes=[
        "matplotlib", "scipy", "pandas", "tkinter",
        "notebook", "jupyter", "IPython",
        "torch.distributions", "torch.onnx",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Tora",
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
    name="Tora",
)
