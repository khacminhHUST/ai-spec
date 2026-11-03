"""Khởi tạo các thành phần dùng chung của Agent Harness (sandbox, skills, tools, model) + dựng vòng lặp agent theo lượt.

Bộ tool của 1 lượt = tool có sẵn + tool config/mcp.yaml (nạp 1 lần lúc khởi động) + tool connector (DB) mà user hiện
tại dùng được (McpHub.turn_tools). Không có tool connector → dùng lại agent dựng sẵn.
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
from app.services.agent.sandbox import SandboxManager, build_manager
from app.services.agent.skills import Skill, load_skills
from app.services.agent.state import AgentContext
from app.services.agent.tools.registry import ToolSet, build_toolset
from app.services.query.deps import Deps

log = logging.getLogger(__name__)
EXTERNAL_DESC_CHARS = 100        # mô tả tool rút gọn cho Gate


@dataclass
class AgentRuntime:
    cfg: dict[str, Any]
    manager: SandboxManager
    toolset: ToolSet
    agent: Any                       # vòng lặp agent với bộ tool gốc (checkpointer kế thừa từ assistant graph)
    model: BaseChatModel | None = None
    skills: dict[str, Skill] | None = None
    mcp: Any = None                  # McpHub | None – connector khai qua giao diện

    def turn_toolset(self, ctx: AgentContext) -> ToolSet:
        extra = self.mcp.turn_tools(ctx.user_id) if self.mcp is not None else []
        return self.toolset.extended(extra) if extra else self.toolset

    def external_systems(self, user_id: str) -> list[dict[str, Any]]:
        """Hệ thống ngoài (MCP: config/mcp.yaml + connector) user đang dùng được → cho Gate nhận biết câu hỏi cần agent.

        [{name, label, tools: [{name, description}]}]. Dùng chung cache tool với lượt agent.
        """
        systems: dict[str, dict[str, Any]] = {}
        for t in self.turn_toolset(AgentContext(user_id, "")).tools:
            meta = t.metadata or {}
            server = meta.get("mcp_server")
            if not server:
                continue
            s = systems.setdefault(server, {"name": server, "label": meta.get("connector_label") or "", "tools": []})
            desc = " ".join((t.description or "").split())
            s["tools"].append({"name": meta.get("mcp_tool") or t.name, "description": desc[:EXTERNAL_DESC_CHARS]})
        return list(systems.values())

    def agent_for(self, ctx: AgentContext) -> Any:
        """Agent của lượt (gọi lại khi resume sau /chat/confirm → bộ tool tại thời điểm đó)."""
        toolset = self.turn_toolset(ctx)
        if toolset is self.toolset:
            return self.agent
        return build_agent(self.model, toolset, self.skills or {}, self.cfg, self.manager)


def build_runtime(deps: Deps, *, cfg: dict[str, Any] | None = None, manager: SandboxManager | None = None,
                  model: BaseChatModel | None = None, mcp_tools: list[BaseTool] | None = None,
                  mcp: Any = None) -> AgentRuntime:
    """Tham số None → theo config/agent.yaml, config/mcp.yaml. Unit test tiêm model/manager/mcp_tools/mcp giả.

    mcp (McpHub) None và không tiêm mcp_tools → dựng McpHub mặc định (Postgres) cho connector khai qua giao diện.
    """
    c = cfg or agent_cfg()
    m = manager or build_manager(c["sandbox"])
    skills = load_skills(c["skills_dir"])
    tools = load_mcp_tools(mcp_cfg()) if mcp_tools is None else mcp_tools
    if mcp is None and mcp_tools is None:
        mcp = _default_hub(deps)
    toolset = build_toolset(deps, m, c, skills, tools)
    llm = model or get_agent_model(c["agent"], deps.settings)
    return AgentRuntime(c, m, toolset, build_agent(llm, toolset, skills, c, m), llm, skills, mcp)


def _default_hub(deps: Deps):
    try:
        from app.services.agent.mcp.hub import McpHub

        return McpHub.default(deps.settings, getattr(deps.repo, "pool", None))
    except Exception as e:  # noqa: BLE001 – thiếu DB: chỉ mất connector, agent vẫn chạy
        log.warning("Không khởi tạo được MCP connector: %s", e)
        return None
