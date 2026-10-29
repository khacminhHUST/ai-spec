"""Fake dùng chung cho test agent: gọi tool với ToolRuntime giả, sandbox mock."""
from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime

from app.services.agent.state import AgentContext

CTX = AgentContext(user_id="test_engineer", conversation_id="conv-1")


def runtime(ctx: AgentContext = CTX, state: dict | None = None) -> ToolRuntime:
    return ToolRuntime(state=state or {}, context=ctx, config={}, stream_writer=lambda _: None,
                       tool_call_id="call-1", store=None)


def call(tool, ctx: AgentContext = CTX, **args: Any) -> dict:
    """Gọi tool như ToolNode (runtime được inject) → dict JSON kết quả."""
    return json.loads(tool.invoke({**args, "runtime": runtime(ctx)}))


def call_cmd(tool, ctx: AgentContext = CTX, state: dict | None = None, **args: Any):
    """Tool trả Command (VD search_project_docs) → (dict JSON của ToolMessage, Command.update)."""
    cmd = tool.invoke({**args, "runtime": runtime(ctx, state)})
    return json.loads(cmd.update["messages"][0].content), cmd.update
