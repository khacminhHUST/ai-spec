"""Gom tool theo config, gắn nhãn access (read | sandbox | write) vào tool.metadata["access"].

Tool `write` (ghi/đẩy dữ liệu ra hệ thống ngoài) → bắt buộc người dùng xác nhận (loop/confirm.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool

from app.services.agent.sandbox import SandboxManager
from app.services.agent.skills import Skill
from app.services.agent.tools.sandbox_tools import make_sandbox_tools
from app.services.agent.tools.search_docs import SearchDocs, make_search_tool
from app.services.agent.tools.skill_tool import make_skill_tool
from app.services.query.deps import Deps

ACCESS = ("read", "sandbox", "write")


def tag(tool: BaseTool, access: str) -> BaseTool:
    tool.metadata = {**(tool.metadata or {}), "access": access if access in ACCESS else "write"}
    return tool


def access_of(tool: BaseTool) -> str:
    """Không gắn nhãn → coi là write (an toàn)."""
    a = (tool.metadata or {}).get("access")
    return a if a in ACCESS else "write"


@dataclass
class ToolSet:
    tools: list[BaseTool]

    @property
    def names(self) -> list[str]:
        return [t.name for t in self.tools]

    @property
    def write_tools(self) -> set[str]:
        return {t.name for t in self.tools if access_of(t) == "write"}

    def get(self, name: str) -> BaseTool | None:
        return next((t for t in self.tools if t.name == name), None)


def build_toolset(deps: Deps, manager: SandboxManager, cfg: dict[str, Any], skills: dict[str, Skill],
                  mcp_tools: list[BaseTool] | None = None) -> ToolSet:
    """cfg = toàn bộ agent.yaml. mcp_tools đã gắn access bởi mcp/client.py."""
    tools = [tag(make_search_tool(SearchDocs(deps), manager, cfg["agent"]), "read")]
    tools += [tag(t, "sandbox") for t in make_sandbox_tools(manager, cfg["sandbox"])]
    tools.append(tag(make_skill_tool(skills), "read"))
    names = {t.name for t in tools}
    for t in mcp_tools or []:
        if t.name in names:
            raise ValueError(f"Tên MCP tool trùng tool có sẵn: {t.name}")
        tools.append(tag(t, (t.metadata or {}).get("access", "write")))
        names.add(t.name)
    return ToolSet(tools)
