"""Desktop control: ask apps in their own language, not by clicking.

    from agents.control import control
    await control().run("spotify", "next")
    await control().run("vscode", "chat", {"project": "clicky-windows-main",
                                            "prompt": "explain the agent system"})

`model.Tier` is the ladder every action declares itself on - app API,
deep link, system key, background UI, on-screen input, vision - and the
registry reports which rung did the work, so the user can see that "next
song" never touched their screen.
"""
from .model import Action, Outcome, Param, Refused, Risk, Tier, Unavailable
from .registry import AppControl, control, set_control

__all__ = ["Action", "AppControl", "Outcome", "Param", "Refused", "Risk", "Tier", "Unavailable",
           "control", "set_control"]
