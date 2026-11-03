"""Fake dùng chung cho test agent: gọi tool với ToolRuntime giả, sandbox mock."""
from __future__ import annotations

import json
import uuid
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.services.agent.state import AgentContext

CTX = AgentContext(user_id="test_engineer", conversation_id="conv-1")


def runtime(ctx: AgentContext = CTX, state: dict | None = None) -> ToolRuntime:
    return ToolRuntime(state=state or {}, context=ctx, config={}, stream_writer=lambda _: None,
                       tool_call_id="call-1", store=None)


def call(tool, ctx: AgentContext = CTX, **args: Any) -> dict:
    """Gọi tool như ToolNode (runtime được inject) → dict JSON kết quả."""
    return json.loads(tool.invoke({**args, "runtime": runtime(ctx)}))


class ScriptedModel(BaseChatModel):
    """LLM giả cho agent: trả lần lượt `script` (AIMessage | str | callable(messages) → AIMessage).

    Hết script → lặp phần tử cuối. Lời gọi tóm tắt hội thoại (SummarizationMiddleware) trả `summary`, không tiêu script.
    `bound`: tên tool gửi kèm mỗi lần bind_tools (tool của nguồn ngoài chỉ có sau khi mở nguồn).
    """

    script: list = []
    summary: str = "TÓM TẮT"
    calls: list = []
    bound: list = []
    summary_calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.bound.append([getattr(t, "name", None) or t.get("name") for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if any("Messages to summarize" in str(m.content) for m in messages):
            self.summary_calls += 1
            return ChatResult(generations=[ChatGeneration(message=AIMessage(self.summary))])
        self.calls.append(messages)
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if callable(item):
            item = item(messages)
        msg = AIMessage(item) if isinstance(item, str) else item.model_copy()
        return ChatResult(generations=[ChatGeneration(message=msg)])


def tool_call(tool_name: str, call_id: str | None = None, **args: Any) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": tool_name, "args": args,
                                      "id": call_id or f"call_{tool_name}_{uuid.uuid4().hex[:6]}"}])


def call_cmd(tool, ctx: AgentContext | None = None, state: dict | None = None, **args: Any):
    """Tool trả Command (VD search_project_docs) → (dict JSON của ToolMessage, Command.update)."""
    ctx = ctx or AgentContext(user_id=CTX.user_id, conversation_id=CTX.conversation_id)
    cmd = tool.invoke({**args, "runtime": runtime(ctx, state)})
    return json.loads(cmd.update["messages"][0].content), cmd.update
