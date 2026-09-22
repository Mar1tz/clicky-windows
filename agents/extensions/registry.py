"""ExtensionRegistry: install, enable, configure, disable, uninstall.

The one place that knows a pack is plugged in. Enabling a pack registers
its tools, workers, plan templates, verifier rules, intent rules and
scheduled jobs with the running AgentManager — and records each one, so
that disabling removes exactly those and nothing else. A pack switched
off leaves no tool a model could call, no rule a classifier could match,
no job a scheduler could fire.

State (installed / enabled / settings per pack) is one JSON file in the
app's data directory. Pack DATA (a client's books, drafts) lives in the
pack's own folders and survives uninstall unless `purge=True`: removing
a feature must never delete somebody's work.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import pkgutil
import re
import shutil
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from agents.core import EventKind, Intent, Step, Task, TaskKind

from .base import MANIFEST_FILE, ExtensionManifest, IAgentExtension
from .hooks import AgentTemplate, DashboardModule, IntentRule, Skill, Workflow

if TYPE_CHECKING:
    from agents.manager import AgentManager

log = logging.getLogger("agents.extensions")

DEFAULT_PACKAGE = "extensions"


# -- the handle a pack gets ---------------------------------------------------------

@dataclass
class ExtensionContext:
    """What a pack may touch while it is being enabled or configured."""
    extension_id: str
    manager: Optional["AgentManager"]
    data_dir: str                                   # the pack's own folder for its data
    settings: dict[str, Any] = field(default_factory=dict)
    save: Callable[[], None] = lambda: None

    @property
    def workspace_root(self) -> str:
        return getattr(self.manager, "workspace_root", "") or self.data_dir


@dataclass
class _Registered:
    """Everything one enable() added, for an exact disable()."""
    tools: list[str] = field(default_factory=list)
    workers: list[str] = field(default_factory=list)
    plan_templates: list[Any] = field(default_factory=list)
    verifier_rules: list[str] = field(default_factory=list)
    jobs: list[str] = field(default_factory=list)


class WorkflowPlanTemplate:
    """Adapter: a pack's Workflow as a planner ProjectTemplate.

    Matches only a task whose metadata names the workflow, so a pack can
    never hijack a goal it was not explicitly routed. Duck-typed to
    agents.planning.templates.ProjectTemplate (name / matches / steps)."""

    def __init__(self, workflow: Workflow) -> None:
        self.workflow = workflow
        self.name = f"{workflow.extension_id}:{workflow.id}"

    def matches(self, spec) -> bool:
        meta = (getattr(spec, "extras", None) or {}).get("metadata") or {}
        return meta.get("workflow") == self.workflow.id and \
            meta.get("extension") == self.workflow.extension_id

    def steps(self, spec) -> list[Step]:
        meta = dict((spec.extras or {}).get("metadata") or {})
        meta.setdefault("workspace", (spec.extras or {}).get("workspace", ""))
        return self.workflow.steps(meta)


# -- the registry ----------------------------------------------------------------------

class ExtensionRegistry:
    def __init__(self, manager: Optional["AgentManager"] = None, *,
                 state_path: Optional[str] = None, data_root: Optional[str] = None,
                 package: str = DEFAULT_PACKAGE,
                 factories: Optional[dict[str, Callable[[], IAgentExtension]]] = None) -> None:
        self.manager = manager
        self.state_path = state_path
        self.data_root = data_root or (os.path.dirname(state_path) if state_path else "")
        self.package = package
        self._factories: dict[str, Callable[[], IAgentExtension]] = dict(factories or {})
        self._manifests: dict[str, ExtensionManifest] = {}
        self._enabled: dict[str, IAgentExtension] = {}
        self._registered: dict[str, _Registered] = {}
        self._state: dict[str, dict[str, Any]] = self._load_state()
        self._lock = threading.RLock()
        if factories:
            for ext_id, make in factories.items():
                try:
                    self._manifests[ext_id] = make().manifest
                except Exception:  # noqa: BLE001
                    log.exception("extension %s factory failed", ext_id)

    # -- discovery --------------------------------------------------------------------

    def discover(self) -> list[ExtensionManifest]:
        """Find packs: every sub-package of `extensions` with a manifest
        and a create_extension(). A broken pack is logged and skipped - one
        bad folder must not take the others (or the app) down."""
        try:
            root = importlib.import_module(self.package)
        except ImportError:
            return list(self._manifests.values())
        for info in pkgutil.iter_modules(getattr(root, "__path__", [])):
            if not info.ispkg or info.name in self._factories:
                continue
            try:
                mod = importlib.import_module(f"{self.package}.{info.name}")
                make = getattr(mod, "create_extension")
                folder = os.path.dirname(mod.__file__ or "")
                manifest = ExtensionManifest.load(folder) if os.path.isfile(
                    os.path.join(folder, MANIFEST_FILE)) else make().manifest
            except Exception:  # noqa: BLE001
                log.exception("extension %s could not be loaded", info.name)
                continue
            self._factories[manifest.id] = make
            self._manifests[manifest.id] = manifest
        return list(self._manifests.values())

    def available(self) -> list[dict[str, Any]]:
        if not self._manifests:
            self.discover()
        out = []
        for ext_id, m in sorted(self._manifests.items()):
            st = self._state.get(ext_id, {})
            out.append({**m.to_dict(), "installed": bool(st.get("installed")),
                        "enabled": ext_id in self._enabled})
        return out

    def manifest(self, ext_id: str) -> ExtensionManifest:
        if ext_id not in self._manifests:
            self.discover()
        try:
            return self._manifests[ext_id]
        except KeyError:
            raise KeyError(f"no extension {ext_id!r}; available: {', '.join(self._manifests) or 'none'}") from None

    # -- lifecycle ------------------------------------------------------------------------

    def install(self, ext_id: str) -> ExtensionManifest:
        m = self.manifest(ext_id)
        with self._lock:
            st = self._state.setdefault(ext_id, {})
            if not st.get("installed"):
                st["installed"] = True
                st.setdefault("settings", {})
                ext = self._factories[ext_id]()
                st["settings"] = {**ext.settings_defaults(), **st["settings"]}
                ext.on_install(self._context(ext_id))
                self._save_state()
        self._log(f"{m.name} extension installed", ext_id)
        return m

    def enable(self, ext_id: str) -> IAgentExtension:
        """Install if needed, then plug everything in. Idempotent."""
        with self._lock:
            if ext_id in self._enabled:
                return self._enabled[ext_id]
            self.install(ext_id)
            ext = self._factories[ext_id]()
            ctx = self._context(ext_id)
            reg = _Registered()
            try:
                self._plug(ext, ctx, reg)
                ext.on_enable(ctx)
            except Exception:
                self._unplug(reg)
                raise
            self._enabled[ext_id] = ext
            self._registered[ext_id] = reg
            self._state[ext_id]["enabled"] = True
            self._save_state()
        self._log(f"{ext.manifest.name} extension enabled", ext_id,
                  tools=len(reg.tools), templates=len(ext.get_agent_templates()))
        return ext

    def disable(self, ext_id: str) -> bool:
        with self._lock:
            ext = self._enabled.pop(ext_id, None)
            reg = self._registered.pop(ext_id, None)
            if ext is None:
                if ext_id in self._state:
                    self._state[ext_id]["enabled"] = False
                    self._save_state()
                return False
            try:
                ext.on_disable(self._context(ext_id))
            except Exception:  # noqa: BLE001 - disabling must always succeed
                log.exception("extension %s on_disable failed", ext_id)
            if reg:
                self._unplug(reg)
            self._state.setdefault(ext_id, {})["enabled"] = False
            self._save_state()
        self._log(f"{ext.manifest.name} extension disabled", ext_id)
        return True

    def uninstall(self, ext_id: str, *, purge: bool = False) -> None:
        """Disable and forget the pack. Its data folder is kept unless
        `purge` - client books are not a cache."""
        self.disable(ext_id)
        with self._lock:
            if ext_id in self._factories:
                try:
                    self._factories[ext_id]().on_uninstall(self._context(ext_id))
                except Exception:  # noqa: BLE001
                    log.exception("extension %s on_uninstall failed", ext_id)
            self._state.pop(ext_id, None)
            self._save_state()
        if purge:
            shutil.rmtree(self._data_dir(ext_id), ignore_errors=True)
        self._log(f"{ext_id} extension uninstalled", ext_id)

    def configure(self, ext_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.install(ext_id)
            st = self._state[ext_id]
            st["settings"] = {**st.get("settings", {}), **(settings or {})}
            self._save_state()
            ext = self._enabled.get(ext_id)
            if ext is not None:
                ext.on_configure(self._context(ext_id), st["settings"])
                self._refresh_jobs(ext_id)
            return dict(st["settings"])

    def settings(self, ext_id: str) -> dict[str, Any]:
        return dict(self._state.get(ext_id, {}).get("settings", {}))

    def restore(self) -> list[str]:
        """Re-enable what was enabled last session. Called once at startup."""
        if not self._manifests:
            self.discover()
        restored = []
        for ext_id, st in list(self._state.items()):
            if st.get("enabled") and ext_id in self._factories:
                try:
                    self.enable(ext_id)
                    restored.append(ext_id)
                except Exception:  # noqa: BLE001
                    log.exception("extension %s failed to re-enable", ext_id)
        return restored

    # -- queries the platform makes -----------------------------------------------------------

    def enabled(self) -> list[IAgentExtension]:
        return list(self._enabled.values())

    def is_enabled(self, ext_id: str) -> bool:
        return ext_id in self._enabled

    def get(self, ext_id: str) -> Optional[IAgentExtension]:
        return self._enabled.get(ext_id)

    def owner_of(self, task: Task) -> Optional[IAgentExtension]:
        for ext_id, ext in self._enabled.items():
            if ext.owns(task, set(self._registered.get(ext_id, _Registered()).workers)):
                return ext
        return None

    def template(self, template_id: str) -> Optional[AgentTemplate]:
        for ext in self._enabled.values():
            for t in ext.get_agent_templates():
                if t.id == template_id:
                    t.extension_id = ext.id
                    return t
        return None

    def templates(self) -> list[AgentTemplate]:
        out = []
        for ext in self._enabled.values():
            for t in ext.get_agent_templates():
                t.extension_id = ext.id
                out.append(t)
        return out

    def workflow(self, workflow_id: str) -> Optional[Workflow]:
        for ext in self._enabled.values():
            for w in ext.get_workflows():
                if w.id == workflow_id:
                    w.extension_id = ext.id
                    return w
        return None

    def match_intent(self, text: str) -> Optional[Intent]:
        """The first enabled pack's rule that matches, as an Intent with the
        routing metadata the manager needs. None = let the classifier decide.

        A question is never a pack's task ("what is a month-end?" is for the
        conversation to answer), so questions are left to the classifier."""
        if _QUESTION.match(text or ""):
            return None
        for ext in self._enabled.values():
            for rule in ext.get_intent_rules():
                found = rule.match(text)
                if found is None:
                    continue
                template = self.template(rule.template_id)
                if template is None:
                    continue
                meta = {"extension": ext.id, "template": template.id, **found}
                if rule.workflow_id:
                    meta["workflow"] = rule.workflow_id
                workspace = self._workspace_for(ext, meta)
                if workspace:
                    meta["workspace"] = workspace
                title = _title(text)
                return Intent(is_task=True, confidence=0.95, title=title, goal=text.strip(),
                              kind=rule.kind, worker_type=template.worker_type,
                              reply=rule.reply or f"Understood. The {template.name} is on it.",
                              metadata=meta)
        return None

    def skills_for(self, task: Task) -> list[str]:
        ext = self.owner_of(task)
        if ext is None:
            return []
        role = (task.metadata or {}).get("template", "")
        out = []
        for s in ext.get_skills():
            if s.always or not s.roles or role in s.roles:
                out.append(f"## {s.title}\n{s.text.strip()}")
        return out

    def context_lines_for(self, task: Task) -> list[str]:
        ext = self.owner_of(task)
        if ext is None:
            return []
        try:
            return [l for l in ext.get_context_lines(task) if l]
        except Exception:  # noqa: BLE001 - a context line is never worth failing a task
            log.exception("extension %s context lines failed", ext.id)
            return []

    def dashboard_modules(self) -> list[DashboardModule]:
        mods = []
        for ext in self._enabled.values():
            for m in ext.get_dashboard_modules():
                m.extension_id = ext.id
                mods.append(m)
        return sorted(mods, key=lambda m: m.order)

    def activation_offer(self, text: str) -> Optional[dict[str, Any]]:
        """If `text` sounds like "I am an accountant" and that pack is not on,
        what to offer. The voice layer turns this into a question."""
        if not self._manifests:
            self.discover()
        low = (text or "").lower()
        for ext_id, m in self._manifests.items():
            if ext_id in self._enabled:
                continue
            for phrase in m.activation_phrases:
                if re.search(r"\b" + re.escape(phrase.lower()) + r"\b", low):
                    return {"id": ext_id, "name": m.name, "offer": list(m.offer)}
        return None

    def voice_hints(self, *, include_off: bool = True) -> list[str]:
        """Lines for Tora's context: which packs are on (and what to delegate
        to them), and - unless include_off is False - which exist but are
        off. The per-turn context passes False: an idle pack is not worth
        tokens on every turn; Tora asks with list_extensions when the user
        says what they do."""
        if not self._manifests:
            self.discover()
        lines = []
        for ext_id, m in sorted(self._manifests.items()):
            if ext_id in self._enabled:
                roles = ", ".join(t.name for t in self._enabled[ext_id].get_agent_templates())
                lines.append(f"{m.name} extension is ON (agents: {roles}). Delegate {m.name.lower()} "
                             f"jobs to it with delegate_task.")
            elif include_off and m.activation_phrases:
                lines.append(f"{m.name} extension is available but off. If the user says something "
                             f"like '{m.activation_phrases[0]}', offer to enable it: "
                             + "; ".join(m.offer[:6])
                             + f". To enable, call manage_agents(action='enable_extension', id='{ext_id}').")
        return lines

    # -- plugging ------------------------------------------------------------------------------

    def _plug(self, ext: IAgentExtension, ctx: ExtensionContext, reg: _Registered) -> None:
        mgr = self.manager
        perms = ext.get_permissions()
        for tool in ext.get_tools(ctx):
            if tool.name in perms.sensitive:
                tool.sensitive = True                           # type: ignore[attr-defined]
            if tool.name in perms.always_ask:
                tool.always_ask = True                          # type: ignore[attr-defined]
            if mgr is not None:
                if tool.name in mgr.tools and tool.name not in reg.tools:
                    raise ValueError(f"extension {ext.id} tool {tool.name!r} clashes with an existing tool")
                mgr.tools.register(tool)
            reg.tools.append(tool.name)
        for worker in ext.get_workers(ctx):
            if mgr is not None and hasattr(mgr.workers, "register"):
                mgr.workers.register(worker)
            reg.workers.append(worker.worker_type)
        planner = _template_planner(mgr.planner) if mgr is not None else None
        for wf in ext.get_workflows():
            wf.extension_id = ext.id
            adapter = WorkflowPlanTemplate(wf)
            if planner is not None:
                planner.templates.insert(0, adapter)
            reg.plan_templates.append(adapter)
        rules = _rule_table(mgr.verifier) if mgr is not None else None
        for name, rule in ext.get_validation_rules().items():
            if rules is not None:
                rules[name] = rule
            reg.verifier_rules.append(name)
        sched = getattr(mgr, "scheduler", None) if mgr is not None else None
        for job in ext.scheduled_jobs(ctx):
            job.owner = ext.id
            if sched is not None:
                sched.add(job)
            reg.jobs.append(job.id)

    def _unplug(self, reg: _Registered) -> None:
        mgr = self.manager
        if mgr is None:
            return
        for name in reg.tools:
            if hasattr(mgr.tools, "unregister"):
                mgr.tools.unregister(name)
        for wt in reg.workers:
            if hasattr(mgr.workers, "unregister"):
                mgr.workers.unregister(wt)
        planner = _template_planner(mgr.planner)
        if planner is not None:
            planner.templates[:] = [t for t in planner.templates if t not in reg.plan_templates]
        rules = _rule_table(mgr.verifier)
        if rules is not None:
            for name in reg.verifier_rules:
                rules.pop(name, None)
        sched = getattr(mgr, "scheduler", None)
        if sched is not None:
            for job_id in reg.jobs:
                sched.remove(job_id)

    def _refresh_jobs(self, ext_id: str) -> None:
        """Settings changed (a new agent, a new schedule): swap the pack's jobs."""
        ext, reg = self._enabled.get(ext_id), self._registered.get(ext_id)
        sched = getattr(self.manager, "scheduler", None)
        if ext is None or reg is None or sched is None:
            return
        for job_id in reg.jobs:
            sched.remove(job_id)
        reg.jobs.clear()
        for job in ext.scheduled_jobs(self._context(ext_id)):
            job.owner = ext_id
            sched.add(job)
            reg.jobs.append(job.id)

    def _workspace_for(self, ext: IAgentExtension, meta: dict[str, Any]) -> str:
        resolver = getattr(ext, "workspace_for", None)
        if resolver is None:
            return ""
        try:
            return resolver(meta, self._context(ext.id)) or ""
        except Exception:  # noqa: BLE001
            log.exception("extension %s could not resolve a workspace", ext.id)
            return ""

    # -- state ------------------------------------------------------------------------------------

    def _context(self, ext_id: str) -> ExtensionContext:
        st = self._state.setdefault(ext_id, {})
        settings = st.setdefault("settings", {})
        return ExtensionContext(extension_id=ext_id, manager=self.manager,
                                data_dir=self._data_dir(ext_id), settings=settings,
                                save=self._save_state)

    def _data_dir(self, ext_id: str) -> str:
        base = self.data_root or os.path.join(os.path.expanduser("~"), ".tora")
        path = os.path.join(base, "extensions", ext_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _load_state(self) -> dict[str, dict[str, Any]]:
        if not self.state_path or not os.path.isfile(self.state_path):
            return {}
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            log.exception("extension state unreadable; starting clean")
            return {}

    def _save_state(self) -> None:
        if not self.state_path:
            return
        tmp = self.state_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, indent=2, default=str)
            os.replace(tmp, self.state_path)
        except OSError:
            log.exception("could not save extension state")

    def _log(self, message: str, ext_id: str, **data: Any) -> None:
        log.info(message)
        events = getattr(self.manager, "events", None)
        if events is not None:
            events.emit(EventKind.LOG, message, data={"extension": ext_id, **data})


_QUESTION = re.compile(r"^\s*(?:what|how|why|when|where|who|which|whose|explain|tell\s+me\s+about|is|are|was|"
                       r"were|does|do|did|should|would\s+it|could\s+it|can\s+it)\b", re.I)


def _template_planner(planner: Any) -> Any:
    """The TemplatePlanner inside whatever planner the manager runs."""
    for candidate in (planner, getattr(planner, "fallback", None)):
        if candidate is not None and isinstance(getattr(candidate, "templates", None), list):
            return candidate
    return None


def _rule_table(verifier: Any) -> Optional[dict[str, Any]]:
    """The RuleVerifier's rule dict inside whatever verifier the manager runs."""
    for candidate in (verifier, getattr(verifier, "rules", None)):
        rules = getattr(candidate, "rules", None)
        if isinstance(rules, dict):
            return rules
    return None


def _title(text: str) -> str:
    clause = re.split(r"[.;\n]", (text or "").strip(), maxsplit=1)[0].strip()
    clause = clause[:1].upper() + clause[1:]
    return clause[:57] + "…" if len(clause) > 60 else clause
