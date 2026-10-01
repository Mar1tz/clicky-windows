# Building HeyTora.exe for Windows

Two ways to package HeyTora for your friend's laptop:

| Method | Output | Size | Install | Best for |
|---|---|---|---|---|
| **Portable folder** | `dist\HeyTora\` | ~640 MB (a zip of it is smaller) | Unzip, double-click `HeyTora.exe` | Quick testing, USB stick |
| **Setup installer** | `Setup-HeyTora.exe` | ~200-300 MB | Double-click, Next, Finish | Polished distribution |

---

## Quick build (portable folder)

```bat
build.bat
```

That's it. After 2-5 minutes:
```
dist\HeyTora\HeyTora.exe    ← hand this whole folder to your friend
```

Your friend:
1. Copies the `HeyTora` folder anywhere on their PC
2. (Optional) creates `.env` next to `HeyTora.exe` with their API keys — see `.env.example`
3. Double-clicks `HeyTora.exe`
4. Tray icon appears, HeyTora is running

**No Python needed on their machine.** Everything is bundled.

---

## Full installer (`Setup-HeyTora.exe`)

1. Install [Inno Setup 6](https://jrsoftware.org/isdl.php) (free) - or, with
   no admin rights needed:
   `winget install --id JRSoftware.InnoSetup -e --scope user`
2. Run:
   ```bat
   build.bat installer
   ```
   (or, after a PyInstaller build: `"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss`)
3. Output: `dist\Setup-HeyTora.exe` — a single self-extracting installer
   (~190 MB; version from `MyAppVersion` in `installer.iss`)

Your friend runs `Setup-HeyTora.exe`:
- No admin rights needed: it installs for them, in
  `%LOCALAPPDATA%\Programs\HeyTora` (it offers "all users" too, which asks
  for admin)
- Choose: desktop shortcut? launch when Windows starts?
- Next → Install → Finish, with "Launch HeyTora" ticked
- Start menu: HeyTora, and Uninstall HeyTora; it is listed in Settings →
  Apps like any other app
- Installing a newer one over it closes the running HeyTora first
- "Launch when Windows starts" is the same switch as Settings → "Start
  HeyTora when Windows starts" (one registry entry, so never two copies);
  uninstalling removes it only if it starts that installed copy
- Uninstalling leaves the user's data (`%LOCALAPPDATA%\HeyTora`: logs,
  history, agent databases) and their `.env` keys behind

---

## What gets bundled

| Component | Bundled? | Notes |
|---|---|---|
| Python runtime | ✅ | Embedded — no install needed |
| PyQt6 | ✅ | UI framework |
| httpx + websockets | ✅ | The whole Gemini transport — no vendor SDK |
| uiautomation + RapidOCR + onnxruntime | ✅ | Local pointing tiers |
| OpenCV (headless) | ✅ | Figure detection for teaching drawings |
| Your `.env` | ❌ | **Must be added post-install** (security) |
| torch, transformers, numba, faiss… | ❌ | Excluded in `heytora.spec`: onnxruntime's conversion tools drag them in, HeyTora never uses them (1.34 GB → 0.64 GB) |
| Playwright | ❌ | Excluded; browser tasks say so and everything else works |

**Keys never ship.** The built app reads only a `.env` / `.env.local` next to
`HeyTora.exe`, and `build.bat` copies `.env.example` (empty keys), never
`.env`. It also accepts `GOOGLE_API_KEY` / `GEMINI_API_KEY` from Windows'
own environment variables - so on a PC where one is set (the developer's),
a test run of the exe uses it without asking. To see what a tester sees,
try it on another PC or a Windows account without that variable.

---

## First-run checklist for your friend

When they launch `HeyTora.exe` the first time:

1. **Windows SmartScreen warning** (blue popup)
   - Click "More info" → "Run anyway"
   - This is normal for unsigned .exe files. To fix permanently, code-sign with a certificate (~$100/year).

2. **Microphone permission** (Windows pops up)
   - Click "Yes" — needed for voice input

3. **Tray icon** appears bottom-right
   - Right-click it → see the menu
   - If no tray icon, check that the process is running in Task Manager

4. **Test it**:
   - Hold `Ctrl + Win`, say "what's on my screen"
   - If silent → the first-run window asks for a Google API key and checks it; re-run it from Tray → Setup again

---

## Troubleshooting the build

**`pyinstaller: command not found`**
```bat
pip install pyinstaller
```

**`ImportError: DLL load failed` at runtime on friend's PC**
- Friend needs [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe) (usually already installed on Windows 10/11)

**Antivirus flags HeyTora.exe**
- False positive — PyInstaller bundles are sometimes flagged because they self-extract
- Add an exclusion for the install folder, or code-sign the binary

**Build is large**
- Most of it is `onnxruntime` + the RapidOCR models (OCR pointing tier) and OpenCV
- For the smallest build, drop `rapidocr-onnxruntime` and `opencv-python-headless` from `requirements.txt` — pointing then goes UIA → vision model, skipping OCR, and figure detection is off

**Build is slow (5+ min)**
- Normal first time — PyInstaller analyses every import
- Subsequent builds are faster if you don't `rmdir /s /q build`

---

## Signing the installer (optional)

To avoid the Windows SmartScreen warning, you need an **Authenticode code-signing certificate**:

1. Buy one from Sectigo / DigiCert / SSL.com (~$80-400/year)
2. After `build.bat`, sign both files:
   ```bat
   signtool sign /f mycert.pfx /p PASSWORD /tr http://timestamp.digicert.com /td sha256 /fd sha256 dist\HeyTora\HeyTora.exe
   signtool sign /f mycert.pfx /p PASSWORD /tr http://timestamp.digicert.com /td sha256 /fd sha256 dist\Setup-HeyTora.exe
   ```

For testing with friends this is overkill — just tell them to click "Run anyway".

---

## Directory layout after build

```
heytora/
├── build/              ← PyInstaller scratch (safe to delete)
└── dist/
    ├── HeyTora/         ← portable folder — give this to friends
    │   ├── HeyTora.exe
    │   ├── _internal/  ← bundled Python + libs (~500 MB)
    │   ├── .env.example
    │   ├── LICENSE
    │   └── README.md
    └── Setup-HeyTora.exe ← single-file installer (if you built with installer flag)
```

## Agent workforce notes

* `heytora.spec` collects `agents/**` via `collect_submodules("agents")` — every
  package under `agents/` is imported lazily, so without this the built exe
  logs "agent workforce failed to start" and runs without agents.
* The SQLite databases (`agents.db`, `agent_memory.db`) and the accounts file
  live under `%LOCALAPPDATA%\HeyTora`, next to `tora.log`. The workspace defaults
  to `%USERPROFILE%\Documents\Tora Projects` and is created on first run.
* Playwright (browser worker) is **optional** and not bundled (it is
  excluded in `heytora.spec`, and it needs its own Chromium download
  anyway). In the built app the browser tools say it is missing and
  everything else keeps working; `pip install playwright` on the target
  machine does NOT help a built app - it only enables them when running
  from source.
* Computer control needs `pynput` (already a requirement) and asks the user
  before every action regardless of settings — that floor is in code, not
  configuration.
