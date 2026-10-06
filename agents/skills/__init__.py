"""Skills: how the user likes something done, as text. See library.py and
docs/agents/SKILLS.md."""
from .library import Skill, SkillError, SkillLibrary, parse, slug, starter_dir, user_dir

__all__ = ["Skill", "SkillError", "SkillLibrary", "parse", "slug", "starter_dir", "user_dir"]
