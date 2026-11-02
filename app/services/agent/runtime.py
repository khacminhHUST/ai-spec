"""Khởi tạo các thành phần dùng chung của Agent Harness (sandbox, skills, tools, model, vòng lặp agent)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.services.agent.config import agent_cfg, mcp_cfg
from app.services.agent.loop.agent import build_agent, get_agent_model
from app.services.agent.mcp import load_mcp_tools
from app.services.agent.sandbox import SandboxManager, build_manager
from app.services.agent.skills import load_skills
from app.services.agent.tools.registry import ToolSet, build_toolset
from app.services.query.deps import Deps


@dataclass
class AgentRuntime:
    cfg: dict[str, Any]
    manager: SandboxManager
    toolset: ToolSet
    agent: Any      # graph vòng lặp agent (checkpointer kế thừa từ assistant graph)


def build_runtime(deps: Deps, *, cfg: dict[str, Any] | None = None, manager: SandboxManager | None = None,
                  model: BaseChatModel | None = None, mcp_tools: list[BaseTool] | None = None) -> AgentRuntime:
    """Tham số None → theo config/agent.yaml, config/mcp.yaml. Unit test tiêm model/manager/mcp_tools giả."""
    c = cfg or agent_cfg()
    m = manager or build_manager(c["sandbox"])
    skills = load_skills(c["skills_dir"])
    tools = load_mcp_tools(mcp_cfg()) if mcp_tools is None else mcp_tools
    toolset = build_toolset(deps, m, c, skills, tools)
    llm = model or get_agent_model(c["agent"], deps.settings)
    return AgentRuntime(c, m, toolset, build_agent(llm, toolset, skills, c, m))
