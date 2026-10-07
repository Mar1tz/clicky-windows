"""
Write assets/icon.ico - HeyTora's icon for the .exe (the desktop shortcut, the
Start menu, Explorer, the installer, Apps) - from the brand mark,
assets/brand/heytora-icon.svg, at every size Windows asks for (ui/app_icon.py).

build.bat runs it before every build:  python assets\\make_icon.py
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")          # no window: it only draws
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUT = Path(__file__).parent / "icon.ico"


def main():
    from PyQt6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])   # noqa: F841 - Qt needs one to draw
    from ui.app_icon import write_ico
    sizes = write_ico(OUT)
    print(f"Wrote {OUT} ({len(sizes)} sizes: {', '.join(map(str, sizes))})")


if __name__ == "__main__":
    main()
