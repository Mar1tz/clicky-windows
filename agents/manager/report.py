"""What a finished job says (OR-15, the Massive Direction Plan).

A job used to end in "Completed: <title>" and a tick per step, even when
the person had said no to the one call that mattered - the EN-01 scenario
set has a guessed delete, refused, reported as done. Now every job that
stops ends in one of five shapes, each saying what was verified, what did
not happen, and the next step, with the path of what it made:

    success     everything ran and was checked
    partial     it ran to the end, but something did not happen
    waiting     it is paused on the person (an approval, a sign-in, a check)
    failure     it could not go on, and why
    cancelled   it was stopped, or its plan was refused

Built from the task alone (its steps, their tool results, its metadata),
so a job recovered after a restart reports the same way.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from agents.core import StepStatus, TaskStatus

SHAPES = ("success", "partial", "waiting", "failure", "cancelled")
READS = ("read_file", "list_dir", "git_status", "git_diff", "git_log", "read_workbook",
         "check_formulas", "screenshot", "find_on_screen", "list_windows", "extract_information")
_WAITING = {
    TaskStatus.WAITING_APPROVAL: "Approve or deny what it asked, and it carries on.",
    TaskStatus.WAITING_USER: "Do that step yourself, then press Resume.",
    TaskStatus.NEEDS_ATTENTION: "Check whether that happened, then Resume or Stop.",
    TaskStatus.PAUSED: "Press Resume when you are ready.",
    TaskStatus.RETRY_WAIT: "Nothing - it will try again by itself.",
}


@dataclass
class Report:
    shape: str
    title: str
    verified: list = field(default_factory=list)
    not_done: list = field(default_factory=list)
    next_step: str = ""
    artifacts: list = field(default_factory=list)
    review: list = field(default_factory=list)          # findings a person must look at
    checks: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def text(self) -> str:
        head = {"success": "Completed", "partial": "Partly done", "waiting": "Waiting",
                "failure": "Didn't finish", "cancelled": "Stopped"}[self.shape]
        lines = [f"{head}: {self.title}", *[f"  ✓ {v}" for v in self.verified]]
        if self.not_done:
            lines.append("Did not happen:")
            lines.extend(f"  ✗ {n}" for n in self.not_done)
        if self.checks:
            lines.append(f"{self.checks} checks completed automatically")
        if self.review:
            lines.append(f"Needs review ({len(self.review)}):")
            lines.extend(f"  {i}. {t}" for i, t in enumerate(self.review[:20], 1))
            if len(self.review) > 20:
                lines.append(f"  … and {len(self.review) - 20} more")
        elif self.checks:
            lines.append("Nothing needs review.")
        if self.artifacts:
            lines.append("Made: " + ", ".join(self.artifacts[:5]))
        if self.next_step:
            lines.append(f"Next: {self.next_step}")
        return "\n".join(lines)

    def spoken(self, who: str) -> str:
        if self.shape == "success":
            return f"{who} is done - {self.title} is ready."
        if self.shape == "partial":
            n = len(self.not_done)
            return (f"{who} finished {self.title}, but {n} thing{'s' if n != 1 else ''} did not happen: "
                    f"{self.not_done[0]}.")
        if self.shape == "waiting":
            return f"{self.title} is waiting for you. {self.next_step}"
        if self.shape == "failure":
            return f"{who} could not finish {self.title}. {self.next_step}"
        return f"{self.title} was stopped."


def _refusal(result) -> str:
    """Why a call did not run, in plain words - or "" if it was not refused."""
    error = (result.error or "").lower()
    if error.startswith("permission denied"):
        return "you said no" if "changed after" not in error and "min after" not in error \
            else "the approval no longer matched"
    if error.startswith("outside workspace"):
        return "it was outside the job's folder"
    return ""


def _artifacts(task) -> list[str]:
    out: list[str] = []
    for step in task.steps:
        for r in step.results or []:
            if not r.ok or r.tool_name in READS:
                continue
            data = r.data or {}
            for key in ("path", "output", "output_path", "file"):
                value = data.get(key)
                if isinstance(value, str) and value and value not in out:
                    out.append(value)
    return out


def report_for(task, verification: str = "") -> Report:
    status = task.status
    rep = Report(shape="success", title=task.title or task.goal)
    meta = task.metadata or {}
    for step in task.steps:
        if step.status is StepStatus.DONE:
            rep.verified.append(step.title)
            for r in step.results or []:
                why = "" if r.ok else _refusal(r)
                if why:
                    rep.not_done.append(f"{r.tool_name.replace('_', ' ')} was not run - {why}")
        elif step.status is StepStatus.SKIPPED:
            rep.not_done.append(f"skipped: {step.title}")
        elif step.status is StepStatus.FAILED and status is TaskStatus.COMPLETED:
            rep.not_done.append(f"did not finish: {step.title}")
    if verification:
        rep.verified.append(f"checked: {verification}")
    rep.checks = int(meta.get("checks_run") or 0)
    order = {"high": 0, "review": 1, "info": 2}
    findings = sorted((f for f in meta.get("findings", []) if isinstance(f, dict)),
                      key=lambda f: order.get(f.get("severity", "review"), 1))
    rep.review = [f.get("title", "") for f in findings]
    rep.artifacts = _artifacts(task)

    if status is TaskStatus.COMPLETED:
        rep.shape = "partial" if rep.not_done else "success"
        rep.next_step = ("Ask again for what did not happen." if rep.not_done
                         else (f"Open {rep.artifacts[0]}." if rep.artifacts else "Nothing else to do."))
    elif status in _WAITING:
        rep.shape = "waiting"
        rep.next_step = _WAITING[status]
    elif status is TaskStatus.FAILED:
        rep.shape = "failure"
        reason = (task.error or "it stopped").split("\n")[0][:160]
        rep.next_step = f"{reason[0].upper() + reason[1:]}. Ask again, or change what you asked for."
    else:
        rep.shape = "cancelled"
        rep.next_step = ("The plan was not run - ask again with what to change."
                         if status is TaskStatus.REJECTED else "Nothing more will run.")
    return rep
