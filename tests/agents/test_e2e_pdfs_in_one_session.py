"""WR-05 / WR-06 (19 Oct 2026): PDFs made fast, or refused at once.

Word was started once per file - 2-4 s each; three files took 13.8 s on the
owner's PC and now 7.8 s in one hidden session. And with no Office and no
LibreOffice, it is said in a moment rather than after PowerShell has tried.
"""
from __future__ import annotations

import json

import pytest

from ai import convert_pdf


@pytest.fixture
def docs(tmp_path):
    for name in ("a.docx", "b.docx", "c.docx", "sheet.xlsx"):
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


def test_files_of_one_kind_share_one_session_and_a_failure_is_tried_alone(docs, monkeypatch):
    sessions, alone = [], []

    def hidden(cmd, env, timeout=0):
        jobs = json.loads(env["TORA_LIST"])
        sessions.append([j["in"] for j in jobs])
        lines = []
        for j in jobs:
            if j["in"].endswith("b.docx"):
                lines.append(f"fail\t{j['in']}\tlocked")
            else:
                open(j["out"], "wb").write(b"%PDF-1.7")
                lines.append(f"ok\t{j['out']}")
        return type("Done", (), {"stdout": "\n".join(lines), "stderr": "", "returncode": 0})()

    def one(path):
        alone.append(path)
        out = convert_pdf.pdf_path(path)
        open(out, "wb").write(b"%PDF-1.7")
        return out
    monkeypatch.setattr(convert_pdf, "_hidden", hidden)
    monkeypatch.setattr(convert_pdf, "to_pdf", one)
    monkeypatch.setattr(convert_pdf, "has_office", lambda app: True)
    files = [str(docs / n) for n in ("a.docx", "b.docx", "c.docx", "sheet.xlsx")]
    made, failed = convert_pdf.to_pdfs(files)
    assert len(sessions) == 1 and len(sessions[0]) == 3, "the three Word files in one session"
    assert [p.split("\\")[-1].split("/")[-1] for p in alone] == ["b.docx", "sheet.xlsx"], \
        "the one that failed, and the lone spreadsheet, on their own"
    assert len(made) == 4 and failed == []


def test_no_office_and_no_libreoffice_is_said_at_once(docs, monkeypatch):
    monkeypatch.setattr(convert_pdf, "has_office", lambda app: False)
    monkeypatch.setattr(convert_pdf, "_soffice", lambda: "")
    monkeypatch.setattr(convert_pdf, "_hidden", lambda *a, **k: pytest.fail("PowerShell was not needed to say so"))
    with pytest.raises(convert_pdf.ConvertError, match="Word is not installed on this PC, and neither is LibreOffice"):
        convert_pdf.to_pdf(str(docs / "a.docx"))


def test_the_session_script_reads_its_list_as_a_list():
    """Windows PowerShell's ConvertFrom-Json hands a list back whole: @() round it made one job of all three."""
    script = convert_pdf._batch_script("word")
    assert "$jobs = $env:TORA_LIST | ConvertFrom-Json" in script and "@($env:TORA_LIST" not in script
    assert "foreach ($j in $jobs)" in script and "$a.Quit()" in script
