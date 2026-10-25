"""One ID follows a job (EN-18, the Massive Direction Plan).

Events already carry the task's id and each tool call its own; the log
lines did not, so "which job wrote this?" meant matching timestamps. While
a job runs its id sits in a context variable - asyncio hands it to every
task and thread the job starts - and every log line made on its behalf is
prefixed "[job <id>]", whichever logger wrote it.
"""
from __future__ import annotations

import contextvars
import logging

JOB = contextvars.ContextVar("heytora_job", default="")
_installed = False


def install() -> None:
    """Prefix log lines made inside a job (once per process)."""
    global _installed
    if _installed:
        return
    _installed = True
    make = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = make(*args, **kwargs)
        job = JOB.get()
        record.job = job
        if job and isinstance(record.msg, str) and not record.msg.startswith("[job "):
            record.msg = f"[job {job}] {record.msg}"
        return record

    logging.setLogRecordFactory(factory)
