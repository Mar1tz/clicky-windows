"""Judgement, for the things rules cannot check.

`RuleVerifier` answers "does the file exist?" — cheap, certain, offline.
It cannot answer "is what is in the file actually a three-line haiku?",
and that gap is where agents quietly fail: `create_file` returns ok, the
rule sees a path on disk, and the task is marked COMPLETED with an empty
or wrong file in it.

`CompositeVerifier` closes it without paying a model on every step:

    rules fail   → fail immediately. A missing file is not a judgement
                   call, and asking a model about it wastes a call and
                   invites it to argue.
    rules silent → ask the model. "No rule" means nobody checked, which
                   is exactly where a second opinion is worth buying.
    rules pass   → accept, except for the final task check, which always
                   gets a model pass because that is the one verdict the
                   user actually sees.

Evidence is the real filesystem, not the agent's own account of it: the
model is shown sizes and the opening bytes of the files the step claims
to have written. An agent that says "created App.jsx" and wrote nothing
is caught by what is on disk, not by what it said.

Failures degrade to the rule verdict, never to a bare pass — a broken
judge must not become a rubber stamp.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Iterable, Optional

from agents.core import (
    AgentContext, Check, Step, StepStatus, VerificationResult, Verifier,
    ProviderMessage, ToolResult,
)

from .verifier import RuleVerifier

log = logging.getLogger("agents.verification.provider")

MAX_PREVIEW = 400          # chars of each file shown to the model
MAX_FILES = 8              # files described per step
MAX_TOKENS = 700

_SYSTEM = (
    "You verify that an autonomous agent's step really did what it claimed. "
    "You are shown the step, the tool calls it ran, and the ACTUAL state of "
    "the files on disk afterwards. Judge the disk, not the agent's wording. "
    "Fail the step when a file it claims to have written is missing, empty, "
    "truncated, a placeholder, or plainly not what the step asked for. "
    "Pass it when the work is genuinely done, even if imperfect. "
    "Judge against the GOAL. If the goal was to open, show, or do something on "
    "screen rather than to produce files, the evidence is the open windows and "
    "the tool results, not the disk - do not fail a UI task for writing nothing. "
    "Reply with ONLY a JSON object: {\"ok\": true|false, \"reason\": \"one "
    "short sentence\"}. No prose, no code fence."
)


class ProviderVerifier(Verifier):
    """Asks a model whether a step's claimed work is real."""

    def __init__(self, *, max_tokens: int = MAX_TOKENS, max_files: int = MAX_FILES,
                 max_preview: int = MAX_PREVIEW):
        self.max_tokens = max_tokens
        self.max_files = max_files
        self.max_preview = max_preview

    async def verify_step(self, step: Step, ctx: AgentContext) -> VerificationResult:
        return await self._judge(self._step_prompt(step, ctx), f"step '{step.title}'", ctx)

    async def verify_task(self, ctx: AgentContext) -> VerificationResult:
        return await self._judge(self._task_prompt(ctx), "task", ctx)

    # -- the call ------------------------------------------------------------

    async def _judge(self, prompt: str, what: str, ctx: AgentContext) -> VerificationResult:
        resp = await ctx.provider.complete(
            [ProviderMessage(role="user", content=prompt)],
            tools=None, system=_SYSTEM, temperature=0.0, max_tokens=self.max_tokens,
        )
        data = _parse(resp.text)
        ok = bool(data.get("ok"))
        reason = str(data.get("reason") or "").strip() or ("looks done" if ok else "not done")
        return VerificationResult(
            ok=ok, summary=reason,
            checks=[Check(f"model judged {what}", ok, reason)],
        )

    # -- prompts -------------------------------------------------------------

    @staticmethod
    def _skill(ctx: AgentContext) -> list[str]:
        """The user's skill for this job (agents/skills), when it has one. The
        agent was told to follow it, so what it asks for is the goal too:
        judged without it, a shopping list skill's "always add milk" failed
        the job for adding milk nobody asked for (6 Oct 2026)."""
        skills = [k for k in (ctx.extras or {}).get("skills") or [] if k]
        if not skills:
            return []
        return ["The user's own instructions for this kind of job - the agent was told to follow "
                "them, so what they ask for is part of the goal, not extra:\n" + "\n\n".join(skills)]

    def _step_prompt(self, step: Step, ctx: AgentContext) -> str:
        parts = [
            f"Overall goal: {ctx.task.goal}",
            *self._skill(ctx),
            f"Step: {step.title}",
        ]
        if step.description:
            parts.append(f"Step detail: {step.description}")
        if step.summary:
            parts.append(f"The agent reported: {step.summary}")
        actions = [f"  {c.tool_name}({_short_args(c.arguments)})" for c in step.tool_calls]
        if actions:
            parts.append("Tool calls it ran:\n" + "\n".join(actions[:12]))
        evidence = self._evidence(step.results, ctx, step.tool_calls)
        parts.append("Actual state on disk:\n" + (evidence or "  (no files touched)"))
        windows = _open_windows()
        if windows:
            parts.append("Open windows right now:\n" + "\n".join(f"  {w}" for w in windows))
        return "\n\n".join(parts)

    def _task_prompt(self, ctx: AgentContext) -> str:
        done = [s for s in ctx.task.steps if s.status is not StepStatus.SKIPPED]
        lines = [f"  [{s.status.value}] {s.title}" for s in done]
        results: list[ToolResult] = [r for s in done for r in s.results]
        calls = [c for s in done for c in s.tool_calls]
        parts = [f"Goal: {ctx.task.goal}", *self._skill(ctx), *_typed(ctx),
                 "Steps taken:\n" + ("\n".join(lines) or "  (none)")]
        if ctx.task.worker_type in ("computer", "browser"):
            # Its work is on screen. Judged by the disk, "type a shopping list
            # in Notepad" failed for "no shopping list text file was created".
            did = [f"  {c.tool_name}({_short_args(c.arguments)}) -> "
                   f"{'ok' if r.ok else 'FAILED'}: {_one_line(r.output if r.ok else r.error)}"
                   for c, r in zip(calls, results)][-12:]
            parts.append("What its actions did (latest last):\n" + ("\n".join(did) or "  (nothing)"))
            windows = _open_windows()
            if windows:
                parts.append("Open windows right now:\n" + "\n".join(f"  {w}" for w in windows))
            parts.append("This job works on screen: judge it by these. No file is expected unless "
                         "the goal asks to save one.")
        else:
            parts.append("Actual state on disk:\n" + (self._evidence(results, ctx, calls)
                                                      or _tree(ctx.workspace) or "  (workspace is empty)"))
        parts.append("Did the agent achieve the goal?")
        return "\n\n".join(parts)

    def _evidence(self, results: list[ToolResult], ctx: AgentContext, calls: Iterable = ()) -> str:
        """Describe what is really on disk for the paths this step touched,
        and for the files its commands named (AG-03: a conversion's output
        is named in the command, never returned as a `path`)."""
        seen: list[str] = []
        for res in results:
            # `paths`: a tool that made more than one file (a picture and its
            # print PDF). With only `path` read, the verifier was sure no PDF
            # existed and had the agent make another.
            data = res.data or {}
            more = data.get("paths") if isinstance(data.get("paths"), list) else []
            for path in [data.get("path"), *more]:
                if isinstance(path, str) and path and path not in seen:
                    seen.append(path)
            if len(seen) >= self.max_files:
                break
        for path in _named_files(calls, ctx.workspace):
            if path not in seen and len(seen) < self.max_files * 2:
                seen.append(path)
        since = (getattr(ctx.task, "started_at", None) or getattr(ctx.task, "created_at", 0.0) or 0.0) - 2.0
        lines: list[str] = []
        for path in seen:
            full = path if os.path.isabs(path) else os.path.join(ctx.workspace, path)
            name = os.path.relpath(full, ctx.workspace) if full.startswith(ctx.workspace) else full
            if os.path.isdir(full):
                # "(directory, 32 entries)" was all a judge saw of the folder a
                # PDF was written into, and it failed the job for not seeing
                # it (AG-03). What changed there during this job, by name.
                lines.append(f"  {name}/  (directory, {len(os.listdir(full))} entries)"
                             + _changed_in(full, since))
            elif os.path.isfile(full):
                size = os.path.getsize(full)
                kind = _binary_kind(full)
                if kind:
                    lines.append(f"  {name}  ({size} bytes, {kind})")
                    continue
                head = _head(full, self.max_preview)
                lines.append(f"  {name}  ({size} bytes)" + (f"\n    | {head}" if head else
                                                            "\n    | (EMPTY FILE)"))
            else:
                lines.append(f"  {name}  (DOES NOT EXIST)")
        for res in results:
            if not res.ok:
                lines.append(f"  {res.tool_name} FAILED: {res.error[:200]}")
            elif (res.data or {}).get("recorded"):
                lines.append(f"  {res.tool_name} recorded: {str(res.data['recorded'])[:200]}")
        return "\n".join(lines)


