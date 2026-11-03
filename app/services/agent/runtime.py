"""Khởi tạo các thành phần dùng chung của Agent Harness: sandbox, skills, bộ tool nội bộ, danh mục nguồn ngoài, model,
và vòng lặp agent (dựng 1 lần – tool của nguồn ngoài được nạp động theo user + nguồn đã mở, xem loop/sources.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.services.agent.config import agent_cfg, mcp_cfg
from app.services.agent.loop.agent import build_agent, get_agent_model
from app.services.agent.mcp import load_mcp_tools
from app.services.agent.mcp.catalog import SourceCatalog
from app.services.agent.sandbox import SandboxManager, build_manager
from app.services.agent.skills import Skill, load_skills
from app.services.agent.tools.registry import ToolSet, build_toolset
from app.services.query.deps import Deps

log = logging.getLogger(__name__)


@dataclass
class AgentRuntime:
    cfg: dict[str, Any]
    manager: SandboxManager
    toolset: ToolSet                 # tool nội bộ (luôn có)
    agent: Any                       # vòng lặp agent (checkpointer kế thừa từ assistant graph)
    model: BaseChatModel | None = None
    skills: dict[str, Skill] | None = None
    mcp: Any = None                  # McpHub | None – connector khai qua giao diện
    sources: SourceCatalog | None = None

    def turn_sources(self, user_id: str) -> list[dict[str, str]]:
        """Danh mục nguồn ngoài của user đầu lượt (chỉ đọc DB/config)."""
        return self.sources.entries(user_id) if self.sources is not None else []


def build_runtime(deps: Deps, *, cfg: dict[str, Any] | None = None, manager: SandboxManager | None = None,
                  model: BaseChatModel | None = None, extra_tools: list[BaseTool] | None = None,
                  yaml_tools: list[BaseTool] | None = None, mcp: Any = None) -> AgentRuntime:
    """Tham số None → theo config/agent.yaml, config/mcp.yaml. Unit test tiêm model/manager/extra_tools/yaml_tools/mcp.

    extra_tools: tool luôn có thêm (test). yaml_tools: tool server config/mcp.yaml (None → nạp theo config).
    Không tiêm gì (chạy thật) → dựng McpHub mặc định (Postgres) cho connector khai qua giao diện.
    """
    c = cfg or agent_cfg()
    m = manager or build_manager(c["sandbox"])
    skills = load_skills(c["skills_dir"])
    if mcp is None and extra_tools is None and yaml_tools is None:
        mcp = _default_hub(deps)
    catalog = SourceCatalog(load_mcp_tools(mcp_cfg()) if yaml_tools is None else yaml_tools, mcp_cfg(), mcp)
    toolset = build_toolset(deps, m, c, skills, extra_tools, catalog)
    llm = model or get_agent_model(c["agent"], deps.settings)
    return AgentRuntime(c, m, toolset, build_agent(llm, toolset, skills, c, m, catalog=catalog), llm, skills, mcp,
                        catalog)


def _default_hub(deps: Deps):
    try:
        from app.services.agent.mcp.hub import McpHub

        return McpHub.default(deps.settings, getattr(deps.repo, "pool", None), llm=deps.llm_fast)
    except Exception as e:  # noqa: BLE001 – thiếu DB: chỉ mất connector, agent vẫn chạy
        log.warning("Không khởi tạo được MCP connector: %s", e)
        return None
