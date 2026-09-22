"""IAgentExtension: the contract every industry pack implements.

A pack is a folder under `extensions/` with a `manifest.json` and a
package that exposes `create_extension()`. The platform discovers it,
and on enable asks it for its parts through the `get_*` methods below.
It never reaches into the pack any other way, which is what makes a
pack removable: delete the folder and nothing else refers to it.

Every getter has a harmless default, so a pack implements only what it
has. A minimal pack is a manifest, one template and one skill.
"""
from __future__ import annotations

import json
import os
from abc import ABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from agents.core import BaseTool, BaseWorker, Task

from .hooks import (
    AgentTemplate, DashboardModule, DocumentType, IntegrationSpec, IntentRule,
    PermissionProfile, Skill, Workflow,
)

if TYPE_CHECKING:
    from .registry import ExtensionContext

MANIFEST_FILE = "manifest.json"


@dataclass
class ExtensionManifest:
    id: str
    name: str
    version: str = "1.0"
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    optional_integrations: list[str] = field(default_factory=list)
    activation_phrases: list[str] = field(default_factory=list)
    offer: list[str] = field(default_factory=list)          # "This adds: ..." bullets

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtensionManifest":
        missing = [k for k in ("id", "name") if not str(data.get(k) or "").strip()]
        if missing:
            raise ValueError(f"manifest is missing {', '.join(missing)}")
        ident = str(data["id"]).strip()
        if not ident.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"manifest id {ident!r} must be letters, digits, - or _")
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def load(cls, folder: str) -> "ExtensionManifest":
        with open(os.path.join(folder, MANIFEST_FILE), encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class IAgentExtension(ABC):
    """Subclass, set `manifest`, override the getters you need."""

    manifest: ExtensionManifest

    @property
    def id(self) -> str:
        return self.manifest.id

    # -- parts -------------------------------------------------------------------

    def get_agent_templates(self) -> list[AgentTemplate]:
        return []

    def get_tools(self, ctx: "ExtensionContext") -> list[BaseTool]:
        return []

    def get_workers(self, ctx: "ExtensionContext") -> list[BaseWorker]:
        return []

    def get_skills(self) -> list[Skill]:
        return []

    def get_workflows(self) -> list[Workflow]:
        return []

    def get_permissions(self) -> PermissionProfile:
        return PermissionProfile()

    def get_integrations(self) -> list[IntegrationSpec]:
        return []

    def get_dashboard_modules(self) -> list[DashboardModule]:
        return []

    def get_document_types(self) -> list[DocumentType]:
        return []

    def get_validation_rules(self) -> dict[str, Callable[..., Any]]:
        """{tool_name: rule} for the RuleVerifier - see agents/verification."""
        return {}

    def get_intent_rules(self) -> list[IntentRule]:
        return []

    def get_context_lines(self, task: Task) -> list[str]:
        """Facts for the prompts of this pack's tasks (client, period...)."""
        return []

    def settings_defaults(self) -> dict[str, Any]:
        return {}

    def scheduled_jobs(self, ctx: "ExtensionContext") -> list[Any]:
        """ScheduledJobs to register on enable (see agents/manager/scheduler)."""
        return []

    # -- lifecycle -----------------------------------------------------------------

    def on_install(self, ctx: "ExtensionContext") -> None: ...
    def on_enable(self, ctx: "ExtensionContext") -> None: ...
    def on_configure(self, ctx: "ExtensionContext", settings: dict[str, Any]) -> None: ...
    def on_disable(self, ctx: "ExtensionContext") -> None: ...
    def on_uninstall(self, ctx: "ExtensionContext") -> None: ...

    # -- ownership ---------------------------------------------------------------------

    def owns(self, task: Task, worker_types: Optional[set[str]] = None) -> bool:
        meta = task.metadata or {}
        if meta.get("extension") == self.id:
            return True
        return bool(worker_types) and task.worker_type in (worker_types or set())