class CompositeVerifier(Verifier):
    """Rules first; the model where rules are silent, and on the final
    verdict. See the module docstring for why that split."""

    def __init__(self, rules: Optional[Verifier] = None,
                 model: Optional[Verifier] = None,
                 *, model_on_every_step: bool = False):
        self.rules = rules or RuleVerifier()
        self.model = model or ProviderVerifier()
        self.model_on_every_step = model_on_every_step

    async def verify_step(self, step: Step, ctx: AgentContext) -> VerificationResult:
        rule_result = await self.rules.verify_step(step, ctx)
        if not rule_result.ok:
            return rule_result                      # certain; do not pay a model
        if not (self.model_on_every_step or _rules_were_silent(rule_result)):
            return rule_result
        return await self._ask(self.model.verify_step(step, ctx), rule_result, "step")

    async def verify_task(self, ctx: AgentContext) -> VerificationResult:
        rule_result = await self.rules.verify_task(ctx)
        if not rule_result.ok:
            return rule_result
        return await self._ask(self.model.verify_task(ctx), rule_result, "task")

    @staticmethod
    async def _ask(coro, rule_result: VerificationResult, what: str) -> VerificationResult:
        """Run the model check, degrading to the rule verdict on failure.

        A verifier that returns ok=True when it is broken is worse than no
        verifier at all, so the fallback is the rules' answer (which was a
        pass, arrived at by evidence) rather than an invented one.
        """
        try:
            judged = await coro
        except Exception as exc:  # noqa: BLE001
            log.warning("model %s verification unavailable (%s); using rules", what, exc)
            return rule_result
        return VerificationResult(
            ok=judged.ok,
            checks=[*rule_result.checks, *judged.checks],
            summary=judged.summary if not judged.ok
            else f"{rule_result.summary}; {judged.summary}",
        )


