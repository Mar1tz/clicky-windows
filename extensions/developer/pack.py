"""The Software developers pack: tools, the dev assistant, its checklists.

Every tool is READ or DRAFT, and the only files written are this pack's
own (release notes, reviews, triage reports) - never the repository.
Nothing is committed, pushed, merged, tagged or labelled; the core git
tools do that, with their own approvals. Tool names stay clear of the
core's git_* tools.

READ   dev_overview, review_changes, find_todos, check_dependencies, explain_test_failures, suggest_next_version
DRAFT  write_release_notes, triage_issues
"""
from __future__ import annotations

import os
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Workflow
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .devdesk import DevDesk, git

EXT = "developer"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="the repository")
REPO = {"type": "string", "description": "The repository's folder. Default: the pack's default repo."}


def _path(d: DevDesk, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(d.root, p)


@TOOLS.tool("dev_overview", "A repository at a glance: branch, uncommitted changes, commits since the last tag, "
            "TODOs.", Level.READ, {"repo": REPO})
def dev_overview(d: DevDesk, a: dict[str, Any]) -> Out:
    repo = d.repo(a.get("repo", ""))
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    dirty = [ln for ln in git(repo, "status", "--porcelain").splitlines() if ln.strip()]
    v = d.next_version(repo)
    t = d.todos(repo)
    return Out(f"{os.path.basename(repo)} on {branch}: {len(dirty)} uncommitted change(s); {v['commits']} commit(s) since "
               f"{v['current']}" + (f" (next {v['next']}, a {v['bump']} release)" if v["next"] else "") +
               f"; {t['count']} TODO/FIXME.", {"branch": branch, "uncommitted": dirty, "version": {k: v[k] for k in
                                                                                                   ("current", "next", "bump", "commits")}})


@TOOLS.tool("review_changes", "Review a change (base...head, or the uncommitted work): size, secrets, debug "
            "leftovers, source changed without tests.", Level.READ,
            {"repo": REPO, "base": {"type": "string"}, "head": {"type": "string"}})
def review_changes(d: DevDesk, a: dict[str, Any]) -> Out:
    r = d.review(d.repo(a.get("repo", "")), a.get("base", ""), a.get("head") or "HEAD")
    findings = [finding(f"{s['file']}: looks like {s['what']} was added", action="remove it and rotate the secret",
                        entity=s["file"], severity="high") for s in r["secrets"]]
    findings += [finding(f"{x['file']}: debug leftover `{x['line']}`", action="remove it", entity=x["file"])
                 for x in r["leftovers"]]
    if r["untested"]:
        findings.append(finding(f"{len(r['untested'])} source file(s) changed and no test changed",
                                ", ".join(r["untested"][:8]), action="add or update a test"))
    if r["too_big"]:
        findings.append(finding(f"{r['added'] + r['removed']} lines changed - hard to review in one go",
                                action="split it into smaller changes"))
    return Out(f"{r['range']}: {len(r['files'])} file(s), +{r['added']} -{r['removed']}"
               + (f", {len(r['binary'])} binary" if r["binary"] else "") + ".", {"review": r}, findings)


@TOOLS.tool("find_todos", "TODO, FIXME, HACK and XXX notes in the tracked files.", Level.READ, {"repo": REPO})
def find_todos(d: DevDesk, a: dict[str, Any]) -> Out:
    t = d.todos(d.repo(a.get("repo", "")))
    return Out(f"{t['count']} note(s): " + (", ".join(f"{k} {v}" for k, v in sorted(t["by_tag"].items())) or "none") + "\n"
               + "\n".join(f"{x['file']}:{x['line']} {x['text']}" for x in t["items"][:30]), {"todos": t})


@TOOLS.tool("check_dependencies", "Dependencies that are not pinned (requirements*.txt, package.json), and ones "
            "listed twice.", Level.READ, {"repo": REPO})
def check_dependencies(d: DevDesk, a: dict[str, Any]) -> Out:
    r = d.dependencies(d.repo(a.get("repo", "")))
    findings = [finding(f"{x['file']}: {x['package']} is {x['why']} ({x['spec']})", action="pin the version you tested",
                        entity=x["package"]) for x in r["loose"]]
    findings += [finding(f"{x['package']} is listed twice ({x['file']} and {x['also']})", action="keep one")
                 for x in r["duplicates"]]
    return Out(f"{r['checked']} Python package(s) checked; {len(r['loose'])} loose, {len(r['duplicates'])} twice.",
               {"dependencies": r}, findings)


@TOOLS.tool("explain_test_failures", "Read a JUnit XML test report and group the failures by cause.", Level.READ,
            {"path": {"type": "string"}}, ["path"])
def explain_test_failures(d: DevDesk, a: dict[str, Any]) -> Out:
    r = d.test_report(_path(d, a["path"]))
    findings = [finding(f"{len(g['tests'])} test(s) fail with {g['type']}: {g['message']}", "\n".join(g["tests"][:10]),
                        action="fix the cause once, then rerun", severity="high") for g in r["groups"]]
    return Out(f"{r['tests']} tests: {r['passed']} passed, {r['failed']} failed ({len(r['groups'])} cause(s)), "
               f"{r['skipped']} skipped.", {"report": r}, findings)


@TOOLS.tool("suggest_next_version", "The next semantic version from the commits since the last tag.", Level.READ,
            {"repo": REPO, "since": {"type": "string"}})
def suggest_next_version(d: DevDesk, a: dict[str, Any]) -> Out:
    v = d.next_version(d.repo(a.get("repo", "")), a.get("since", ""))
    return Out(f"{v['commits']} commit(s) since {v['current']}: " + (f"next is {v['next']} ({v['bump']})." if v["next"]
                                                                     else "nothing that needs a release."),
               {"version": {k: v[k] for k in ("current", "next", "bump", "commits")}})


@TOOLS.tool("write_release_notes", "Write release notes from the conventional commits since the last tag (to this "
            "pack's folder, not the repo).", Level.DRAFT,
            {"repo": REPO, "since": {"type": "string"}, "until": {"type": "string"}, "version": {"type": "string"}})
def write_release_notes(d: DevDesk, a: dict[str, Any]) -> Out:
    r = d.release_notes(d.repo(a.get("repo", "")), a.get("since", ""), a.get("until") or "HEAD", a.get("version", ""))
    c = r["counts"]
    return Out(f"Release notes for {r['version']}: {c['breaking']} breaking, {c['feat']} feature(s), {c['fix']} fix(es) "
               f"from {r['commits']} commit(s) ({r['left_out']} housekeeping left out). File: {r['path']}", {"notes": r})


@TOOLS.tool("triage_issues", "Sort a GitHub issues export (gh issue list --json number,title,body,labels,updatedAt): "
            "label suggestions, likely duplicates, stale issues. Nothing is labelled.", Level.DRAFT,
            {"path": {"type": "string"}, "as_of": {"type": "string"}}, ["path"])
def triage_issues(d: DevDesk, a: dict[str, Any]) -> Out:
    r = d.triage(_path(d, a["path"]), a.get("as_of", ""))
    findings = [finding(f"#{x['a']} and #{x['b']} look like the same issue ({x['similarity']:.0%})",
                        action="close one as a duplicate") for x in r["duplicates"]]
    return Out(f"{r['issues']} issues: {len(r['suggest'])} unlabelled, {len(r['duplicates'])} likely duplicate pair(s), "
               f"{len(r['stale'])} stale. Report: {r['path']}", {"triage": r}, findings)


TEMPLATE = AgentTemplate(
    id="dev_assistant", name="Dev Assistant", worker_type=EXT,
    mission="Take the chores around the code off a developer's hands: every change reviewed for secrets and "
            "leftovers before it goes out, failing tests explained, dependencies pinned, issues sorted, release "
            "notes and the next version ready.",
    handles=["change review", "test failures", "dependencies", "TODOs", "issue triage", "release notes"],
    tools=TOOLS.names() + ["read_file", "list_dir", "git_status", "git_log", "git_diff"], max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="weekdays", at="09:00")], memory_schema={"repo": "conventions, what reviewers care about"},
    default_workflow="pre_push_review", artifacts=["reviews", "release notes", "triage reports"],
    setup_questions=["Default repository", "Big change threshold", "Stale issue age"], extension_id=EXT)

