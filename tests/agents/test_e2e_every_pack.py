"""Every pack in the extensions folder, held to the same rules.

A new pack gets these checks by existing: it loads, plugs in and comes out
cleanly; every tool declares an action level whose risk and approval
flags match it; the agent it offers has a worker; what it says it
understands routes to it; its dashboard draws on an empty folder; and
saying what you do offers it.
"""
from __future__ import annotations

import pytest

from agents.bootstrap import build_agent_manager
from agents.extensions import ExtensionRegistry

PACKS = sorted(m.id for m in ExtensionRegistry(None).discover())


def test_the_packs_are_found():
    assert {"accounting", "freelancer", "creator"} <= set(PACKS)


@pytest.mark.parametrize("pack", PACKS)
def test_a_pack_plugs_in_and_comes_out_cleanly(tmp_path, pack):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    core, core_workers = set(m.tools.names()), set(m.workers.types())
    ext = m.extensions.enable(pack)
    added = set(m.tools.names()) - core
    assert added, "a pack adds tools"
    for name in added:
        tool = m.tools.get(name)
        level = getattr(tool, "level", None)
        assert level is not None, f"{name} declares no action level"
        assert tool.spec.risk is level.risk, name
        assert bool(getattr(tool, "always_ask", False)) == level.always_ask, name
        assert bool(getattr(tool, "sensitive", False)) == level.sensitive, name
        assert tool.spec.description.startswith("["), f"{name}: the level is the first thing the model reads"
    for t in ext.get_agent_templates():
        assert t.worker_type in m.workers.types(), f"{t.id} has no worker"
        assert set(t.tools) <= set(m.tools.names()), f"{t.id} lists tools that do not exist"
    for wf in ext.get_workflows():
        assert m.extensions.template(wf.template_id) is not None, f"{wf.id} runs as an unknown agent"
    manifest = ext.manifest
    assert manifest.description and manifest.offer and manifest.activation_phrases
    m.extensions.disable(pack)
    assert set(m.tools.names()) == core and set(m.workers.types()) == core_workers
    assert m.extensions.activation_offer(f"hi, {manifest.activation_phrases[0]}")["id"] == pack


@pytest.mark.parametrize("pack", PACKS)
def test_a_pack_dashboard_draws_on_a_fresh_folder(tmp_path, pack):
    pytest.importorskip("PyQt6")
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    ext = m.extensions.enable(pack)
    for module in ext.get_dashboard_modules():
        widget = module.factory(m, ext)
        try:
            assert widget is not None
        finally:
            for timer in widget.findChildren(QTimer):
                timer.stop()
            widget.deleteLater()


def test_with_every_pack_on_each_request_still_reaches_its_own_pack(tmp_path):
    """A pack's rules must not take another pack's requests: "check my shop"
    is the store's even when Sales, which checks pipelines, is on too."""
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    for pack in PACKS:
        m.extensions.enable(pack)
    checked = 0
    for pack in PACKS:
        for text, workflow in getattr(m.extensions.get(pack), "EXAMPLES", []):
            intent = m.extensions.match_intent(text)
            assert intent is not None, f"{pack}: {text!r} reaches no pack"
            assert intent.metadata["extension"] == pack, f"{text!r} went to {intent.metadata['extension']}, not {pack}"
            assert intent.metadata.get("workflow", "") == workflow, (pack, text)
            checked += 1
    assert checked >= 20
