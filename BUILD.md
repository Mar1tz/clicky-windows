# Building Tora.exe for Windows

Two ways to package Tora for your friend's laptop:

| Method | Output | Size | Install | Best for |
|---|---|---|---|---|
| **Portable folder** | `dist\Tora\` | ~400-600 MB | Copy the folder, double-click `Tora.exe` | Quick testing, USB stick |
| **Setup installer** | `Setup-Tora.exe` | ~200-400 MB | Double-click, Next, Finish | Polished distribution |

---

## Quick build (portable folder)

```bat
build.bat
```

That's it. After 2-5 minutes:
```
dist\Tora\Tora.exe    â† hand this whole folder to your friend
```

Your friend:
1. Copies the `Tora` folder anywhere on their PC
2. (Optional) creates `.env` next to `Tora.exe` with their API keys â€” see `.env.example`
3. Double-clicks `Tora.exe`
4. Tray icon appears, Tora is running

**No Python needed on their machine.** Everything is bundled.

---

## Full installer (`Setup-Tora.exe`)

1. Install [Inno Setup 6](https://jrsoftware.org/isdl.php) (free)
2. Run:
   ```bat
   build.bat installer
   ```
3. Output: `dist\Setup-Tora.exe` â€” a single self-extracting installer

Your friend runs `Setup-Tora.exe`:
- Pick install location (default: `C:\Program Files\Tora`)
- Choose: desktop shortcut? launch on Windows startup?
- Next â†’ Install â†’ Finish
- Uninstall works through Windows Settings like any other app

---

## What gets bundled

| Component | Bundled? | Notes |
|---|---|---|
| Python runtime | âœ… | Embedded â€” no install needed |
| PyQt6 | âœ… | UI framework |
| httpx + websockets | ✅ | The whole Gemini transport — no vendor SDK |
| uiautomation + RapidOCR + onnxruntime | ✅ | Local pointing tiers |
| OpenCV (headless) | ✅ | Figure detection for teaching drawings |
| Your `.env` | âŒ | **Must be added post-install** (security) |

---

## First-run checklist for your friend

When they launch `Tora.exe` the first time:

1. **Windows SmartScreen warning** (blue popup)
   - Click "More info" â†’ "Run anyway"
   - This is normal for unsigned .exe files. To fix permanently, code-sign with a certificate (~$100/year).

2. **Microphone permission** (Windows pops up)
   - Click "Yes" â€” needed for voice input

3. **Tray icon** appears bottom-right
   - Right-click it â†’ see the menu
   - If no tray icon, check that the process is running in Task Manager

4. **Test it**:
   - Hold `Ctrl + Win`, say "what's on my screen"
   - If silent â†’ the first-run window asks for a Google API key and checks it; re-run it from Tray â†’ Setup again

---

## Troubleshooting the build

**`pyinstaller: command not found`**
```bat
pip install pyinstaller
```

**`ImportError: DLL load failed` at runtime on friend's PC**
- Friend needs [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe) (usually already installed on Windows 10/11)

**Antivirus flags Tora.exe**
- False positive â€” PyInstaller bundles are sometimes flagged because they self-extract
- Add an exclusion for the install folder, or code-sign the binary

**Build is large**
- Most of it is `onnxruntime` + the RapidOCR models (OCR pointing tier) and OpenCV
- For the smallest build, drop `rapidocr-onnxruntime` and `opencv-python-headless` from `requirements.txt` â€” pointing then goes UIA â†’ vision model, skipping OCR, and figure detection is off

**Build is slow (5+ min)**
- Normal first time â€” PyInstaller analyses every import
- Subsequent builds are faster if you don't `rmdir /s /q build`

---

## Signing the installer (optional)

To avoid the Windows SmartScreen warning, you need an **Authenticode code-signing certificate**:

1. Buy one from Sectigo / DigiCert / SSL.com (~$80-400/year)
2. After `build.bat`, sign both files:
   ```bat
   signtool sign /f mycert.pfx /p PASSWORD /tr http://timestamp.digicert.com /td sha256 /fd sha256 dist\Tora\Tora.exe
   signtool sign /f mycert.pfx /p PASSWORD /tr http://timestamp.digicert.com /td sha256 /fd sha256 dist\Setup-Tora.exe
   ```

For testing with friends this is overkill â€” just tell them to click "Run anyway".

---

## Directory layout after build

```
tora-windows/
â”œâ”€â”€ build/              â† PyInstaller scratch (safe to delete)
â””â”€â”€ dist/
    â”œâ”€â”€ Tora/         â† portable folder â€” give this to friends
    â”‚   â”œâ”€â”€ Tora.exe
    â”‚   â”œâ”€â”€ _internal/  â† bundled Python + libs (~500 MB)
    â”‚   â”œâ”€â”€ .env.example
    â”‚   â”œâ”€â”€ LICENSE
    â”‚   â””â”€â”€ README.md
    â””â”€â”€ Setup-Tora.exe â† single-file installer (if you built with installer flag)
```

## Agent workforce notes

* `tora.spec` collects `agents/**` via `collect_submodules("agents")` — every
  package under `agents/` is imported lazily, so without this the built exe
  logs "agent workforce failed to start" and runs without agents.
* The SQLite databases (`agents.db`, `agent_memory.db`) and the accounts file
  live under `%LOCALAPPDATA%\Tora`, next to `tora.log`. The workspace defaults
  to `%USERPROFILE%\Documents\Tora Projects` and is created on first run.
* Playwright (browser worker) is **optional** and not bundled: `pip install
  playwright && playwright install chromium` on the target machine enables
  it. Without it the browser tools fail with that exact instruction and
  everything else keeps working.
* Computer control needs `pynput` (already a requirement) and asks the user
  before every action regardless of settings — that floor is in code, not
  configuration.
