"""Bộ tool nội bộ của agent (luôn có): tra tài liệu dự án, sandbox, skill, mở nguồn ngoài.

Tool của nguồn ngoài (MCP) không nằm ở đây – chỉ nạp khi agent mở nguồn (loop/sources.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool

from app.services.agent.mcp.catalog import SourceCatalog
from app.services.agent.sandbox import SandboxManager
from app.services.agent.skills import Skill
from app.services.agent.tools.base import access_of, tag
from app.services.agent.tools.sandbox_tools import make_sandbox_tools
from app.services.agent.tools.search_docs import SearchDocs, make_search_tool
from app.services.agent.tools.skill_tool import make_skill_tool
from app.services.agent.tools.source_tool import make_source_tool
from app.services.query.deps import Deps


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
                  extra: list[BaseTool] | None = None, catalog: SourceCatalog | None = None) -> ToolSet:
    """cfg = toàn bộ agent.yaml. extra: tool luôn có thêm (đã gắn access; test dùng). catalog: có → thêm tool mở nguồn."""
    tools = [tag(make_search_tool(SearchDocs(deps), manager, cfg["agent"]), "read")]
    tools += [tag(t, "sandbox") for t in make_sandbox_tools(manager, cfg["sandbox"])]
    tools.append(tag(make_skill_tool(skills), "read"))
    if catalog is not None:
        tools.append(tag(make_source_tool(catalog), "read"))
    names = {t.name for t in tools}
    for t in extra or []:
        if t.name in names:
            raise ValueError(f"Tên tool trùng tool có sẵn: {t.name}")
        tools.append(tag(t, (t.metadata or {}).get("access", "write")))
        names.add(t.name)
    return ToolSet(tools)
