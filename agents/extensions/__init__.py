"""Industry extensions: the framework (generic; knows no industry).

    from agents.extensions import ExtensionRegistry
    reg = ExtensionRegistry(manager, state_path=".../extensions.json")
    reg.discover()                 # finds packs under the top-level `extensions` package
    reg.enable("accounting")       # tools, workers, workflows, rules, jobs plugged in
    reg.disable("accounting")      # ...and exactly those removed again

See docs/agents/EXTENSIONS.md for the design, and extensions/accounting
for the first pack, which is the blueprint for every later one.
"""
from .base import ExtensionManifest, IAgentExtension
from .hooks import (
    INTEGRATION_TIERS, AgentTemplate, ContextProvider, DashboardModule, DocumentType,
    FolderWatch, IntegrationSpec, IntentRule, PermissionProfile, Schedule, Skill, Trigger,
    Workflow,
)
from .registry import ExtensionContext, ExtensionRegistry, WorkflowPlanTemplate

__all__ = [
    "ExtensionManifest", "IAgentExtension", "ExtensionContext", "ExtensionRegistry",
    "WorkflowPlanTemplate", "AgentTemplate", "ContextProvider", "DashboardModule",
    "DocumentType", "FolderWatch", "IntegrationSpec", "IntentRule", "PermissionProfile",
    "Schedule", "Skill", "Trigger", "Workflow", "INTEGRATION_TIERS",
]
