"""Extensions UI: the switchboard, the dashboard frame, the Accounting module and wizard,
and the typed confirmation on sensitive approvals."""
from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from agents.bootstrap import build_agent_manager  # noqa: E402
from agents.core import ApprovalRequest, ToolCall  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    yield m
    m.stop()


def test_switchboard_turns_a_pack_on_and_off(qapp, mgr):
    from agents.ui.extensions import ExtensionsDialog
    dlg = ExtensionsDialog(mgr)
    card = dlg.cards["accounting"]
    assert not card.enabled and card.toggle_btn.text() == "Enable"
    card.toggle_btn.click()
    assert mgr.extensions.is_enabled("accounting")
    assert dlg.cards["accounting"].enabled and dlg.cards["accounting"].open_btn.isVisibleTo(dlg)
    dlg.cards["accounting"].toggle_btn.click()
    assert not mgr.extensions.is_enabled("accounting")
    dlg.close()


def test_dashboard_shows_a_tab_per_enabled_pack(qapp, mgr):
    from agents.ui.extensions import ExtensionDashboard
    empty = ExtensionDashboard(mgr)
    assert empty.tabs.count() == 1 and empty.tabs.tabText(0) == "Dashboard"
    mgr.extensions.enable("accounting")
    dash = ExtensionDashboard(mgr)
    assert dash.tabs.tabText(0) == "Accounting"
    assert dash.show_module("accounting.dashboard")
    empty.close()
    dash.close()


def test_accounting_dashboard_runs_and_lists(qapp, mgr):
    from extensions.accounting.ui.dashboard import AccountingDashboard
    mgr.extensions.enable("accounting")
    ext = mgr.extensions.get("accounting")
    ws = ext.create_demo_client(mgr.extensions._context("accounting"))
    dash = AccountingDashboard(mgr, ext)
    dash.show()
    qapp.processEvents()
    assert dash.client_box.currentText() == ws.client
    dash.run("accounts_receivable", "debtors_run")
    task = next(t for t in mgr.list_tasks() if t.metadata.get("workflow") == "debtors_run")
    done = mgr.wait(task.id, 60)
    assert done.status.value == "completed", done.error
    dash.refresh()
    assert dash.attention.topLevelItemCount() >= 1, "exceptions are listed"
    assert dash.drafts.topLevelItemCount() >= 1, "the reminder drafts are listed"
    assert any("Reminder" in dash.reports.item(i).text() for i in range(dash.reports.count()))
    assert dash.deadlines.topLevelItemCount() >= 1, "ZA demo client has typical deadlines"
    dash.close()


def test_wizard_creates_a_scheduled_agent(qapp, mgr):
    from extensions.accounting.ui.wizard import AgentSetupWizard
    mgr.extensions.enable("accounting")
    ext = mgr.extensions.get("accounting")
    ws = ext.create_demo_client(mgr.extensions._context("accounting"))
    wiz = AgentSetupWizard(mgr, ext, client=ws.client, template="accounts_payable")
    wiz.schedule.setCurrentIndex(wiz.schedule.findData("weekdays"))
    wiz.auto.setValue(90)
    wiz.flag.setValue(60)
    wiz._create(run_now=False)
    agents = mgr.extensions.settings("accounting")["agents"]
    assert agents and agents[-1]["template"] == "accounts_payable" and agents[-1]["schedule"]["every"] == "weekdays"
    assert any(j["name"].startswith("Accounts Payable Agent") for j in mgr.scheduler.jobs())
    from extensions.accounting.workspace import ClientWorkspace
    assert ClientWorkspace.load(ws.root).confidence == {"auto": 0.9, "flag": 0.6}


def test_sensitive_approval_needs_the_typed_word(qapp, mgr):
    from agents.ui.approval import ApprovalDialog
    req = ApprovalRequest(task_id="t", agent_id="a", call=ToolCall("release_payment_batch", {"draft_id": "D-0001"}),
                          reason="moves money", sensitive=True, confirm_word="RELEASE")
    dlg = ApprovalDialog(mgr, req)
    assert dlg._always_btn is None, "no 'allow for this task' on a sensitive action"
    assert not dlg._approve_btn.isEnabled()
    dlg.type_confirmation("releas")
    assert not dlg._approve_btn.isEnabled()
    dlg.type_confirmation("release")
    assert dlg._approve_btn.isEnabled()
    dlg.click_approve()
    assert dlg.decision is True and dlg.confirmation == "release"
