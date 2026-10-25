"""Report shapes (OR-15, the Massive Direction Plan).

Success, partial, waiting, failure and cancelled each say what was
verified, what did not happen and the next step, with the path of what
the job made. A call the person refused inside a "done" step makes the
job partial - it is never reported as done.
"""
from __future__ import annotations

from agents.core import Step, StepStatus, Task, TaskStatus, ToolCall, ToolResult
from agents.manager.report import report_for


def _task(status, *steps, error=""):
    t = Task(title="Tidy the reports", goal="tidy", workspace=".")
    t.steps = list(steps)
    t.status = status
    t.error = error
    return t


def _step(title, status=StepStatus.DONE, *results):
    s = Step(title)
    s.status = status
    s.results = list(results)
    return s


def _ok(tool, **data):
    return ToolResult(call_id="1", tool_name=tool, ok=True, output="ok", data=data)


def _no(tool, error):
    return ToolResult.failure(ToolCall(tool, {}), error)


def test_success_says_what_it_made():
    rep = report_for(_task(TaskStatus.COMPLETED, _step("Write it", StepStatus.DONE,
                                                       _ok("write_file", path="C:/w/summary.md"))), "file exists")
    assert rep.shape == "success" and rep.artifacts == ["C:/w/summary.md"]
    text = rep.text()
    assert text.startswith("Completed: Tidy the reports") and "checked: file exists" in text
    assert "Next: Open C:/w/summary.md." in text


def test_a_refused_call_makes_it_partial():
    rep = report_for(_task(TaskStatus.COMPLETED, _step("Delete the old one", StepStatus.DONE,
                                                       _no("delete_path", "permission denied: needs a human"))))
    assert rep.shape == "partial"
    assert rep.not_done == ["delete path was not run - you said no"]
    assert rep.text().startswith("Partly done:") and "✗ delete path was not run" in rep.text()
    assert "did not happen: delete path was not run" in rep.spoken("File Agent")


def test_waiting_says_what_to_do():
    rep = report_for(_task(TaskStatus.WAITING_USER, _step("Check the portal", StepStatus.PENDING)))
    assert rep.shape == "waiting" and "Resume" in rep.next_step


def test_failure_says_why_and_what_next():
    rep = report_for(_task(TaskStatus.FAILED, _step("Build", StepStatus.FAILED), error="step 'Build' failed: npm missing"))
    assert rep.shape == "failure" and rep.next_step.startswith("Step 'Build' failed: npm missing.")


def test_cancelled_and_rejected():
    assert report_for(_task(TaskStatus.CANCELLED)).next_step == "Nothing more will run."
    rejected = report_for(_task(TaskStatus.REJECTED))
    assert rejected.shape == "cancelled" and "plan was not run" in rejected.next_step


def test_every_ending_is_written_on_the_job(tmp_path):
    """Through the orchestrator: the report rides on the task's metadata."""
    from tests.agents.test_manager import Boom, FakeRegistry, ListPlanner, MkDir, make_manager
    planner = ListPlanner([("Make it", [("create_folder", {"path": "out"})])])
    mgr = make_manager(tmp_path, planner=planner, tools=FakeRegistry(MkDir(), Boom()))
    try:
        task = mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path))).id, 10)
    finally:
        mgr.stop()
    rep = task.metadata["report"]
    assert rep["shape"] == "success" and rep["artifacts"] and rep["artifacts"][0].endswith("out")
    assert task.result.startswith("Completed: t")
