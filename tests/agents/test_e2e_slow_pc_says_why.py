"""FS-09 (docs/FAST_AND_RIGHT.md): a slow PC says why in its log.

"On some people's PCs it's very slow" came with nothing to go on. tora.log
now opens each session with one line about the machine, and each look's
line carries its capture time (FS-05) and the server's split (FS-06).
"""
from __future__ import annotations

import re


def test_the_pc_line_has_what_makes_a_pc_slow():
    import pcinfo
    line = pcinfo.describe()
    assert line.startswith("pc: ")
    assert re.search(r"\d+ cores", line) and "CPU" in line and "RAM" in line and "monitor" in line
    assert "Windows" in line


def test_one_missing_number_does_not_lose_the_rest(monkeypatch):
    import pcinfo

    def broken():
        raise OSError("no such call")
    monkeypatch.setattr(pcinfo, "_memory", broken)
    line = pcinfo.describe()
    assert "?" in line and "cores" in line and "CPU" in line


def test_the_manager_logs_it_once_a_session():
    import inspect

    import companion_manager as cm
    src = inspect.getsource(cm.CompanionManager.__init__)
    assert "pcinfo" in src and "daemon=True" in src, "logged off the start-up path"


def test_the_build_includes_it():
    from pathlib import Path
    spec = (Path(__file__).resolve().parents[2] / "heytora.spec").read_text(encoding="utf-8")
    assert '"pcinfo"' in spec, "imported by name, so PyInstaller would not find it on its own"