def _rules_were_silent(result: VerificationResult) -> bool:
    """True when nothing was actually checked - every check said 'no rule',
    or there were no checks at all. That is the gap worth a model call."""
    if not result.checks:
        return True
    return all("no rule" in (c.detail or "").lower() for c in result.checks)


def _parse(text: str) -> dict[str, Any]:
    from agents.planning.planner import parse_json_object
    try:
        data = parse_json_object(text)
    except Exception:  # noqa: BLE001
        raise ValueError(f"verifier did not return JSON: {text[:120]!r}")
    if not isinstance(data, dict) or "ok" not in data:
        raise ValueError(f"verifier JSON lacks 'ok': {str(data)[:120]}")
    return data


def _open_windows(limit: int = 12) -> list[str]:
    """Window titles, for judging goals whose evidence is on screen."""
    try:
        from agents.tools.app_tools import list_windows
        return [w["title"][:80] for w in list_windows()[:limit]]
    except Exception:  # noqa: BLE001 - headless, or not Windows
        return []


def _typed(ctx: AgentContext) -> list[str]:
    """OR-02: the outcome wanted and the constraints given - judged too."""
    typed = (getattr(ctx.task, "metadata", None) or {}).get("intent")
    if not isinstance(typed, dict):
        return []
    out = [f"Outcome wanted: {typed.get('outcome', '')}"]
    if typed.get("constraints"):
        out.append("Constraints they gave - a broken one fails the job: " + "; ".join(typed["constraints"]))
    return out


