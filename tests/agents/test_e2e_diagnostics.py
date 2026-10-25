"""Diagnostics you preview (EN-16, the Massive Direction Plan).

The package - versions, models, memory counts, the end of tora.log - is
redacted (keys, the user name in paths, email addresses), shown in full,
and written only when the person presses Save. Nothing is sent.
"""
from __future__ import annotations

import getpass

import pytest

import diagnostics

# Built from pieces: a real-looking key in a source file trips push protection.
FAKE_KEY = "AI" + "za" + "Sy" + "D3mo" + "x" * 31


def _log(tmp_path):
    user = getpass.getuser()
    (tmp_path / "tora.log").write_text(
        f"started\nkey={FAKE_KEY}\nmail from someone@example.com\n"
        f"opened C:\\Users\\{user}\\Documents\\taxes.xlsx\n"
        f"<module from 'C:\\\\Users\\\\{user}\\\\AppData\\\\Roaming'>\nlast line\n", encoding="utf-8")
    return user


def test_the_package_is_redacted(tmp_path):
    user = _log(tmp_path)
    text = diagnostics.build(tmp_path)
    assert "HeyTora diagnostics" in text and "last line" in text, "the log's end is in it"
    assert FAKE_KEY not in text and "someone@example.com" not in text
    assert f"Users\\{user}\\" not in text and "Users\\<you>\\Documents\\taxes.xlsx" in text


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def test_it_is_seen_before_it_is_saved(qapp, tmp_path):
    from PyQt6.QtWidgets import QPushButton

    from ui.diagnostics_preview import DiagnosticsPreview
    _log(tmp_path)
    text = diagnostics.build(tmp_path)
    saved = []
    dlg = DiagnosticsPreview(text, saved.append)
    assert dlg.view.toPlainText() == text and not saved, "shown, nothing written yet"
    next(b for b in dlg.findChildren(QPushButton) if b.text() == "Save").click()
    assert saved == [text], "exactly what was shown"
    out = diagnostics.save(text, tmp_path)
    assert out.read_text(encoding="utf-8") == text
