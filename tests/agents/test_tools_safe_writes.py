"""Verified artifacts, written safely (OR-16, the Massive Direction Plan).

A file is written to a temporary name beside it and renamed into place
only when the write finished - a crash leaves the old file, never half a
new one - and it is read back before the tool says it is done.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from agents.core import ToolCall, ToolContext
from agents.tools import default_registry
from agents.tools.base import replacing


def test_a_failed_write_leaves_the_old_file(tmp_path):
    target = tmp_path / "notes.md"
    target.write_text("the old notes", encoding="utf-8")
    with pytest.raises(RuntimeError):
        with replacing(str(target)) as tmp:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("half a new")
            raise RuntimeError("the disk filled up")
    assert target.read_text(encoding="utf-8") == "the old notes"
    assert os.listdir(tmp_path) == ["notes.md"], "and no temporary file is left"


def test_the_extension_is_kept_for_writers_that_go_by_it(tmp_path):
    with replacing(str(tmp_path / "chart.png")) as tmp:
        assert tmp.endswith(".png")
        open(tmp, "wb").close()
    assert (tmp_path / "chart.png").exists()


def _run(tmp_path, name, **args):
    reg = default_registry()
    return asyncio.run(reg.execute(ToolCall(name, args), ToolContext(workspace=str(tmp_path))))


def test_write_file_lands_whole(tmp_path):
    result = _run(tmp_path, "write_file", path="out/summary.md", content="# Done\n" * 500)
    assert result.ok and (tmp_path / "out" / "summary.md").read_text(encoding="utf-8") == "# Done\n" * 500
    assert sorted(os.listdir(tmp_path / "out")) == ["summary.md"]


def test_a_workbook_is_opened_again_before_it_is_done(tmp_path):
    pytest.importorskip("openpyxl")
    result = _run(tmp_path, "write_workbook", path="totals.xlsx",
                  rows=[["item", "cost"], ["paint", 12.5]])
    assert result.ok, result.error
    from openpyxl import load_workbook
    assert load_workbook(tmp_path / "totals.xlsx").active["B2"].value == 12.5
    assert sorted(os.listdir(tmp_path)) == ["totals.xlsx"]