def _one_line(text: Optional[str], limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _short_args(args: dict[str, Any], limit: int = 60) -> str:
    bits = []
    for k, v in list(args.items())[:3]:
        text = str(v).replace("\n", " ")
        bits.append(f"{k}={len(str(v))} chars" if len(text) > limit else f"{k}={text}")
    return ", ".join(bits)


# Files a command names: a document, a picture, an archive - the outputs a
# conversion or an export writes, which no tool returns as its `path`.
_FILE_RE = re.compile(
    r"""(?:[A-Za-z]:[\\/]|\\\\|\.{0,2}[\\/])?[^\s"'<>|*?]*?[^\s"'<>|*?\\/]"""
    r"""\.(?:pdf|docx?|xlsx?|pptx?|csv|png|jpe?g|gif|webp|svg|txt|md|html?|zip|mp3|mp4|wav)\b""",
    re.IGNORECASE)
# A quoted path may hold spaces: "C:\Users\x\10 Mei 2026.pdf".
_QUOTED_RE = re.compile(
    r"""["']((?:[A-Za-z]:[\\/]|\\\\)[^"'<>|*?\r\n]+?\.(?:pdf|docx?|xlsx?|pptx?|csv|png|jpe?g|gif|webp|svg|txt|md|html?|zip|mp3|mp4|wav))["']""",
    re.IGNORECASE)


def _named_files(calls: Iterable, workspace: str) -> list[str]:
    """Paths named in the tool calls' arguments: those on disk, and absolute
    ones that are not - an output that was never written."""
    out: list[str] = []
    for call in calls:
        args = getattr(call, "arguments", None) or {}
        if not isinstance(args, dict):
            continue
        cwd = str(args.get("cwd") or workspace)
        for text in (v for v in args.values() if isinstance(v, str)):
            found = [m.group(1) for m in _QUOTED_RE.finditer(text)]
            rest = _QUOTED_RE.sub(" ", text)
            found += [m.group(0) for m in _FILE_RE.finditer(rest)]
            for raw in found:
                raw = raw.strip().strip("'\"")
                full = raw if os.path.isabs(raw) else os.path.normpath(os.path.join(cwd, raw))
                if (os.path.isabs(raw) or os.path.exists(full)) and full not in out:
                    out.append(full)
    return out[:12]


def _changed_in(folder: str, since: float, limit: int = 8) -> str:
    """'; changed during this job: a.pdf (48210 bytes, PDF document), ...'"""
    hits = []
    try:
        for entry in os.scandir(folder):
            try:
                st = entry.stat()
            except OSError:
                continue
            if st.st_mtime >= since:
                hits.append((st.st_mtime, entry, st.st_size))
    except OSError:
        return ""
    if not hits:
        return "; nothing in it changed during this job"
    shown = []
    for _t, entry, size in sorted(hits, key=lambda h: h[0], reverse=True)[:limit]:
        if entry.is_dir():
            shown.append(f"{entry.name}/")
        else:
            kind = _binary_kind(entry.path)
            shown.append(f"{entry.name} ({size} bytes{', ' + kind if kind else ''})")
    more = f", and {len(hits) - limit} more" if len(hits) > limit else ""
    return "; changed during this job: " + ", ".join(shown) + more


_MAGIC = ((b"%PDF", "PDF document"), (b"\x89PNG", "PNG image"), (b"\xff\xd8\xff", "JPEG image"),
          (b"GIF8", "GIF image"), (b"PK\x03\x04", "Office or ZIP file"), (b"ID3", "MP3 audio"))


def _binary_kind(path: str) -> str:
    """What a binary file is, by its first bytes: shown as its kind, never as
    its bytes read as text. "" for text, or a file that cannot be read."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return ""
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind
    return ""


def _head(path: str, limit: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(limit + 1)
    except OSError:
        return ""
    text = text.strip()
    if not text:
        return ""
    return (text[:limit] + "…") if len(text) > limit else text


def _tree(root: str, limit: int = 20) -> str:
    lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        prefix = "" if rel == "." else f"{rel}/"
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            lines.append(f"  {prefix}{name} ({size} bytes)")
            if len(lines) >= limit:
                return "\n".join(lines) + "\n  …"
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "__pycache__"}]
    return "\n".join(lines)
