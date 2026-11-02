"""load_skill: trả nội dung SKILL.md của 1 skill (danh sách skill đã có trong system prompt)."""
from __future__ import annotations

from langchain.tools import tool
from langchain_core.tools import BaseTool

from app.services.agent.skills import Skill
from app.services.agent.tools.base import fail, ok

DESC = """Nạp hướng dẫn chi tiết của 1 skill (quy cách xuất file, cách kiểm tra file…) theo tên trong danh sách skill.
Dùng TRƯỚC khi tạo file thuộc phạm vi skill (VD xuất Excel → load_skill(name="xlsx-export"); xong file → load_skill(name="file-check")).
Không gọi lại skill đã nạp trong lượt này."""


def make_skill_tool(skills: dict[str, Skill]) -> BaseTool:
    @tool("load_skill", description=DESC)
    def load_skill(name: str) -> str:
        s = skills.get((name or "").strip())
        if not s:
            return fail(f"Không có skill '{name}'", available=list(skills))
        return ok(name=s.name, content=s.body, files=s.files)

    return load_skill