WORKFLOWS = [
    Workflow(id="pre_push_review", name="Review before pushing", description="The uncommitted work: secrets, "
             "leftovers, missing tests, size; then TODOs.", template_id=TEMPLATE.id, trigger=Schedule(every="weekdays", at="17:00"),
             build_steps=lambda meta: [step("Review the change", "review_changes",
                                            {"repo": meta["repo"]} if meta.get("repo") else {}),
                                       step("Dependencies", "check_dependencies",
                                            {"repo": meta["repo"]} if meta.get("repo") else {})],
             goal="Review the change before it is pushed"),
    Workflow(id="release_prep", name="Prepare a release", description="The next version and the release notes.",
             template_id=TEMPLATE.id, trigger=Schedule(every="monthly", at="10:00", day=1),
             build_steps=lambda meta: [step("Next version", "suggest_next_version"),
                                       step("Release notes", "write_release_notes")],
             goal="Prepare the release"),
]

SKILLS = [Skill("developer.rules", "How this pack works", always=True, text=(
    "You help a developer with the work around the code. Read the repository with this pack's tools and the git_* "
    "tools; never commit, push, tag, merge or label on your own - suggest the command and let the developer run it. "
    "A secret found in a change is urgent: say to remove it and rotate it, because history keeps it. Commit "
    "messages follow conventional commits (feat, fix, docs, chore...; ! or BREAKING CHANGE for breaking)."))]

INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:my\s+|the\s+)?(?:changes|diff|pull\s+request|pr)\b", workflow="pre_push_review",
         reply="Reviewing the change: secrets, leftovers, tests, size.",
         verb=r"\b(?:please\s+)?(?:review|check|look\s+over|go\s+over)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:release\s+notes|changelog|next\s+version|a\s+release)\b", workflow="release_prep",
         reply="Working out the next version and writing the release notes.",
         verb=r"\b(?:please\s+)?(?:write|prepare|draft|make|get\s+ready|cut)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:failing\s+tests|test\s+failures|test\s+report|the\s+ci)\b",
         reply="Reading the test report.", verb=r"\b(?:please\s+)?(?:explain|check|look\s+at|read)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:the\s+)?issues\b", reply="Sorting the issues.",
         verb=r"\b(?:please\s+)?(?:triage|sort|go\s+through|label)\b"),
]


class DeveloperExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = DevDesk
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Developer"
    DEFAULTS = {"repo": "", "big_change": 400, "stale_days": 60, "duplicate_ratio": 0.8}
    ROLE = "a developer's assistant"
    ACTIONS = [("Review changes", "pre_push_review"), ("Prepare release", "release_prep")]
    NOTES = "Every tool is READ or DRAFT; nothing is committed, pushed, tagged or labelled."
    EXAMPLES = [("review my changes", "pre_push_review"), ("please write the release notes", "release_prep"),
                ("check the failing tests", ""), ("triage the issues", "")]

    def context_line(self, d: DevDesk) -> str:
        return f"Developer: default repo {d.cfg.get('repo') or '(none set)'} · big change over {d.cfg.get('big_change') or 400} lines"

    def snapshot(self, d: DevDesk) -> dict[str, Any]:
        notes = sorted(os.listdir(d.folder("Release notes")))[-10:]
        triage = sorted(os.listdir(d.folder("Triage")))[-10:]
        return {"title": "Developer", "meta": d.cfg.get("repo") or "No default repo set",
                "numbers": [("Release notes", len(notes)), ("Triage reports", len(triage))],
                "lists": [("Release notes", [(n, os.path.join(d.root, "Release notes", n)) for n in notes]),
                          ("Triage", [(n, os.path.join(d.root, "Triage", n)) for n in triage]), ("", [])]}
