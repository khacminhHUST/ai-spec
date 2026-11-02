"""Skill nạp theo nhu cầu (docs/agent-harness.md mục 5)."""
from app.services.agent.skills.loader import Skill, load_skills, skills_prompt

__all__ = ["Skill", "load_skills", "skills_prompt"]
